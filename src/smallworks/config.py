"""Config loaders: parse and validate models/workers YAMLs (plan 01, spec §4-§5)."""

from __future__ import annotations

from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, Field

DeploymentClass = Literal["frontier", "self-hosted"]
Tier = Literal["small", "medium", "large"]

VALID_CLASSES: set[str] = {"frontier", "self-hosted"}
VALID_TIERS: set[str] = {"small", "medium", "large"}


class Deployment(BaseModel):
    name: str = Field(min_length=1)
    model_class: DeploymentClass = Field(alias="class")

    model_config = {"populate_by_name": True}


class WorkerConfig(BaseModel):
    model_group: str = Field(min_length=1)
    tier: Tier
    max_concurrent: int = Field(ge=1)
    enabled: bool = True


class LoadedConfig(BaseModel):
    models: dict[str, list[Deployment]]
    workers: dict[str, WorkerConfig]
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


def load_configs(models_path: Path, workers_path: Path) -> LoadedConfig:
    """Load both YAMLs; fail fast on unknown group/tier."""
    models = load_models(models_path)
    workers = load_workers(workers_path, known_groups=set(models))
    return LoadedConfig(models=models, workers=workers)


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
