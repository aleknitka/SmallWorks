"""Workflow tests: happy path, retries, escalation, concurrency (plan 04)."""

import json
import threading

import pytest

from smallworks.config import Deployment, FactoryPolicy, WorkerConfig
from smallworks.gateway import Gateway, TransportError, TransportResult
from smallworks.schemas import ImplementationTask
from smallworks.workflow import TaskOutcome, Workflow, run_until_milestone, run_workflow
from smallworks.schemas import Milestone
from smallworks.supervision import TaskControl
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


def _dep_task(task_id: str, *deps: str) -> ImplementationTask:
    task = _task(task_id)
    return ImplementationTask(
        task_id=task.task_id,
        module=task.module,
        behaviour=task.behaviour,
        allowed_files=task.allowed_files,
        acceptance_criteria=task.acceptance_criteria,
        depends_on=list(deps),
    )


def test_diamond_dependency_runs_in_waves(tmp_path):
    gw = _gateway(_texts())
    leaf = _dep_task("AUTH-017")
    mid_a = _dep_task("AUTH-018", "AUTH-017")
    mid_b = _dep_task("AUTH-019", "AUTH-017")
    top = _dep_task("AUTH-020", "AUTH-018", "AUTH-019")
    results = run_workflow([top, mid_b, mid_a, leaf], gw, max_workers=2, worktree_root=str(tmp_path))
    assert [r.task_id for r in results] == ["AUTH-020", "AUTH-019", "AUTH-018", "AUTH-017"]
    assert all(r.outcome == TaskOutcome.PASSED for r in results)


def test_failed_dependency_blocks_dependent_without_running(tmp_path):
    gw = _gateway(_texts(pass_tests=False))
    leaf = _dep_task("AUTH-017")
    top = _dep_task("AUTH-018", "AUTH-017")
    results = run_workflow([leaf, top], gw, max_workers=2, worktree_root=str(tmp_path))
    assert results[1].outcome == TaskOutcome.ESCALATED
    assert results[1].attempts == 0
    assert "AUTH-017" in results[1].decision.reason


def test_unknown_dependency_fails_fast(tmp_path):
    gw = _gateway(_texts())
    with __import__("pytest").raises(ValueError, match="unknown task"):
        run_workflow([_dep_task("AUTH-017", "AUTH-999")], gw, worktree_root=str(tmp_path))


def test_dependency_cycle_fails_fast(tmp_path):
    gw = _gateway(_texts())
    tasks = [_dep_task("AUTH-017", "AUTH-018"), _dep_task("AUTH-018", "AUTH-017")]
    with __import__("pytest").raises(ValueError, match="cycle"):
        run_workflow(tasks, gw, worktree_root=str(tmp_path))


class FlapTransport(KeyedTransport):
    """Fails tester once per task in ``fail_once_tasks``, then passes.

    Inner ``run_task`` retries absorb transient failures, so cross-round
    convergence needs a failure the inner loop cannot outlast: with
    ``max_retries=0`` the task gets one attempt per round — fail round 1,
    pass round 2.
    """

    def __init__(self, texts, fail_once_tasks: set[str] | None = None) -> None:
        super().__init__(texts)
        self.fail_once_tasks = set(fail_once_tasks or ())
        self.failed: set[str] = set()
        self.developer_calls = 0

    def complete(self, deployment, prompt, *, task_id):
        if prompt.startswith("tester::") and task_id in self.fail_once_tasks and task_id not in self.failed:
            self.failed.add(task_id)
            bad = json.loads(self.texts["tester"])
            bad["passed"] = False
            bad["tests_failed"] = 1
            return TransportResult(text=json.dumps(bad), input_tokens=1, output_tokens=1)
        if prompt.startswith("developer::"):
            self.developer_calls += 1
        return super().complete(deployment, prompt, task_id=task_id)


def _milestone(task_ids=("AUTH-017",), **over) -> Milestone:
    base: dict = {
        "milestone_id": "AUTH-M1",
        "engineering_plan": "plan-auth",
        "predicate": "all_tasks_pass",
        "tasks": list(task_ids),
    }
    base.update(over)
    return Milestone.model_validate(base)


def test_milestone_met_first_round(tmp_path):
    gw = _gateway(_texts())
    done = run_until_milestone([_task()], _milestone(), gw, worktree_root=str(tmp_path))
    assert done.milestone.verdict == "met"
    assert done.rounds == 1


def test_milestone_converges_after_retry_without_rerunning_passed(tmp_path):
    # AUTH-018 passes round 1; AUTH-017 fails once (single attempt per round),
    # then passes round 2 alone — AUTH-018 is skipped, not re-run.
    transport = FlapTransport(_texts(), fail_once_tasks={"AUTH-017"})
    gw = RoleGateway(_models(), _workers(), _policy(), transport=transport)
    tasks = [_task("AUTH-017"), _task("AUTH-018")]
    done = run_until_milestone(tasks, _milestone(tasks=("AUTH-017", "AUTH-018")), gw,
                               max_retries=0, worktree_root=str(tmp_path))
    assert done.milestone.verdict == "met"
    assert done.rounds == 2
    assert transport.developer_calls == 3
    assert [r.task_id for r in done.history[1]] == ["AUTH-017"]


def test_milestone_breached_when_rounds_spent(tmp_path):
    gw = _gateway(_texts(pass_tests=False))
    done = run_until_milestone([_task()], _milestone(max_rounds=2), gw,
                               worktree_root=str(tmp_path))
    assert done.milestone.verdict == "breached"
    assert done.rounds == 2
    assert len(done.history) == 2


def test_milestone_breached_on_escalation_parks_for_human(tmp_path):
    gw = _gateway(_texts(verdict="ESCALATE"))
    done = run_until_milestone([_task()], _milestone(), gw, worktree_root=str(tmp_path))
    assert done.milestone.verdict == "breached"
    assert done.rounds == 1


def test_retry_control_reenters_escalated_task(tmp_path):
    gw = _gateway(_texts())
    escalated = run_until_milestone([_task()], _milestone(), _gateway(_texts(verdict="ESCALATE")),
                                    worktree_root=str(tmp_path))
    assert escalated.milestone.verdict == "breached"
    control = TaskControl(task_id="AUTH-017", action="retry")
    done = run_until_milestone([_task()], _milestone(), gw, worktree_root=str(tmp_path),
                               controls=[control])
    assert done.milestone.verdict == "met"


def test_developer_out_of_scope_rejected(tmp_path):
    texts = _texts()
    texts["developer"] = json.dumps({"files_changed": ["/etc/passwd"], "summary": "evil"})
    gw = _gateway(texts)
    with pytest.raises(WorkerError, match="out-of-scope"):
        from smallworks.workers.roles import developer_task

        developer_task(_task(), gw)
