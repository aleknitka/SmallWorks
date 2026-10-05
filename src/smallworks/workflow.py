"""TAKT-style workflow state machine: supervised dev loop (plan 04, spec §9).

States: request -> engineer -> plan -> develop <-> test -> review -> decide ->
(writer) -> done. Terminal outcomes: ``passed`` (review PASS + gates hold),
``retry`` (loop back to develop, bounded by ``max_retries``), ``escalated``
(retry budget spent, security failure, or reviewer ESCALATE — a human decides).

Independent modules run concurrently: ``run_module`` owns one task's loop; the
orchestrator fans tasks out with a thread pool and each task still passes
through the same gated sequence. Git worktree setup/teardown brackets the
Developer step; forbidden-file violations reject via ``validate_decision``.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from enum import Enum

from smallworks.gateway import BudgetExceeded, Gateway, GatewayExhausted
from smallworks.logging import logger
from smallworks.schemas import (
    Decision,
    ImplementationTask,
    Patch,
    ReviewReport,
    TestReport,
    validate_decision,
)
from smallworks.workers.roles import (
    WorkerError,
    developer_task,
    reviewer_task,
    tester_task,
)
from smallworks.worktrees import WorktreeError, worktree_for


class TaskState(str, Enum):
    REQUEST = "request"
    ENGINEER = "engineer"
    PLAN = "plan"
    DEVELOP = "develop"
    TEST = "test"
    REVIEW = "review"
    DECIDE = "decide"
    WRITE = "write"
    DONE = "done"


class TaskOutcome(str, Enum):
    PASSED = "passed"
    RETRY = "retry"
    ESCALATED = "escalated"


@dataclass
class TaskResult:
    task_id: str
    outcome: TaskOutcome
    attempts: int
    decision: Decision
    patch: Patch | None = None
    test_report: TestReport | None = None
    review: ReviewReport | None = None
    states: list[str] = field(default_factory=list)


@dataclass
class Workflow:
    """Owns one task's gated loop; inject gateway + worktree root for tests."""

    gateway: Gateway
    max_retries: int = 2
    worktree_root: str | None = None

    def run_task(self, task: ImplementationTask, *, symbols: list[str] | None = None) -> TaskResult:
        log = logger.bind(component="workflow", task_id=task.task_id)
        states: list[str] = [TaskState.REQUEST, TaskState.ENGINEER, TaskState.PLAN]
        attempts = 0
        last_patch: Patch | None = None
        last_report: TestReport | None = None
        last_review: ReviewReport | None = None
        while True:
            attempts += 1
            log.debug("attempt {} states={}", attempts, states)
            try:
                states.append(TaskState.DEVELOP)
                try:
                    with worktree_for(task.task_id, root=self.worktree_root):
                        last_patch = developer_task(task, self.gateway, symbols=symbols)
                except WorktreeError as exc:
                    log.error("worktree setup failed: {}", exc)
                    raise WorkerError(f"worktree failed: {exc}") from exc
                states.append(TaskState.TEST)
                last_report = tester_task(task, last_patch, self.gateway)
                states.append(TaskState.REVIEW)
                last_review = reviewer_task(task, last_patch, last_report, self.gateway)
            except (WorkerError, GatewayExhausted, BudgetExceeded) as exc:
                log.warning("attempt {} worker failure: {}", attempts, exc)
                if isinstance(exc, BudgetExceeded):
                    decision = Decision(task_id=task.task_id, action="escalate",
                                        reason=f"budget breach: {exc}")
                    return TaskResult(task.task_id, TaskOutcome.ESCALATED, attempts,
                                      decision, last_patch, last_report, last_review,
                                      [s.value for s in states])
                if attempts > self.max_retries:
                    decision = Decision(task_id=task.task_id, action="escalate",
                                        reason=f"retry budget spent ({attempts}): {exc}")
                    return TaskResult(task.task_id, TaskOutcome.ESCALATED, attempts,
                                      decision, last_patch, last_report, last_review,
                                      [s.value for s in states])
                continue
            states.append(TaskState.DECIDE)
            forbidden = any(f not in task.allowed_files for f in last_patch.files_changed)
            gated = validate_decision(
                Decision(task_id=task.task_id, action="pass"),
                test_report=last_report,
                review=last_review,
                forbidden_files_touched=forbidden,
            )
            log.debug("gated decision={} review={}", gated.action, last_review.verdict)
            if last_review.verdict == "ESCALATE":
                gated = Decision(task_id=task.task_id, action="escalate",
                                 reason="reviewer escalated")
            if gated.action == "pass":
                states.append(TaskState.WRITE)
                states.append(TaskState.DONE)
                return TaskResult(task.task_id, TaskOutcome.PASSED, attempts,
                                  gated, last_patch, last_report, last_review,
                                  [s.value for s in states])
            if gated.action == "escalate":
                return TaskResult(task.task_id, TaskOutcome.ESCALATED, attempts,
                                  gated, last_patch, last_report, last_review,
                                  [s.value for s in states])
            # retry: loop back to develop unless the budget is spent
            if attempts > self.max_retries:
                decision = Decision(task_id=task.task_id, action="escalate",
                                    reason=f"retry budget spent after {attempts} attempts")
                return TaskResult(task.task_id, TaskOutcome.ESCALATED, attempts,
                                  decision, last_patch, last_report, last_review,
                                  [s.value for s in states])
            log.debug("retrying task (attempt {}/{})", attempts, self.max_retries + 1)


def _waves(tasks: list[ImplementationTask]) -> list[list[ImplementationTask]]:
    """Topological waves over ``depends_on``; unknown deps and cycles fail fast."""
    by_id = {t.task_id: t for t in tasks}
    for t in tasks:
        for dep in t.depends_on:
            if dep not in by_id:
                raise ValueError(f"task {t.task_id} depends on unknown task {dep!r}")
    remaining = {t.task_id for t in tasks}
    done: set[str] = set()
    waves: list[list[ImplementationTask]] = []
    while remaining:
        ready = sorted(tid for tid in remaining if all(d in done for d in by_id[tid].depends_on))
        if not ready:
            raise ValueError(f"dependency cycle among tasks: {sorted(remaining)}")
        waves.append([by_id[tid] for tid in ready])
        done.update(ready)
        remaining.difference_update(ready)
    return waves


def run_workflow(
    tasks: list[ImplementationTask],
    gateway: Gateway,
    *,
    max_retries: int = 2,
    max_workers: int = 4,
    worktree_root: str | None = None,
) -> list[TaskResult]:
    """Run tasks in dependency waves; each wave fans out, each keeps its gated sequence.

    A task whose dependency did not pass is escalated without running, naming
    the unmet dependency.
    """
    log = logger.bind(component="workflow", tasks=[t.task_id for t in tasks])
    log.debug("fan-out {} tasks workers={}", len(tasks), max_workers)
    by_id = {t.task_id: t for t in tasks}
    outcomes: dict[str, TaskResult] = {}
    for wave in _waves(tasks):
        runnable = [
            t
            for t in wave
            if all(
                outcomes[d].outcome == TaskOutcome.PASSED for d in t.depends_on
            )
        ]
        for t in wave:
            if t not in runnable:
                unmet = [d for d in t.depends_on if outcomes[d].outcome != TaskOutcome.PASSED]
                outcomes[t.task_id] = TaskResult(
                    t.task_id,
                    TaskOutcome.ESCALATED,
                    0,
                    Decision(
                        task_id=t.task_id,
                        action="escalate",
                        reason=f"blocked: dependencies did not pass: {unmet}",
                    ),
                    None,
                    None,
                    None,
                    ["request", "done"],
                )
                log.debug("task {} blocked by {}", t.task_id, unmet)
        if len(runnable) == 1:
            outcomes[runnable[0].task_id] = Workflow(gateway, max_retries, worktree_root).run_task(
                runnable[0]
            )
        elif runnable:
            with ThreadPoolExecutor(max_workers=min(max_workers, len(runnable))) as pool:
                futures = {
                    pool.submit(Workflow(gateway, max_retries, worktree_root).run_task, t): t
                    for t in runnable
                }
                for f, t in futures.items():
                    outcomes[t.task_id] = f.result()
    _ = by_id
    return [outcomes[t.task_id] for t in tasks]


__all__ = ["TaskOutcome", "TaskResult", "TaskState", "Workflow", "run_workflow"]
