"""Config loaders: parse and validate models/workers YAMLs (plan 01, spec §4-§5)."""

from __future__ import annotations

from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, Field, model_validator

DeploymentClass = Literal["frontier", "self-hosted"]
Tier = Literal["small", "medium", "large"]

KNOWN_PROVIDERS: tuple[str, ...] = ("ollama", "vllm", "litellm", "github", "openai", "openrouter")
PROVIDER_DEFAULT_BASES: dict[str, str] = {
    "ollama": "http://localhost:11434/v1",
    "vllm": "http://localhost:8001/v1",
    "litellm": "http://localhost:4000/v1",
    "github": "https://models.github.ai/inference",
    "openai": "https://api.openai.com/v1",
    "openrouter": "https://openrouter.ai/api/v1",
}
# Providers that need an API key unless talking to a local override.
# Keys are resolved env-first (or .env), never stored in YAML.
PROVIDERS_REQUIRING_KEY: frozenset[str] = frozenset({"litellm", "github", "openai", "openrouter"})


class ProviderConfig(BaseModel):
    """One vendor endpoint: base URL + env var holding its API key."""

    base_url: str = Field(min_length=1)
    api_key_env: str = Field(default="", min_length=0)
    default_model: str = Field(default="", min_length=0)


DEFAULT_API_KEY_ENVS: dict[str, str] = {
    "ollama": "",
    "vllm": "",
    "litellm": "LITELLM_API_KEY",
    "github": "GITHUB_TOKEN",
    "openai": "OPENAI_API_KEY",
    "openrouter": "OPENROUTER_API_KEY",
}


def default_providers() -> dict[str, ProviderConfig]:
    """Seed every known provider with its default base URL (no keys in YAML)."""
    return {
        name: ProviderConfig(base_url=base, api_key_env=DEFAULT_API_KEY_ENVS.get(name, ""))
        for name, base in PROVIDER_DEFAULT_BASES.items()
    }


class Deployment(BaseModel):
    """One routable model: provider + endpoint + model + per-call params.

    ``name`` stays as a legacy shorthand (``"ollama/qwen2.5-coder-8b"``):
    provider = prefix, model = remainder. Explicit fields win when both given.
    """

    name: str = Field(default="", min_length=0)
    provider: str = Field(default="", min_length=0)
    endpoint: str = Field(default="", min_length=0)
    model: str = Field(default="", min_length=0)
    params: dict = Field(default_factory=dict)
    model_class: DeploymentClass = Field(default="self-hosted", alias="class")
    model_config = {"populate_by_name": True}
    @model_validator(mode="after")
    def _split_legacy_name(self):
        """Fill provider/model from ``name`` when explicit fields are absent."""
        if not self.name:
            return self
        provider, _, model = self.name.partition("/")
        if not self.provider:
            self.provider = provider or "unknown"
        if not self.model and model:
            self.model = model
        return self

    @model_validator(mode="after")
    def _require_provider_and_model(self):
        if not self.provider:
            raise ValueError("deployment needs 'provider' (or legacy 'name' with a prefix)")
        if not self.model:
            raise ValueError("deployment needs 'model' (or legacy 'name' with 'provider/model')")
        return self

    @property
    def display_name(self) -> str:
        """Canonical ``provider/model`` label used in logs and gateway records."""
        return f"{self.provider}/{self.model}"


class WorkerConfig(BaseModel):
    model_group: str = Field(min_length=1)
    tier: Tier
    max_concurrent: int = Field(ge=1)
    enabled: bool = True


class LoadedConfig(BaseModel):
    models: dict[str, list[Deployment]]
    workers: dict[str, WorkerConfig]
    providers: dict[str, ProviderConfig] = Field(default_factory=default_providers)
    model_config = {"arbitrary_types_allowed": True}

    def role_mapping(self) -> dict[str, str]:
        """Role -> model group for enabled workers only."""
        return {r: w.model_group for r, w in self.workers.items() if w.enabled}


def _read_yaml(path: Path) -> dict:
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise ValueError(f"config file not found: {path}") from None
    if not isinstance(data, dict):
        raise ValueError(f"config {path} must be a mapping at top level")
    return data


def load_models(path: Path) -> dict[str, list[Deployment]]:
    data = _read_yaml(path)
    raw = data.get("models")
    if not isinstance(raw, dict) or not raw:
        raise ValueError(f"config {path} needs a non-empty 'models' mapping")
    models: dict[str, list[Deployment]] = {}
    for group, deployments in raw.items():
        if not isinstance(deployments, list) or not deployments:
            raise ValueError(f"model group {group!r} needs a non-empty deployment list")
        models[group] = [Deployment.model_validate(d) for d in deployments]
    return models


def load_workers(path: Path, *, known_groups: set[str]) -> dict[str, WorkerConfig]:
    data = _read_yaml(path)
    raw = data.get("workers")
    if not isinstance(raw, dict) or not raw:
        raise ValueError(f"config {path} needs a non-empty 'workers' mapping")
    workers: dict[str, WorkerConfig] = {}
    for role, cfg in raw.items():
        if not isinstance(cfg, dict):
            raise ValueError(f"worker {role!r} must be a mapping")
        worker = WorkerConfig.model_validate(cfg)
        if worker.model_group not in known_groups:
            raise ValueError(f"worker {role!r} references unknown model group {worker.model_group!r}")
        workers[role] = worker
    return workers


def load_providers(path: Path) -> dict[str, ProviderConfig]:
    """Load ``providers.yaml``; missing file = defaults for every known provider."""
    try:
        data = _read_yaml(path)
    except ValueError as exc:
        if "not found" in str(exc):
            return default_providers()
        raise
    raw = data.get("providers")
    if raw is None:
        return default_providers()
    if not isinstance(raw, dict) or not raw:
        raise ValueError(f"config {path} needs a non-empty 'providers' mapping")
    providers: dict[str, ProviderConfig] = {}
    for name, cfg in raw.items():
        if not isinstance(cfg, dict):
            raise ValueError(f"provider {name!r} must be a mapping")
        providers[name] = ProviderConfig.model_validate(cfg)
    return providers


def load_configs(
    models_path: Path, workers_path: Path, providers_path: Path | None = None
) -> LoadedConfig:
    """Load models + workers (+ providers); fail fast on unknown group/tier/provider.

    ``providers.yaml`` holds explicit slots only (one local slot ships); every
    other known vendor resolves from built-in defaults until the user adds it
    via the settings page. Truly unknown provider names still fail fast.
    """
    models = load_models(models_path)
    workers = load_workers(workers_path, known_groups=set(models))
    explicit = (
        load_providers(providers_path) if providers_path is not None else default_providers()
    )
    providers = default_providers()
    providers.update(explicit)
    for group, deployments in models.items():
        for dep in deployments:
            if dep.provider not in providers:
                raise ValueError(
                    f"model group {group!r} deployment {dep.display_name!r} "
                    f"references unknown provider {dep.provider!r}"
                )
    return LoadedConfig(models=models, workers=workers, providers=providers)


def default_config_dir() -> Path:
    return Path(__file__).resolve().parents[2] / "configs"


class EscalationPolicy(BaseModel):
    max_retries: int = Field(ge=1)
    high_complexity_goes_frontier: bool = True


class FactoryBudgets(BaseModel):
    max_cost_per_task: float = Field(gt=0)
    max_wallclock_minutes: float = Field(gt=0)


class FactoryPolicy(BaseModel):
    """Supervised-autonomy policy (spec §10): escalation + approvals + budgets."""

    mode: str = Field(min_length=1)
    max_retries: int = Field(ge=1)
    approvals_required: list[str] = Field(min_length=1)
    max_cost_per_task: float = Field(gt=0)
    max_wallclock_minutes: float = Field(gt=0)


def load_factory(path: Path) -> FactoryPolicy:
    """Load supervised-autonomy policy; fail fast on missing budgets/escalation."""
    from smallworks.logging import logger

    data = _read_yaml(path)
    raw = data.get("factory")
    if not isinstance(raw, dict):
        raise ValueError(f"config {path} needs a 'factory' mapping")
    esc_raw = raw.get("escalation")
    if not isinstance(esc_raw, dict):
        raise ValueError(f"config {path} needs a 'factory.escalation' mapping")
    bud_raw = raw.get("budgets")
    if not isinstance(bud_raw, dict) or not bud_raw:
        raise ValueError(f"config {path} needs a non-empty 'factory.budgets' mapping")
    escalation = EscalationPolicy.model_validate(esc_raw)
    budgets = FactoryBudgets.model_validate(bud_raw)
    approvals = raw.get("approvals_required")
    if not isinstance(approvals, list) or not approvals:
        raise ValueError(f"config {path} needs a non-empty 'factory.approvals_required' list")
    policy = FactoryPolicy(
        mode=str(raw.get("mode", "supervised")),
        max_retries=escalation.max_retries,
        approvals_required=[str(a) for a in approvals],
        max_cost_per_task=budgets.max_cost_per_task,
        max_wallclock_minutes=budgets.max_wallclock_minutes,
    )
    logger.bind(component="config", factory=str(path)).debug(
        "factory policy loaded mode={} max_retries={} approvals={} "
        "max_cost={} max_minutes={}",
        policy.mode,
        policy.max_retries,
        policy.approvals_required,
        policy.max_cost_per_task,
        policy.max_wallclock_minutes,
    )
    return policy
