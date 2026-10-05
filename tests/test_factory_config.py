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
    # Pool shape: every group declares strategy + deployments; all routable.
    for group, node in cfg.items():
        assert node["strategy"] == "pool", group
        assert len(node["deployments"]) >= 2, group
        for d in node["deployments"]:
            dep = Deployment.model_validate(d)
            assert dep.provider in KNOWN_PROVIDERS, (group, d)
            assert dep.model, (group, d)
            assert dep.model_class in VALID_CLASSES, (group, d)
    # OpenRouter/HuggingFace slots exist as pool members or rescue.
    vendors = {d["provider"] for node in cfg.values() for d in node["deployments"]}
    assert {"ollama", "openrouter", "huggingface"} <= vendors


def test_factory_policy_loads():
    factory = _load("factory.yaml")["factory"]
    assert factory["mode"] == "supervised"
    assert factory["escalation"]["max_retries"] >= 1
    assert {"blueprint", "release", "escalate"} <= set(factory["approvals_required"])
    assert factory["budgets"]["max_cost_per_task"] > 0
