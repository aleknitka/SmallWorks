"""Gateway tests: class-order fallback, concurrency cap, budget breach (plan 02)."""

import threading

import pytest

from smallworks.config import Deployment, FactoryPolicy, WorkerConfig
from smallworks.gateway import BudgetExceeded, Gateway, GatewayExhausted, TransportError, TransportResult


def _policy(**over: object) -> FactoryPolicy:
    base: dict[str, object] = {
        "mode": "supervised",
        "max_retries": 2,
        "approvals_required": ["blueprint"],
        "max_cost_per_task": 5.0,
        "max_wallclock_minutes": 60.0,
    }
    base.update(over)
    return FactoryPolicy.model_validate(base)


def _workers() -> dict[str, WorkerConfig]:
    return {
        "developer": WorkerConfig(model_group="coder_fast", tier="small", max_concurrent=2),
        "orchestrator": WorkerConfig(model_group="strong_reasoning", tier="large", max_concurrent=1),
    }


def _models() -> dict[str, list[Deployment]]:
    return {
        "coder_fast": [
            Deployment.model_validate({"name": "ollama/fast-a", "class": "self-hosted"}),
            Deployment.model_validate({"name": "vllm/fast-b", "class": "self-hosted"}),
            Deployment.model_validate({"name": "external/fast-c", "class": "frontier"}),
        ],
        "strong_reasoning": [
            Deployment.model_validate({"name": "external/brain", "class": "frontier"}),
            Deployment.model_validate({"name": "ollama/brain-local", "class": "self-hosted"}),
        ],
    }


class ScriptTransport:
    """Fails named deployments in order, then succeeds; records call sequence."""

    def __init__(self, fail_names: set[str] | None = None, cost: float = 0.0) -> None:
        self.fail_names = fail_names or set()
        self.cost = cost
        self.calls: list[str] = []

    def complete(self, deployment: Deployment, prompt: str, *, task_id: str) -> TransportResult:
        self.calls.append(deployment.name)
        if deployment.name in self.fail_names:
            raise TransportError(f"boom: {deployment.name}")
        return TransportResult(text=f"ok from {deployment.name}", input_tokens=3, output_tokens=7, cost=self.cost)


def test_self_hosted_first_then_frontier_fallback():
    transport = ScriptTransport(fail_names={"ollama/fast-a", "vllm/fast-b"})
    gw = Gateway(_models(), _workers(), _policy(), transport=transport)
    done = gw.complete("developer", "write code", task_id="AUTH-017")
    assert transport.calls == ["ollama/fast-a", "vllm/fast-b", "external/fast-c"]
    assert done.deployment == "external/fast-c"
    assert done.deployment_class == "frontier"
    assert done.attempts == 3


def test_strong_reasoning_hits_frontier_first():
    transport = ScriptTransport()
    gw = Gateway(_models(), _workers(), _policy(), transport=transport)
    done = gw.complete("orchestrator", "triage", task_id="AUTH-017")
    assert transport.calls == ["external/brain"]
    assert done.deployment_class == "frontier"


def test_retry_budget_caps_attempts():
    transport = ScriptTransport(fail_names={"ollama/fast-a", "vllm/fast-b", "external/fast-c"})
    gw = Gateway(_models(), _workers(), _policy(max_retries=1), transport=transport)
    with pytest.raises(GatewayExhausted):
        gw.complete("developer", "write code", task_id="AUTH-017")
    assert transport.calls == ["ollama/fast-a", "vllm/fast-b"]


def test_concurrency_cap_never_exceeded():
    entered = 0
    peak = 0
    lock = threading.Lock()
    barrier = threading.Barrier(2)  # parties = max_concurrent: pairs meet, peak must be 2

    class SlowTransport:
        def complete(self, deployment, prompt, *, task_id):
            nonlocal entered, peak
            with lock:
                entered += 1
                peak = max(peak, entered)
            barrier.wait(timeout=10)
            with lock:
                entered -= 1
            return TransportResult(text="ok")

    workers = {"developer": WorkerConfig(model_group="coder_fast", tier="small", max_concurrent=2)}
    models = {"coder_fast": [Deployment.model_validate({"name": "ollama/fast-a", "class": "self-hosted"})]}
    gw = Gateway(models, workers, _policy(), transport=SlowTransport())
    threads = [threading.Thread(target=lambda: gw.complete("developer", "x", task_id="A-1")) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=20)
    assert peak <= 2


def test_budget_breach_pauses_for_human():
    transport = ScriptTransport(cost=99.0)
    gw = Gateway(_models(), _workers(), _policy(max_cost_per_task=5.0), transport=transport)
    with pytest.raises(BudgetExceeded, match="exceeds max_cost_per_task"):
        gw.complete("developer", "write code", task_id="AUTH-017")


def test_pool_spreads_calls_across_healthy():
    from smallworks.config import ModelGroup

    transport = ScriptTransport()
    pool = {
        "coder_fast": ModelGroup(
            strategy="pool", pool_cooldown_s=60.0, deployments=_models()["coder_fast"]
        )
    }
    gw = Gateway(_models(), _workers(), _policy(max_retries=5), transport=transport, groups=pool)
    for i in range(3):
        gw.complete("developer", "x", task_id=f"P-{i}")
    assert transport.calls == ["ollama/fast-a", "vllm/fast-b", "external/fast-c"]


def test_pool_skips_down_member_then_shares_live():
    from smallworks.config import ModelGroup

    transport = ScriptTransport(fail_names={"ollama/fast-a"})
    pool = {
        "coder_fast": ModelGroup(
            strategy="pool", pool_cooldown_s=60.0, deployments=_models()["coder_fast"]
        )
    }
    gw = Gateway(_models(), _workers(), _policy(max_retries=5), transport=transport, groups=pool)
    for i in range(3):
        gw.complete("developer", "x", task_id=f"P-{i}")
    # First call burns fast-a then wins fast-b; next calls rotate fast-b/fast-c.
    assert transport.calls[0:2] == ["ollama/fast-a", "vllm/fast-b"]
    assert "ollama/fast-a" not in transport.calls[2:]


def test_fallback_order_unchanged_without_pool():
    transport = ScriptTransport(fail_names={"ollama/fast-a"})
    gw = Gateway(_models(), _workers(), _policy(), transport=transport)
    gw.complete("developer", "x", task_id="F-0")
    assert transport.calls == ["ollama/fast-a", "vllm/fast-b"]


def test_unknown_role_rejected():
    gw = Gateway(_models(), _workers(), _policy(), transport=ScriptTransport())
    with pytest.raises(ValueError, match="unknown role"):
        gw.complete("janitor", "x", task_id="A-1")


def test_resolve_endpoint_prefers_deployment_endpoint():
    from smallworks.config import default_providers
    from smallworks.gateway import resolve_endpoint

    dep = Deployment.model_validate(
        {"provider": "ollama", "endpoint": "http://custom:11434/v1", "model": "m"}
    )
    assert resolve_endpoint(dep, default_providers()).base_url == "http://custom:11434/v1"


def test_resolve_endpoint_env_overrides_yaml(tmp_path):
    from smallworks.config import default_providers
    from smallworks.gateway import resolve_endpoint

    dep = Deployment.model_validate({"provider": "github", "model": "openai/gpt-4o-mini"})
    resolved = resolve_endpoint(dep, default_providers(), env={"GITHUB_TOKEN": "sekret"})
    assert resolved.headers == {"Authorization": "Bearer sekret"}
    assert resolved.base_url == "https://models.github.ai/inference"


def test_resolve_endpoint_reads_dotenv(tmp_path):
    from smallworks.config import default_providers
    from smallworks.gateway import load_dotenv, resolve_endpoint

    dotenv = tmp_path / ".env"
    dotenv.write_text("GITHUB_TOKEN=from-file\n", encoding="utf-8")
    assert load_dotenv(dotenv) == {"GITHUB_TOKEN": "from-file"}
    dep = Deployment.model_validate({"provider": "github", "model": "openai/gpt-4o-mini"})
    resolved = resolve_endpoint(dep, default_providers(), env={}, dotenv_path=dotenv)
    assert resolved.headers == {"Authorization": "Bearer from-file"}


def test_resolve_endpoint_unknown_provider_rejected():
    from smallworks.gateway import TransportError, resolve_endpoint

    dep = Deployment.model_validate({"provider": "nope", "model": "m"})
    with pytest.raises(TransportError, match="no endpoint"):
        resolve_endpoint(dep, {})
