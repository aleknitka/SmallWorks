"""Supervision tests: board mapping, each human control, RunReport (plan 05)."""

from smallworks.gateway import GatewayCompletion
from smallworks.schemas import Decision, ImplementationTask, Patch, ReviewReport, TaskStatus
from smallworks.schemas import TestReport as TestReportModel
from smallworks.supervision import (
    TaskControl,
    apply_control,
    board_record,
    decide_gate,
    run_report,
)


def _task() -> ImplementationTask:
    return ImplementationTask(
        task_id="AUTH-017",
        module="auth",
        behaviour="handle expired token",
        allowed_files=["src/auth/token.py"],
        acceptance_criteria=["expired token returns 401"],
    )


def _completion() -> GatewayCompletion:
    return GatewayCompletion(
        text="{}",
        role="developer",
        group="coder_fast",
        deployment="ollama/fast-a",
        deployment_class="self-hosted",
        provider="ollama",
        input_tokens=120,
        output_tokens=40,
        cost=0.002,
        latency_ms=900,
        attempts=1,
    )


def test_board_record_exposes_spec10_fields():
    report = TestReportModel(task_id="AUTH-017", passed=True, tests_run=3, tests_failed=0)
    review = ReviewReport(task_id="AUTH-017", verdict="PASS", notes="clean")
    patch = Patch(task_id="AUTH-017", files_changed=["src/auth/token.py"], summary="fix")
    record = board_record(
        _task(), status="review", role="reviewer", model="ollama/fast-a",
        attempt=2, report=report, patch=patch, review=review, cost=0.002,
        input_tokens=120, output_tokens=40,
    )
    dumped = record.model_dump()
    # Every §10 field present.
    for key in ("status", "role", "model", "dependencies", "attempt", "test_status",
                "latest_report", "artefacts", "cost_tokens"):
        assert key in dumped
    assert dumped["test_status"] == "passed"
    assert "PASS" in dumped["latest_report"]
    assert "src/auth/token.py" in dumped["artefacts"]
    assert "$0.0020" in dumped["cost_tokens"]


def test_board_record_failed_tests():
    report = TestReportModel(task_id="AUTH-017", passed=False, tests_run=3, tests_failed=1)
    record = board_record(_task(), status="test", report=report, attempt=1)
    assert record.test_status == "failed"
    assert "failed" in record.latest_report


def test_pause_and_retry_controls():
    state = {"status": "running"}
    paused = apply_control(state, TaskControl(task_id="AUTH-017", action="pause"))
    assert paused["status"] == "paused"
    retried = apply_control(paused, TaskControl(task_id="AUTH-017", action="retry"))
    assert retried["status"] == "running" and retried["retry_requested"] is True


def test_cancel_and_escalate_controls():
    assert apply_control({"status": "running"}, TaskControl(task_id="AUTH-017", action="cancel"))["status"] == "cancelled"
    assert apply_control({"status": "running"}, TaskControl(task_id="AUTH-017", action="escalate"))["status"] == "escalated"


def test_change_model_send_back_instruct():
    state = apply_control({"status": "running"}, TaskControl(task_id="AUTH-017", action="change_model", argument="external/frontier-coder"))
    assert state["model_override"] == "external/frontier-coder"
    state = apply_control(state, TaskControl(task_id="AUTH-017", action="send_back"))
    assert state["send_back_to"] == "engineer"
    state = apply_control(state, TaskControl(task_id="AUTH-017", action="instruct", argument="use refresh flow"))
    assert state["instructions"] == ["use refresh flow"]


def test_approve_reject_gate():
    approved = apply_control(
        {"status": "paused", "pending_approval": "release"},
        TaskControl(task_id="AUTH-017", action="approve"),
    )
    assert approved["pending_approval"] is None and approved["last_gate_decision"] == "approved"
    rejected = apply_control(
        {"status": "paused", "pending_approval": "release"},
        TaskControl(task_id="AUTH-017", action="reject"),
    )
    assert rejected["last_gate_decision"] == "rejected" and rejected["status"] == "paused"


def test_run_report_complete_per_section12():
    report = run_report(
        worker="developer", completion=_completion(), task_id="AUTH-017",
        status=TaskStatus.PASSED, context_sources=["serena:token", "contract:auth"],
        tool_calls=4, result="passed", artefacts=["src/auth/token.py"],
    )
    dumped = report.model_dump()
    for key in ("worker", "model", "provider", "input_tokens", "output_tokens",
                "cost", "context_sources", "tool_calls", "result", "parent_task", "artefacts"):
        assert key in dumped
    assert dumped["model"] == "ollama/fast-a" and dumped["provider"] == "ollama"
    assert dumped["context_sources"] == ["serena:token", "contract:auth"]
    assert dumped["parent_task"] == "AUTH-017"


def test_decide_gate_maps_decisions():
    approvals = ["blueprint", "release", "escalate"]
    assert decide_gate(Decision(task_id="AUTH-017", action="pass"), approvals) == "release"
    assert decide_gate(Decision(task_id="AUTH-017", action="escalate"), approvals) == "escalate"
    assert decide_gate(Decision(task_id="AUTH-017", action="retry"), approvals) is None
