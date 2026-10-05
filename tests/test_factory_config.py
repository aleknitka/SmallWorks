from pathlib import Path

import yaml

REPO = Path(__file__).resolve().parents[1]
VALID_CLASSES = {"frontier", "self-hosted"}


def _load(name: str) -> dict:
    return yaml.safe_load((REPO / "configs" / name).read_text(encoding="utf-8"))


def test_model_groups_prefer_class_order():
    from smallworks.config import KNOWN_PROVIDERS, Deployment

    cfg = _load("models.yaml")["models"]
    assert {"strong_reasoning", "engineering", "coder_fast", "reviewer"} <= set(cfg)
    for group, deployments in cfg.items():
        assert deployments, group
        for d in deployments:
            dep = Deployment.model_validate(d)
            assert dep.provider in KNOWN_PROVIDERS, (group, d)
            assert dep.model, (group, d)
            assert dep.model_class in VALID_CLASSES, (group, d)
    # Factory policy: frontier decides first, self-hosted executes first.
    assert cfg["strong_reasoning"][0]["class"] == "frontier"
    assert cfg["coder_fast"][0]["class"] == "self-hosted"
    assert cfg["engineering"][0]["class"] == "self-hosted"


def test_factory_policy_loads():
    factory = _load("factory.yaml")["factory"]
    assert factory["mode"] == "supervised"
    assert factory["escalation"]["max_retries"] >= 1
    assert {"blueprint", "release", "escalate"} <= set(factory["approvals_required"])
    assert factory["budgets"]["max_cost_per_task"] > 0
