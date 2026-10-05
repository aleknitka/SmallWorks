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
    Milestone,
    Patch,
    ReviewReport,
    TestReport,
    validate_decision,
    validate_milestone,
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


def _read_existing(task: ImplementationTask, workdir: object) -> dict[str, str]:
    """Current text of in-scope files the developer may touch (missing = absent)."""
    from pathlib import Path

    root = Path(str(workdir))
    existing: dict[str, str] = {}
    for name in task.allowed_files:
        if not name.endswith(".py"):
            continue
        candidate = root / name
        if candidate.is_file():
            try:
                existing[name] = candidate.read_text(encoding="utf-8")
            except OSError:
                continue
    return existing

def _apply_patch(task: ImplementationTask, patch: Patch, workdir: object, *, verify: bool = False) -> None:
    """Materialize ``patch.contents`` under ``workdir``.
    Under ``verify`` every entry of ``files_changed`` needs full text in
    ``contents`` — a patch that names files without writing them is
    unverifiable and rejected. Scripted runs (``verify=False``) skip
    enforcement: their transports return names only, no repo files asserted.
    """
    from pathlib import Path

    from smallworks.workers.roles import WorkerError

    log = logger.bind(component="workflow", task_id=task.task_id)
    root = Path(str(workdir))
    if verify:
        missing = [f for f in patch.files_changed if f not in patch.contents]
        if missing:
            log.warning("patch names unwritten files: {}", missing)
            raise WorkerError(f"developer left files unwritten: {missing}")
    for name, text in patch.contents.items():
        target = root / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")
    log.debug("patch applied files={}", patch.files_changed)


@dataclass
class Workflow:
    """Owns one task's gated loop; inject gateway + worktree root for tests."""

    gateway: Gateway
    max_retries: int = 2
    worktree_root: str | None = None
    verify: bool = False

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
                    with worktree_for(task.task_id, root=self.worktree_root) as workdir:
                        last_patch = developer_task(
                            task, self.gateway, symbols=symbols,
                            existing=_read_existing(task, workdir),
                        )
                        _apply_patch(task, last_patch, workdir, verify=self.verify)
                        states.append(TaskState.TEST)
                        last_report = tester_task(task, last_patch, self.gateway,
                                                  verify=self.verify, root=workdir)
                except WorktreeError as exc:
                    log.error("worktree setup failed: {}", exc)
                    raise WorkerError(f"worktree failed: {exc}") from exc
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
                                        reason=f"retry budget spent ({attempts}): {exc}",
                                        retryable=True)
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
                                    reason=f"retry budget spent after {attempts} attempts",
                                    retryable=True)
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
    passed: set[str] | None = None,
    verify: bool = False,
) -> list[TaskResult]:
    """Run tasks in dependency waves; each wave fans out, each keeps its gated sequence.

    A task whose dependency did not pass is escalated without running, naming
    the unmet dependency. ``passed`` names task ids already closed by an outer
    loop (milestones): their dependencies count as satisfied and they are not
    re-run — only unfinished tasks consume model calls.
    """
    log = logger.bind(component="workflow", tasks=[t.task_id for t in tasks])
    log.debug("fan-out {} tasks workers={}", len(tasks), max_workers)
    already: set[str] = set(passed or ())
    outcomes: dict[str, TaskResult] = {}
    for wave in _waves(tasks):
        runnable = [
            t
            for t in wave
            if t.task_id not in already
            and all(
                d in already or (d in outcomes and outcomes[d].outcome == TaskOutcome.PASSED)
                for d in t.depends_on
            )
        ]
        for t in wave:
            if t.task_id not in already and t not in runnable:
                unmet = [d for d in t.depends_on if not (d in already or (d in outcomes and outcomes[d].outcome == TaskOutcome.PASSED))]
                outcomes[t.task_id] = TaskResult(
                    t.task_id,
                    TaskOutcome.ESCALATED,
                    0,
                    Decision(
                        task_id=t.task_id,
                        action="escalate",
                        reason=f"blocked: dependencies did not pass: {unmet}",
                        retryable=True,
                    ),
                    None,
                    None,
                    None,
                    ["request", "done"],
                )
                log.debug("task {} blocked by {}", t.task_id, unmet)
        if len(runnable) == 1:
            outcomes[runnable[0].task_id] = Workflow(
                gateway, max_retries, worktree_root, verify
            ).run_task(runnable[0])
        elif runnable:
            with ThreadPoolExecutor(max_workers=min(max_workers, len(runnable))) as pool:
                futures = {
                    pool.submit(
                        Workflow(gateway, max_retries, worktree_root, verify).run_task, t
                    ): t
                    for t in runnable
                }
                for f, t in futures.items():
                    outcomes[t.task_id] = f.result()
    results = []
    for t in tasks:
        if t.task_id in already and t.task_id not in outcomes:
            continue  # closed by outer loop; not part of this round's results
        results.append(outcomes[t.task_id])
    return results


@dataclass
class MilestoneResult:
    """Outcome of the convergence loop: final verdict + per-round history."""

    milestone: Milestone
    rounds: int
    history: list[list[TaskResult]] = field(default_factory=list)


def milestone_view(
    tasks: list[ImplementationTask],
    milestone: Milestone,
    latest: dict[str, TaskResult],
    history: list[list[TaskResult]],
    round_no: int,
) -> dict:
    """Render-ready snapshot: wave-ranked nodes + per-round outcomes + verdict.

    Returns plain data (not store models — workflow stays store-free); the
    service layer maps it into ``MilestoneView`` for persistence.
    """
    waves = _waves(tasks)
    wave_of = {t.task_id: i for i, wave in enumerate(waves) for t in wave}
    nodes = []
    for t in tasks:
        r = latest.get(t.task_id)
        test_status = "unknown"
        latest_report = ""
        if r is not None and r.test_report is not None:
            test_status = "passed" if r.test_report.passed else "failed"
            latest_report = (
                f"tests {'passed' if r.test_report.passed else 'failed'} "
                f"({r.test_report.tests_run} run, {r.test_report.tests_failed} failed)"
            )
        if r is not None and r.review is not None and r.decision.action == "pass":
            latest_report = f"review {r.review.verdict}: {r.review.notes}"
        nodes.append(
            {
                "task_id": t.task_id,
                "depends_on": list(t.depends_on),
                "wave": wave_of.get(t.task_id, 0),
                "outcome": r.outcome.value if r is not None else "pending",
                "action": r.decision.action if r is not None else "",
                "reason": r.decision.reason if r is not None else "",
                "attempts": r.attempts if r is not None else 0,
                "test_status": test_status,
                "latest_report": latest_report,
            }
        )
    return {
        "milestone_id": milestone.milestone_id,
        "predicate": milestone.predicate,
        "verdict": milestone.verdict,
        "rounds": round_no,
        "max_rounds": milestone.max_rounds,
        "nodes": nodes,
        "round_outcomes": [{r.task_id: r.decision.action for r in rnd} for rnd in history],
    }


def run_until_milestone(
    tasks: list[ImplementationTask],
    milestone: Milestone,
    gateway: Gateway,
    *,
    max_retries: int = 2,
    max_workers: int = 4,
    worktree_root: str | None = None,
    controls: list | None = None,
    on_round=None,
    fresh_controls=None,
    verify: bool = False,
) -> MilestoneResult:
    """Run the task graph until the milestone predicate holds, or park it.

    Each round runs unfinished tasks through ``run_workflow`` (fresh
    ContextPackets per §6 — no parent-conversation inheritance), then evaluates
    ``validate_milestone`` over the latest decisions. ``met`` ends the loop;
    ``breached`` or ``max_rounds`` spent parks for a human. A ``retry`` control
    re-enters an escalated task; ``cancel`` stops the loop immediately.
    ``on_round`` receives a ``milestone_view`` snapshot after every round
    (service wires it to STORE so the panel can poll). ``fresh_controls`` is
    an optional zero-arg callable returning newly queued controls — the service
    drains its per-milestone queue through it so panel buttons steer live loops.
    """
    from smallworks.supervision import TaskControl

    log = logger.bind(component="workflow", milestone_id=milestone.milestone_id)
    pending_controls: list = list(controls or [])
    current = milestone.model_copy(deep=True)
    latest: dict[str, TaskResult] = {}
    history: list[list[TaskResult]] = []
    round_no = 0
    def snap() -> None:
        if on_round is not None:
            on_round(milestone_view(tasks, current, latest, history, round_no))

    while True:
        if fresh_controls is not None:
            pending_controls.extend(fresh_controls())
        for control in [c for c in pending_controls if isinstance(c, TaskControl)]:
            if control.action == "cancel" and (
                control.task_id == current.milestone_id or control.task_id in latest
            ):
                current.verdict = "breached"
                snap()
                return MilestoneResult(current, round_no, history)
            if control.action == "retry" and control.task_id in latest:
                del latest[control.task_id]
            if control.action == "approve" and control.task_id == current.milestone_id:
                current.verdict = "met"
                snap()
                return MilestoneResult(current, round_no, history)
            if control.action == "reject" and control.task_id == current.milestone_id:
                current.verdict = "breached"
                snap()
                return MilestoneResult(current, round_no, history)
            if control.action == "send_back" and control.task_id == current.milestone_id:
                # Human returns scope to the Engineer: re-drive every task fresh.
                latest.clear()
        pending_controls = [c for c in pending_controls if not isinstance(c, TaskControl)]
        if round_no >= current.max_rounds:
            current.verdict = "breached"
            log.debug("milestone breached: max_rounds spent ({})", current.max_rounds)
            snap()
            return MilestoneResult(current, round_no, history)
        round_no += 1
        unfinished = [t for t in tasks if t.task_id not in latest or latest[t.task_id].decision.action != "pass"]
        if not unfinished:
            current = validate_milestone(current, {tid: r.decision for tid, r in latest.items()})
            snap()
            return MilestoneResult(current, round_no - 1, history)
        log.debug("milestone round {}/{} tasks={}", round_no, current.max_rounds, [t.task_id for t in unfinished])
        # Full graph each round so wave logic unlocks dependents of newly-passing
        # tasks; already-passed tasks are skipped, not re-run (no wasted calls).
        closed = {tid for tid, r in latest.items() if r.decision.action == "pass"}
        round_results = run_workflow(
            tasks, gateway, max_retries=max_retries, max_workers=max_workers,
            worktree_root=worktree_root, passed=closed, verify=verify,
        )
        history.append(round_results)
        for r in round_results:
            latest[r.task_id] = r
        current = validate_milestone(current, {tid: r.decision for tid, r in latest.items()})
        snap()
        log.debug("milestone round {} verdict={}", round_no, current.verdict)
        if current.verdict in ("met", "breached"):
            return MilestoneResult(current, round_no, history)


__all__ = ["MilestoneResult", "TaskOutcome", "TaskResult", "TaskState", "Workflow", "milestone_view", "run_until_milestone", "run_workflow"]
