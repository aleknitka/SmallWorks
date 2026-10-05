"""Canonical SmallWorks artefacts (spec §11) with deterministic decision gates (spec §8)."""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Literal

from pydantic import BaseModel, Field


class TaskStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    PASSED = "passed"
    FAILED = "failed"
    BLOCKED = "blocked"


class ContextPacket(BaseModel):
    """Fresh per-task context (spec §6): goal + contract + symbols + tests + rules."""

    task_id: str = Field(pattern=r"^[A-Z]+-\d+$")
    goal: str = Field(min_length=1)
    module_contract: str = Field(min_length=1)
    relevant_symbols: list[str] = Field(default_factory=list)
    related_tests: list[str] = Field(default_factory=list)
    coding_rules: list[str] = Field(default_factory=list)


class ProjectSpec(BaseModel):
    """Project intent (spec §11): name + goal + constraints + acceptance."""

    name: str = Field(min_length=1)
    goal: str = Field(min_length=1)
    constraints: list[str] = Field(default_factory=list)
    acceptance_criteria: list[str] = Field(min_length=1)


class ModuleSpec(BaseModel):
    """One module boundary inside a Blueprint (spec §11)."""

    name: str = Field(min_length=1)
    responsibility: str = Field(min_length=1)
    interface: str = Field(min_length=1)
    acceptance_criteria: list[str] = Field(min_length=1)


class Blueprint(BaseModel):
    """Approved module decomposition (spec §9, §11); approval is a human gate."""

    project: str = Field(min_length=1)
    modules: list[ModuleSpec] = Field(min_length=1)
    dependencies: list[str] = Field(default_factory=list)
    acceptance_criteria: list[str] = Field(min_length=1)


class ImplementationTask(BaseModel):
    task_id: str = Field(pattern=r"^[A-Z]+-\d+$")
    module: str = Field(min_length=1)
    behaviour: str = Field(min_length=1)
    allowed_files: list[str] = Field(min_length=1)
    acceptance_criteria: list[str] = Field(min_length=1)
    depends_on: list[str] = Field(default_factory=list)


class EngineeringPlan(BaseModel):
    """Ordered bounded tasks realising a Blueprint (spec §9, §11)."""

    project: str = Field(min_length=1)
    tasks: list[ImplementationTask] = Field(min_length=1)


class Patch(BaseModel):
    """Developer output for one task (spec §11): changed files + summary."""

    task_id: str = Field(pattern=r"^[A-Z]+-\d+$")
    files_changed: list[str] = Field(min_length=1)
    summary: str = Field(min_length=1)


class TestReport(BaseModel):
    __test__ = False  # not a test class; silences pytest collection warning

    task_id: str
    passed: bool
    tests_run: int = Field(ge=0)
    tests_failed: int = Field(ge=0)
    raw_output_ref: str | None = None  # RTK keeps raw output as artefact (spec §7)


class ReviewReport(BaseModel):
    __test__ = False  # not a test class; silences pytest collection warning

    task_id: str
    verdict: Literal["PASS", "RETRY", "ESCALATE"]
    notes: str = ""


class Decision(BaseModel):
    task_id: str
    action: Literal["pass", "retry", "escalate"]
    reason: str = ""
    retryable: bool = False
    """May an outer milestone loop re-drive this task in a new round?

    Retry-budget exhaustion and blocked dependencies are retryable (fresh
    context may succeed); reviewer escalations, security failures, and budget
    breaches need a human and park the milestone.
    """


class Milestone(BaseModel):
    """Run-level convergence target: which tasks close it and how (research §4.1).

    ``predicate`` names the deterministic rule ``validate_milestone`` applies
    over the task decisions collected so far. ``verdict`` is the last
    evaluation — ``open`` keeps the outer loop running, ``met`` ends it,
    ``breached`` parks it for a human (budget spent or escalated task).
    """

    milestone_id: str = Field(pattern=r"^[A-Z]+-M\d+$")
    engineering_plan: str = Field(min_length=1)
    predicate: Literal["all_tasks_pass", "no_open_escalations"] = "all_tasks_pass"
    tasks: list[str] = Field(min_length=1)
    max_rounds: int = Field(default=5, ge=1)
    verdict: Literal["open", "met", "breached"] = "open"


def validate_milestone(milestone: Milestone, decisions: dict[str, Decision]) -> Milestone:
    """Deterministic run-level gate over per-task decisions (research §4.1/§4.2).

    - every in-scope task decided ``pass`` ⇒ ``met``
    - any ``escalate`` ⇒ ``breached`` (a human decides; the loop parks)
    - missing decisions or any ``retry`` ⇒ ``open``
    - ``no_open_escalations`` also counts decided-but-unpassed tasks as breached
      once nothing is still retryable — i.e. pass, or park.
    """
    updated = milestone.model_copy(deep=True)
    present = {tid: decisions[tid] for tid in milestone.tasks if tid in decisions}
    if len(present) < len(milestone.tasks):
        updated.verdict = "open"
        return updated
    if any(d.action == "escalate" and not d.retryable for d in present.values()):
        updated.verdict = "breached"
        return updated
    if all(d.action == "pass" for d in present.values()):
        updated.verdict = "met"
        return updated
    if milestone.predicate == "no_open_escalations" and not any(
        d.action != "pass" and d.retryable for d in present.values()
    ):
        # Nothing left that another round could fix — park instead of lingering.
        updated.verdict = "breached"
        return updated
    updated.verdict = "open"
    return updated


class RunReport(BaseModel):
    """Per-run observability record (spec §12)."""

    worker: str
    model: str
    provider: str
    started: datetime
    completed: datetime
    input_tokens: int = Field(ge=0)
    output_tokens: int = Field(ge=0)
    compressed_tokens_saved: int = Field(ge=0)
    cost: float = Field(ge=0.0)
    status: TaskStatus
    parent_task: str | None = None
    context_sources: list[str] = Field(default_factory=list)
    tool_calls: int = Field(ge=0, default=0)
    result: str = ""
    artefacts: list[str] = Field(default_factory=list)


def validate_decision(
    decision: Decision,
    *,
    test_report: TestReport | None,
    review: ReviewReport | None,
    forbidden_files_touched: bool = False,
    security_gate_passed: bool = True,
) -> Decision:
    """Deterministic gates override model decisions (spec §8).

    - failing tests ⇒ cannot pass
    - non-PASS review ⇒ cannot pass
    - forbidden files touched ⇒ reject (retry)
    - security gate failing ⇒ escalate
    """
    if not security_gate_passed:
        return Decision(
            task_id=decision.task_id, action="escalate",
            reason="security gate failed", retryable=False,
        )
    if forbidden_files_touched:
        return Decision(
            task_id=decision.task_id, action="retry",
            reason="forbidden files modified", retryable=True,
        )
    if test_report is not None and (not test_report.passed or test_report.tests_failed > 0):
        if decision.action == "pass":
            return Decision(
                task_id=decision.task_id, action="retry", reason="tests failing", retryable=True
            )
    if review is not None and review.verdict != "PASS" and decision.action == "pass":
        return Decision(
            task_id=decision.task_id, action="retry", reason="review not PASS", retryable=True
        )
    return decision
