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
from typing import Protocol

import httpx
from pydantic import BaseModel, Field

from smallworks.config import Deployment, FactoryPolicy, WorkerConfig
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


def provider_for(deployment_name: str) -> str:
    """Provider prefix before '/', e.g. 'ollama/qwen' -> 'ollama'."""
    return deployment_name.split("/", 1)[0] if "/" in deployment_name else "unknown"


def model_id_for(deployment_name: str) -> str:
    """Model id after the provider prefix."""
    return deployment_name.split("/", 1)[1] if "/" in deployment_name else deployment_name


class OpenAICompatibleTransport:
    """POST ``{base}/chat/completions``; works for Ollama, vLLM, LiteLLM proxy."""

    def __init__(self, base_urls: dict[str, str] | None = None, timeout_s: float = 120.0) -> None:
        self.base_urls = base_urls or {
            "ollama": os.environ.get("OLLAMA_BASE_URL", "http://localhost:11434/v1"),
            "vllm": os.environ.get("VLLM_BASE_URL", "http://localhost:8001/v1"),
            "external": os.environ.get("LITELLM_PROXY_URL", "http://localhost:4000/v1"),
        }
        self.timeout_s = timeout_s

    def complete(self, deployment: Deployment, prompt: str, *, task_id: str) -> TransportResult:
        provider = provider_for(deployment.name)
        base = self.base_urls.get(provider)
        log = logger.bind(
            component="gateway", task_id=task_id, deployment=deployment.name, provider=provider
        )
        if base is None:
            raise TransportError(f"no endpoint configured for provider {provider!r}")
        url = base.rstrip("/") + "/chat/completions"
        log.debug("POST {} model={} prompt_chars={}", url, model_id_for(deployment.name), len(prompt))
        try:
            resp = httpx.post(
                url,
                json={
                    "model": model_id_for(deployment.name),
                    "messages": [{"role": "user", "content": prompt}],
                    "stream": False,
                },
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
    ) -> None:
        self.models = models
        self.workers = workers
        self.policy = policy
        self.transport: Transport = transport or OpenAICompatibleTransport()
        self._slots = {role: threading.Semaphore(cfg.max_concurrent) for role, cfg in workers.items()}

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
            log.debug("slot acquired, deployments={}", [d.name for d in deployments])
            return self._complete_locked(log, role, group, deployments, prompt, task_id=task_id)

    def _complete_locked(
        self, log, role: str, group: str, deployments: list[Deployment], prompt: str, *, task_id: str
    ) -> GatewayCompletion:
        started = time.monotonic()
        limit = min(len(deployments), self.policy.max_retries + 1)
        last_err: Exception | None = None
        for attempt, deployment in enumerate(deployments[:limit], start=1):
            attempt_log = log.bind(
                attempt=attempt, deployment=deployment.name, class_=deployment.model_class
            )
            attempt_log.debug("trying deployment ({}/{})", attempt, limit)
            t0 = time.monotonic()
            try:
                result = self.transport.complete(deployment, prompt, task_id=task_id)
            except TransportError as exc:
                last_err = exc
                attempt_log.warning("deployment failed, falling back: {}", exc)
                continue
            except Exception as exc:  # defensive: a transport bug must not hang the factory
                last_err = exc
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
            attempt_log.info(
                "success via {} tokens={}/{} latency_ms={}",
                deployment.name,
                result.input_tokens,
                result.output_tokens,
                latency_ms,
            )
            return GatewayCompletion(
                text=result.text,
                role=role,
                group=group,
                deployment=deployment.name,
                deployment_class=deployment.model_class,
                provider=provider_for(deployment.name),
                input_tokens=result.input_tokens,
                output_tokens=result.output_tokens,
                cost=result.cost,
                latency_ms=latency_ms,
                attempts=attempt,
            )
        log.error("all {} deployments failed, escalating", limit)
        raise GatewayExhausted(f"group {group!r}: {limit} attempts failed (last: {last_err})")
