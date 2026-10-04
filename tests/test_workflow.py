"""Workflow tests: happy path, retries, escalation, concurrency (plan 04)."""

import json
import threading

import pytest

from smallworks.config import Deployment, FactoryPolicy, WorkerConfig
from smallworks.gateway import Gateway, TransportError, TransportResult
from smallworks.schemas import ImplementationTask
from smallworks.workflow import TaskOutcome, Workflow, run_workflow
from smallworks.workers.roles import WorkerError


def _policy(**over) -> FactoryPolicy:
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
    roles = ["engineer", "developer", "tester", "reviewer", "writer"]
    return {r: WorkerConfig(model_group="coder_fast", tier="small", max_concurrent=8) for r in roles}


def _models() -> dict[str, list[Deployment]]:
    return {"coder_fast": [Deployment.model_validate({"name": "ollama/fast-a", "class": "self-hosted"})]}


def _task(task_id: str = "AUTH-017") -> ImplementationTask:
    return ImplementationTask(
        task_id=task_id,
        module="auth",
        behaviour="handle expired token",
        allowed_files=["src/auth/token.py"],
        acceptance_criteria=["expired token returns 401"],
    )


class KeyedTransport:
    """Routes by a role tag smuggled in the prompt by the test gateway subclass."""

    def __init__(self, texts: dict[str, str], fail_roles: set[str] | None = None):
        self.texts = texts
        self.fail_roles = fail_roles or set()
        self.calls: list[str] = []
        self._lock = threading.Lock()

    def complete(self, deployment, prompt, *, task_id):
        role = prompt.split("::", 1)[0]
        with self._lock:
            self.calls.append(role)
        if role in self.fail_roles:
            raise TransportError(f"{role} boom")
        return TransportResult(text=self.texts[role])


class RoleGateway(Gateway):
    """Prefixes prompts with 'role::' so KeyedTransport can route canned text."""

    def complete(self, role, prompt, *, task_id="TASK-0"):
        return super().complete(role, f"{role}::{prompt}", task_id=task_id)


def _texts(pass_tests: bool = True, verdict: str = "PASS") -> dict[str, str]:
    failed = 0 if pass_tests else 1
    return {
        "engineer": json.dumps({"tasks": []}),
        "developer": json.dumps({"files_changed": ["src/auth/token.py"], "summary": "fix expiry"}),
        "tester": json.dumps({"passed": pass_tests, "tests_run": 3, "tests_failed": failed}),
        "reviewer": json.dumps({"verdict": verdict, "notes": "looks good"}),
        "writer": "docs updated.",
    }


def _gateway(texts: dict[str, str], fail_roles: set[str] | None = None, **policy_over) -> RoleGateway:
    return RoleGateway(_models(), _workers(), _policy(**policy_over), transport=KeyedTransport(texts, fail_roles))


def test_happy_path_passes(tmp_path):
    gw = _gateway(_texts())
    result = Workflow(gw, worktree_root=str(tmp_path)).run_task(_task())
    assert result.outcome == TaskOutcome.PASSED
    assert result.decision.action == "pass"
    assert result.patch is not None and result.patch.files_changed == ["src/auth/token.py"]
    assert result.test_report is not None and result.test_report.passed
    assert result.states[-1] == "done"


def test_test_failure_retries_then_escalates(tmp_path):
    gw = _gateway(_texts(pass_tests=False))
    result = Workflow(gw, max_retries=1, worktree_root=str(tmp_path)).run_task(_task())
    assert result.outcome == TaskOutcome.ESCALATED
    assert result.attempts == 2  # initial + 1 retry, then budget spent
    assert "retry budget" in result.decision.reason


def test_reviewer_escalate_escalates(tmp_path):
    gw = _gateway(_texts(verdict="ESCALATE"))
    result = Workflow(gw, worktree_root=str(tmp_path)).run_task(_task())
    assert result.outcome == TaskOutcome.ESCALATED
    assert result.decision.action == "escalate"


def test_transport_failure_escalates_after_retries(tmp_path):
    gw = _gateway(_texts(), fail_roles={"developer"})
    result = Workflow(gw, max_retries=1, worktree_root=str(tmp_path)).run_task(_task())
    assert result.outcome == TaskOutcome.ESCALATED
    assert result.attempts == 2


def test_two_modules_run_concurrently(tmp_path):
    gw = _gateway(_texts())
    tasks = [_task("AUTH-017"), _task("AUTH-018")]
    results = run_workflow(tasks, gw, max_workers=2, worktree_root=str(tmp_path))
    assert [r.task_id for r in results] == ["AUTH-017", "AUTH-018"]
    assert all(r.outcome == TaskOutcome.PASSED for r in results)


def test_developer_out_of_scope_rejected(tmp_path):
    texts = _texts()
    texts["developer"] = json.dumps({"files_changed": ["/etc/passwd"], "summary": "evil"})
    gw = _gateway(texts)
    with pytest.raises(WorkerError, match="out-of-scope"):
        from smallworks.workers.roles import developer_task

        developer_task(_task(), gw)
