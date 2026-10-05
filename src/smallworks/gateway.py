"""Model gateway: route roles through logical groups (plan 02, spec §4-§5).

Order inside each group is preference: frontier-first for decisions,
self-hosted-first for execution. Retries fall back to the next deployment,
capped by ``factory.max_retries``. Per-role ``max_concurrent`` bounds act as
resource-pool semaphores. Factory budgets breach as ``BudgetExceeded``
(pause + human approval). Every success returns a ``GatewayCompletion``
recording model/provider/class/tokens/cost for later ``RunReport``.

Transports share one interface: ``OpenAICompatibleTransport`` serves
Ollama/vLLM directly and frontier via a LiteLLM proxy URL (all
OpenAI-compatible, no extra deps). Tests inject script transports.
"""

from __future__ import annotations

import os
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol

import httpx
from pydantic import BaseModel, Field

from smallworks.config import Deployment, FactoryPolicy, ProviderConfig, WorkerConfig
from smallworks.logging import logger


class TransportError(RuntimeError):
    """One deployment attempt failed; gateway falls back to the next."""


class GatewayExhausted(RuntimeError):
    """All deployments in the group failed within the retry budget."""


class BudgetExceeded(RuntimeError):
    """Factory budget breached: run pauses, human approval required."""


class UnknownRole(ValueError):
    pass


class TransportResult(BaseModel):
    text: str = ""
    input_tokens: int = Field(ge=0, default=0)
    output_tokens: int = Field(ge=0, default=0)
    cost: float = Field(ge=0.0, default=0.0)


class GatewayCompletion(BaseModel):
    text: str
    role: str
    group: str
    deployment: str
    deployment_class: str
    provider: str
    input_tokens: int = Field(ge=0)
    output_tokens: int = Field(ge=0)
    cost: float = Field(ge=0.0)
    latency_ms: int = Field(ge=0)
    attempts: int = Field(ge=1)


class Transport(Protocol):
    def complete(self, deployment: Deployment, prompt: str, *, task_id: str) -> TransportResult: ...


def provider_for(deployment: Deployment | str) -> str:
    """Provider key for a deployment (or a legacy ``"provider/model"`` name)."""
    if isinstance(deployment, str):
        return deployment.split("/", 1)[0] if "/" in deployment else "unknown"
    return deployment.provider or "unknown"


def model_id_for(deployment: Deployment | str) -> str:
    """Model id for a deployment (or the remainder of a legacy name)."""
    if isinstance(deployment, str):
        return deployment.split("/", 1)[1] if "/" in deployment else deployment
    return deployment.model


def load_dotenv(path: Path | None = None) -> dict[str, str]:
    """Load ``KEY=VALUE`` pairs from .env (no override of the live environment).

    Returns what was read; never raises — a missing/unreadable file is normal
    (first run, container without a mounted .env).
    """
    env_path = path or Path.cwd() / ".env"
    loaded: dict[str, str] = {}
    try:
        text = env_path.read_text(encoding="utf-8")
    except OSError:
        return loaded
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip("'").strip('"')
        if key and key not in loaded:
            loaded[key] = value
    return loaded


@dataclass
class ResolvedEndpoint:
    base_url: str
    model: str
    headers: dict[str, str]
    params: dict


def resolve_endpoint(
    deployment: Deployment,
    providers: dict[str, ProviderConfig] | None = None,
    *,
    env: dict[str, str] | None = None,
    dotenv_path: Path | None = None,
) -> ResolvedEndpoint:
    """Resolve where/how to call a deployment: base URL + model + auth + params.

    Precedence for the base URL: deployment.endpoint > ``<PROVIDER>_BASE_URL``
    env (or .env) > providers.yaml > built-in default. The API key comes from
    the provider's ``api_key_env`` var (env first, then .env); empty means the
    endpoint needs no key (local Ollama/vLLM). Deployment ``params`` ride along
    as extra JSON fields on the chat request.
    """
    from smallworks.config import PROVIDER_DEFAULT_BASES, default_providers

    provider = deployment.provider or "unknown"
    cfg = (providers or {}).get(provider)
    environ = env if env is not None else os.environ
    file_env = load_dotenv(dotenv_path)

    def lookup(name: str) -> str:
        if name in environ:
            return str(environ[name])
        return file_env.get(name, "")

    base = deployment.endpoint or ""
    if not base:
        base = lookup(f"{provider.upper()}_BASE_URL")
    if not base and cfg is not None:
        base = cfg.base_url
    if not base:
        base = PROVIDER_DEFAULT_BASES.get(provider, "")
    if not base:
        raise TransportError(f"no endpoint configured for provider {provider!r}")
    model = deployment.model or (cfg.default_model if cfg else "")
    if not model:
        raise TransportError(f"no model configured for provider {provider!r}")
    headers: dict[str, str] = {}
    key_env = cfg.api_key_env if cfg and cfg.api_key_env else ""
    if key_env:
        key = lookup(key_env)
        if key:
            headers["Authorization"] = f"Bearer {key}"
    return ResolvedEndpoint(base_url=base, model=model, headers=headers, params=dict(deployment.params))


class OpenAICompatibleTransport:
    """POST ``{base}/chat/completions``; works for Ollama, vLLM, LiteLLM, GitHub, OpenAI."""

    def __init__(
        self,
        base_urls: dict[str, str] | None = None,
        timeout_s: float = 120.0,
        providers: dict[str, ProviderConfig] | None = None,
        dotenv_path: Path | None = None,
    ) -> None:
        # Legacy per-provider base overrides (kept for tests/callers); the
        # resolver also honors <PROVIDER>_BASE_URL env, .env, and providers.yaml.
        self.base_urls = base_urls or {}
        self.timeout_s = timeout_s
        self.providers = providers
        self.dotenv_path = dotenv_path

    def complete(self, deployment: Deployment, prompt: str, *, task_id: str) -> TransportResult:
        provider = provider_for(deployment)
        overrides = dict(self.base_urls) if self.base_urls else None
        if overrides and provider in overrides:
            resolved = ResolvedEndpoint(
                base_url=overrides[provider],
                model=model_id_for(deployment),
                headers={},
                params=dict(deployment.params),
            )
        else:
            try:
                resolved = resolve_endpoint(
                    deployment, self.providers, dotenv_path=self.dotenv_path
                )
            except TransportError:
                if overrides:
                    raise TransportError(f"no endpoint configured for provider {provider!r}")
                from smallworks.config import default_providers

                resolved = resolve_endpoint(
                    deployment, default_providers(), dotenv_path=self.dotenv_path
                )
        log = logger.bind(
            component="gateway", task_id=task_id, deployment=deployment.display_name, provider=provider
        )
        url = resolved.base_url.rstrip("/") + "/chat/completions"
        log.debug("POST {} model={} prompt_chars={}", url, resolved.model, len(prompt))
        payload: dict = {
            "model": resolved.model,
            "messages": [{"role": "user", "content": prompt}],
            "stream": False,
        }
        payload.update(resolved.params)
        try:
            resp = httpx.post(
                url,
                json=payload,
                headers=resolved.headers or None,
                timeout=self.timeout_s,
            )
        except httpx.HTTPError as exc:
            raise TransportError(f"{provider} request failed: {exc}") from exc
        if resp.status_code != 200:
            raise TransportError(f"{provider} HTTP {resp.status_code}: {resp.text[:300]}")
        try:
            body = resp.json()
            text = body["choices"][0]["message"]["content"] or ""
        except (ValueError, KeyError, IndexError, TypeError) as exc:
            raise TransportError(f"{provider} bad response envelope: {exc}") from exc
        usage = body.get("usage") or {}
        result = TransportResult(
            text=text,
            input_tokens=int(usage.get("prompt_tokens", 0) or 0),
            output_tokens=int(usage.get("completion_tokens", 0) or 0),
            cost=0.0,  # self-hosted + proxy: cost attributed by later accounting, not the wire
        )
        log.debug(
            "response chars={} in_tokens={} out_tokens={}",
            len(text),
            result.input_tokens,
            result.output_tokens,
        )
        return result


class Gateway:
    """Routes role prompts through group deployments with fallback + budgets."""

    def __init__(
        self,
        models: dict[str, list[Deployment]],
        workers: dict[str, WorkerConfig],
        policy: FactoryPolicy,
        transport: Transport | None = None,
        groups: dict | None = None,
    ) -> None:
        self.models = models
        self.workers = workers
        self.policy = policy
        self.transport: Transport = transport or OpenAICompatibleTransport()
        self.groups = dict(groups or {})
        self._slots = {role: threading.Semaphore(cfg.max_concurrent) for role, cfg in workers.items()}
        self._pool_cursor: dict[str, int] = {}
        self._pool_down_until: dict[tuple[str, str], float] = {}
        self._pool_lock = threading.Lock()

    def deployments_for_role(self, role: str) -> tuple[str, list[Deployment]]:
        worker = self.workers.get(role)
        if worker is None:
            raise UnknownRole(f"unknown role {role!r}")
        deployments = self.models.get(worker.model_group)
        if not deployments:
            raise UnknownRole(f"role {role!r} maps to empty group {worker.model_group!r}")
        return worker.model_group, deployments

    def complete(self, role: str, prompt: str, *, task_id: str = "TASK-0") -> GatewayCompletion:
        group, deployments = self.deployments_for_role(role)
        slot = self._slots[role]
        log = logger.bind(component="gateway", task_id=task_id, role=role, group=group)
        log.debug("acquiring slot max_concurrent={}", self.workers[role].max_concurrent)
        with slot:
            log.debug("slot acquired, deployments={}", [d.display_name for d in deployments])
            return self._complete_locked(log, role, group, deployments, prompt, task_id=task_id)

    def _order_for_group(self, group: str, deployments: list[Deployment]) -> list[Deployment]:
        """Attempt order: fallback = listed; pool = round-robin from cursor, down members last."""
        policy = self.groups.get(group)
        strategy = policy.strategy if policy is not None else "fallback"
        if strategy != "pool":
            return list(deployments)
        now = time.monotonic()
        cooldown = policy.pool_cooldown_s if policy is not None else 60.0
        with self._pool_lock:
            start = self._pool_cursor.get(group, 0) % max(1, len(deployments))
            rotated = deployments[start:] + deployments[:start]
            live = [d for d in rotated if self._pool_down_until.get((group, d.display_name), 0.0) <= now]
            down = [d for d in rotated if self._pool_down_until.get((group, d.display_name), 0.0) > now]
            return live + down if live else list(deployments)

    def _note_pool_down(self, group: str, deployment: Deployment, cooldown_s: float) -> None:
        with self._pool_lock:
            self._pool_down_until[(group, deployment.display_name)] = time.monotonic() + cooldown_s

    def _advance_pool_cursor(
        self, group: str, used: Deployment, deployments: list[Deployment], ordered: list[Deployment]
    ) -> None:
        """Cursor in base-list coordinates: dead members can't shift the rotation."""
        with self._pool_lock:
            if used not in ordered:
                return
            try:
                idx = list(deployments).index(used)
            except ValueError:
                return
            self._pool_cursor[group] = idx + 1

    def _complete_locked(
        self, log, role: str, group: str, deployments: list[Deployment], prompt: str, *, task_id: str
    ) -> GatewayCompletion:
        from smallworks.config import ModelGroup

        started = time.monotonic()
        policy = self.groups.get(group)
        strategy = policy.strategy if isinstance(policy, ModelGroup) else "fallback"
        cooldown = policy.pool_cooldown_s if isinstance(policy, ModelGroup) else 60.0
        ordered = self._order_for_group(group, deployments)
        limit = min(len(ordered), self.policy.max_retries + 1)
        last_err: Exception | None = None
        for attempt, deployment in enumerate(ordered[:limit], start=1):
            attempt_log = log.bind(
                attempt=attempt, deployment=deployment.display_name, class_=deployment.model_class
            )
            attempt_log.debug("trying deployment ({}/{})", attempt, limit)
            t0 = time.monotonic()
            try:
                result = self.transport.complete(deployment, prompt, task_id=task_id)
            except TransportError as exc:
                last_err = exc
                if strategy == "pool":
                    self._note_pool_down(group, deployment, cooldown)
                attempt_log.warning("deployment failed, falling back: {}", exc)
                continue
            except Exception as exc:  # defensive: a transport bug must not hang the factory
                last_err = exc
                if strategy == "pool":
                    self._note_pool_down(group, deployment, cooldown)
                attempt_log.opt(exception=True).warning("transport raised, falling back")
                continue
            latency_ms = int((time.monotonic() - t0) * 1000)
            if result.cost > self.policy.max_cost_per_task:
                attempt_log.error(
                    "budget breach cost={} > max={}: pausing for human",
                    result.cost,
                    self.policy.max_cost_per_task,
                )
                raise BudgetExceeded(
                    f"cost {result.cost} exceeds max_cost_per_task {self.policy.max_cost_per_task}"
                )
            elapsed_min = (time.monotonic() - started) / 60
            if elapsed_min > self.policy.max_wallclock_minutes:
                attempt_log.error(
                    "budget breach wallclock_min={:.2f} > max={}: pausing for human",
                    elapsed_min,
                    self.policy.max_wallclock_minutes,
                )
                raise BudgetExceeded(
                    f"wallclock {elapsed_min:.2f}min exceeds max {self.policy.max_wallclock_minutes}min"
                )
            if strategy == "pool":
                self._advance_pool_cursor(group, deployment, deployments, ordered)
            attempt_log.info(
                "success via {} tokens={}/{} latency_ms={}",
                deployment.display_name,
                result.input_tokens,
                result.output_tokens,
                latency_ms,
            )
            return GatewayCompletion(
                text=result.text,
                role=role,
                group=group,
                deployment=deployment.display_name,
                deployment_class=deployment.model_class,
                provider=provider_for(deployment),
                input_tokens=result.input_tokens,
                output_tokens=result.output_tokens,
                cost=result.cost,
                latency_ms=latency_ms,
                attempts=attempt,
            )
        log.error("all {} deployments failed, escalating", limit)
        raise GatewayExhausted(f"group {group!r}: {limit} attempts failed (last: {last_err})")
