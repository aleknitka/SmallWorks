"""Phase-2 tests: flags off by default, each item gated, MVP unchanged (plan 07)."""

import json

import pytest

from smallworks.board import sync_payload_v2, to_issue_body
from smallworks.config import Deployment, FactoryPolicy, WorkerConfig
from smallworks.eval import ScriptedTransport, compare, fixture_tasks, role_texts, run_smallworks_arm
from smallworks.gateway import Gateway, TransportResult
from smallworks.phase2 import (
    BenchmarkStore,
    NeedsHuman,
    Phase2Disabled,
    Phase2Flags,
    assess_complexity,
    can_release,
    eval_delta,
    flags_from_env,
    keyword_recall,
    load_phase2,
    merge_allowed,
    next_tier,
    rank_by_cost,
    request_release,
    route_choice,
    run_architect,
    tune_budget,
)
from smallworks.supervision import BoardRecord, board_record
from smallworks.schemas import ImplementationTask


def _gateway(texts: dict[str, str]) -> Gateway:
    models = {"coder_fast": [Deployment.model_validate({"name": "ollama/fast-a", "class": "self-hosted"})]}
    roles = ["architect", "engineer", "developer", "tester", "reviewer", "writer"]
    workers = {r: WorkerConfig(model_group="coder_fast", tier="small", max_concurrent=8) for r in roles}
    policy = FactoryPolicy.model_validate({
        "mode": "supervised", "max_retries": 2, "approvals_required": ["blueprint", "release", "escalate"],
        "max_cost_per_task": 5.0, "max_wallclock_minutes": 60.0,
    })

    class Tag(Gateway):
        def complete(self, role, prompt, *, task_id="TASK-0"):
            return super().complete(role, f"{role}::{prompt}", task_id=task_id)

    class Keyed:
        def complete(self, deployment, prompt, *, task_id):
            return TransportResult(text=texts[prompt.split("::", 1)[0]])

    return Tag(models, workers, policy, transport=Keyed())


def _blueprint_texts() -> dict[str, str]:
    return {
        "architect": json.dumps({
            "project": "auth",
            "modules": [{"name": "token", "responsibility": "tokens",
                         "interface": "validate(token) -> bool",
                         "acceptance_criteria": ["expired rejected"]}],
            "dependencies": [],
            "acceptance_criteria": ["auth works"],
        }),
        "engineer": json.dumps({"tasks": []}),
        "developer": json.dumps({"files_changed": ["src/auth/token.py"], "summary": "x"}),
        "tester": json.dumps({"passed": True, "tests_run": 1, "tests_failed": 0}),
        "reviewer": json.dumps({"verdict": "PASS", "notes": "ok"}),
        "writer": "docs.",
    }


# --- flags ---


def test_all_flags_off_by_default():
    assert Phase2Flags().any_on() is False


def test_unknown_flag_fails_fast():
    with pytest.raises(ValueError, match="unknown phase-2 flag"):
        flags_from_env({"SMALLWORKS_PHASE2": "architect,stargate"})
    with pytest.raises(ValueError, match="unknown phase-2 flag"):
        Phase2Flags().enabled("stargate")


def test_env_enables_named_flags():
    flags = flags_from_env({"SMALLWORKS_PHASE2": "architect, release_gate"})
    assert flags.architect and flags.release_gate and not flags.benchmarking


def test_load_phase2_defaults_off(tmp_path):
    assert load_phase2(tmp_path / "missing.yaml").any_on() is False


def test_load_phase2_reads_file_and_rejects_unknown(tmp_path):
    cfg = tmp_path / "factory.yaml"
    cfg.write_text("factory:\n  phase2:\n    architect: true\n", encoding="utf-8")
    assert load_phase2(cfg).architect is True
    cfg.write_text("factory:\n  phase2:\n    stargate: true\n", encoding="utf-8")
    with pytest.raises(ValueError, match="unknown phase-2 flag"):
        load_phase2(cfg)


# --- 1. architect ---


def test_architect_off_raises():
    with pytest.raises(Phase2Disabled):
        run_architect("idea", None, "overview", Phase2Flags())


def test_architect_returns_blueprint_with_gate():
    from smallworks.phase2 import architect_approval_gate

    bp = run_architect("expiry", _gateway(_blueprint_texts()), "repo auth", Phase2Flags(architect=True))
    assert bp.project == "auth" and len(bp.modules) == 1
    assert architect_approval_gate() == "blueprint"  # human approves before Engineer


# --- 2. bounded routing: overrides rule ---


def test_routing_off_raises():
    with pytest.raises(Phase2Disabled):
        route_choice({"retry": 0.9}, flags=Phase2Flags())


def test_deterministic_override_wins():
    assert route_choice({"retry": 0.9, "escalate": 0.1}, deterministic="escalate",
                        flags=Phase2Flags(decision_routing=True)) == "escalate"


def test_top_score_wins_without_override():
    assert route_choice({"retry": 0.2, "accept": 0.8}, flags=Phase2Flags(decision_routing=True)) == "accept"


def test_complexity_bands():
    assert assess_complexity(chars=100, files=1) == "small"
    assert assess_complexity(chars=10_000, files=2) == "medium"
    assert assess_complexity(chars=100, files=99) == "large"


# --- 3. escalation, cost-aware ranking, benchmarking ---


def test_escalation_off_raises():
    with pytest.raises(Phase2Disabled):
        next_tier("small", 1, flags=Phase2Flags())


def test_tier_climb_and_human():
    flags = Phase2Flags(dynamic_escalation=True)
    assert next_tier("small", 0, flags=flags) == "small"
    assert next_tier("small", 1, flags=flags) == "medium"
    assert next_tier("medium", 3, flags=flags) == "large"
    with pytest.raises(NeedsHuman):
        next_tier("large", 5, flags=flags)


def test_cheapest_first_unknown_last():
    deps = [
        Deployment.model_validate({"name": "vllm/b", "class": "self-hosted"}),
        Deployment.model_validate({"name": "ollama/a", "class": "self-hosted"}),
        Deployment.model_validate({"name": "external/c", "class": "frontier"}),
    ]
    ranked = rank_by_cost(deps, {"ollama/a": 0.002, "vllm/b": 0.001})
    assert [d.name for d in ranked] == ["vllm/b", "ollama/a", "external/c"]


def test_benchmark_averages():
    from smallworks.gateway import GatewayCompletion

    store = BenchmarkStore()
    store.observe(GatewayCompletion(text="t", role="developer", group="g", deployment="ollama/a",
                                    deployment_class="self-hosted", provider="ollama",
                                    input_tokens=10, output_tokens=5, cost=0.002,
                                    latency_ms=100, attempts=1))
    store.observe(GatewayCompletion(text="t", role="developer", group="g", deployment="ollama/a",
                                    deployment_class="self-hosted", provider="ollama",
                                    input_tokens=10, output_tokens=5, cost=0.004,
                                    latency_ms=300, attempts=1))
    assert store.avg_cost() == {"ollama/a": pytest.approx(0.003)}
    assert store.to_report()["ollama/a"]["avg_latency_ms"] == pytest.approx(200.0)


# --- 4. context: same interfaces ---


def test_tune_budget_off_raises():
    with pytest.raises(Phase2Disabled):
        tune_budget(8000, [100], flags=Phase2Flags())


def test_tune_budget_tracks_use_with_floor():
    flags = Phase2Flags(context_optimisation=True)
    assert tune_budget(8000, [], flags=flags) == 8000
    assert tune_budget(8000, [1000, 3000], flags=flags) == 3000
    assert tune_budget(8000, [10], flags=flags) == 2000  # 25% floor


def test_keyword_recall_ranks_overlap():
    docs = [("ref:a", "token expiry validation"), ("ref:b", "billing invoice totals")]
    assert keyword_recall("expired token", docs) == ["ref:a"]
    assert keyword_recall("nothing matching here xyz", docs) == []


# --- 5. release gate: never autonomous ---


def test_release_needs_flag_and_human():
    assert merge_allowed({"status": "running"}, flags=Phase2Flags()) is False
    flags = Phase2Flags(release_gate=True)
    state = request_release({"status": "running"})
    assert state["pending_approval"] == "release"
    assert merge_allowed(state, flags=flags) is False  # requested, not approved
    approved = dict(state, last_gate="release", last_gate_decision="approved")
    assert can_release(approved, flags=flags) is True
    assert merge_allowed(approved, flags=flags) is True


# --- 6. board v2 + event stream ---


def _record() -> BoardRecord:
    return board_record(
        ImplementationTask(task_id="AUTH-017", module="auth", behaviour="b",
                           allowed_files=["src/auth/token.py"], acceptance_criteria=["a"]),
        status="review", role="reviewer", model="ollama/a", attempt=2, cost=0.002,
        input_tokens=10, output_tokens=5,
    )


def test_board_v2_gated_and_pure():
    with pytest.raises(Phase2Disabled):
        sync_payload_v2(_record(), phase2=Phase2Flags())
    payload = sync_payload_v2(_record(), phase2=Phase2Flags(board_sync_v2=True))
    assert payload["task"] == "AUTH-017" and payload["attempt"] == 2
    assert "Task: AUTH-017" in to_issue_body(_record())  # v1 renderer unchanged


def test_event_stream_gated_off():
    from fastapi.testclient import TestClient

    from smallworks.service import app

    res = TestClient(app).get("/api/runs/demo/events")
    assert res.status_code == 404  # flag off by default


# --- 7. eval-delta + MVP regression ---


def test_eval_delta_verdicts():
    base = {"completion": "2/2", "api_cost": 1.0, "context_chars": 100_000}
    same_cheaper = {"completion": "2/2", "api_cost": 0.5, "context_chars": 16_000}
    same_equal = {"completion": "2/2", "api_cost": 1.0, "context_chars": 100_000}
    worse = {"completion": "2/2", "api_cost": 2.0, "context_chars": 200_000}
    assert eval_delta("architect", base, same_cheaper).verdict == "BETTER"
    assert eval_delta("architect", base, same_cheaper).is_neutral_or_better()
    assert eval_delta("architect", base, same_equal).verdict == "NEUTRAL"
    assert eval_delta("architect", base, worse).verdict == "WORSE"
    with pytest.raises(ValueError, match="unknown phase-2 flag"):
        eval_delta("stargate", base, base)


def test_mvp_path_unchanged_with_flags_off(tmp_path):
    tasks = fixture_tasks()
    texts = role_texts()
    patches = {"EVAL-001": ["src/auth/token.py"], "EVAL-002": ["src/billing/invoice.py"]}
    flags = load_phase2(tmp_path / "nope.yaml")
    assert flags.any_on() is False
    sw = run_smallworks_arm(tasks, transport=ScriptedTransport(dict(texts), patch_files=patches),
                            worktree_root=str(tmp_path))
    bl_texts = ScriptedTransport(dict(texts))
    from smallworks.eval import run_baseline

    bl = run_baseline(tasks, transport=bl_texts)
    assert compare(bl, sw)["verdict"] == "SUPPORTED"
