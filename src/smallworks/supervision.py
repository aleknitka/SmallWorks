"""Supervision: GitHub board sync, human controls, CLI status/logs/cost (plan 05, spec §10).

Board sync reuses GitHub Issues + Projects — no custom dashboard. This module
maps a task + its artefacts to the §10 field set so a dumb sync script can
push it verbatim; the GitHub API call itself stays outside (needs a token and
network, not unit-test material). Human controls are workflow inputs applied
to a ``TaskControl`` record the orchestrator reads before each attempt.
``RunReport`` building per §12 keeps every run auditable.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Literal

from pydantic import BaseModel, Field

from smallworks.gateway import GatewayCompletion
from smallworks.logging import logger
from smallworks.schemas import (
    Decision,
    ImplementationTask,
    Patch,
    ReviewReport,
    RunReport,
    TaskStatus,
    TestReport,
)

ControlAction = Literal[
    "pause", "cancel", "retry", "escalate", "change_model", "send_back", "instruct", "approve", "reject"
]


class TaskControl(BaseModel):
    """One human input to the workflow; the orchestrator drains these FIFO.

    ``task_id`` addresses a task (``AUTH-017``) or a milestone (``AUTH-M1``)
    for milestone-scoped actions: ``approve``/``reject`` close the milestone
    gate, ``send_back`` returns the milestone's tasks to the Engineer,
    ``pause``/``cancel``/``retry`` behave per-target.
    """

    task_id: str = Field(pattern=r"^[A-Z]+-(\d+|M\d+)$")
    action: ControlAction
    argument: str = ""  # model name, instructions, rejection reason, ...
    actor: str = "human"


class BoardRecord(BaseModel):
    """§10 field set for one task — push verbatim to Issue body / Project fields."""

    task_id: str
    status: str
    role: str = ""
    model: str = ""
    dependencies: list[str] = Field(default_factory=list)
    attempt: int = 0
    test_status: str = "unknown"
    latest_report: str = ""
    artefacts: list[str] = Field(default_factory=list)
    cost_tokens: str = ""


def board_record(
    task: ImplementationTask,
    *,
    status: str,
    role: str = "",
    model: str = "",
    attempt: int = 0,
    report: TestReport | None = None,
    patch: Patch | None = None,
    review: ReviewReport | None = None,
    cost: float = 0.0,
    input_tokens: int = 0,
    output_tokens: int = 0,
) -> BoardRecord:
    """Map task + artefacts to the §10 board fields."""
    test_status = "unknown" if report is None else ("passed" if report.passed else "failed")
    latest = ""
    if review is not None:
        latest = f"review {review.verdict}: {review.notes}"
    elif report is not None:
        latest = f"tests {'passed' if report.passed else 'failed'} ({report.tests_run} run, {report.tests_failed} failed)"
    artefacts = list(patch.files_changed) if patch else []
    if report and report.raw_output_ref:
        artefacts.append(report.raw_output_ref)
    record = BoardRecord(
        task_id=task.task_id,
        status=status,
        role=role,
        model=model,
        dependencies=list(task.depends_on),
        attempt=attempt,
        test_status=test_status,
        latest_report=latest,
        artefacts=artefacts,
        cost_tokens=f"${cost:.4f} in={input_tokens} out={output_tokens}",
    )
    logger.bind(component="supervision", task_id=task.task_id, status=status).debug(
        "board record: {} attempt={} tests={}", role, attempt, test_status
    )
    return record


def apply_control(
    state: dict,
    control: TaskControl,
    *,
    approvals_required: list[str] | None = None,
) -> dict:
    """Apply one human control to a mutable run-state dict. Returns the state.

    State keys: ``status`` (running/paused/cancelled/done), ``pending_approval``
    (gate name or None), ``model_override``, ``instructions`` (list), ``send_back_to``.
    Unknown keys pass through untouched.
    """
    log = logger.bind(component="supervision", task_id=control.task_id, action=control.action)
    state = dict(state)
    action = control.action
    if action == "pause":
        state["status"] = "paused"
    elif action == "cancel":
        state["status"] = "cancelled"
    elif action == "retry":
        state["status"] = "running"
        state["retry_requested"] = True
    elif action == "escalate":
        state["status"] = "escalated"
    elif action == "change_model":
        state["model_override"] = control.argument
    elif action == "send_back":
        state["send_back_to"] = "engineer"
        state["status"] = "running"
    elif action == "instruct":
        state.setdefault("instructions", []).append(control.argument)
    elif action in ("approve", "reject"):
        gate = state.get("pending_approval") or (approvals_required or [None])[0]
        state["pending_approval"] = None
        state["last_gate"] = gate
        state["last_gate_decision"] = "approved" if action == "approve" else "rejected"
        if action == "reject":
            state["status"] = "paused"
    log.debug("applied; status={}", state.get("status"))
    return state


def run_report(
    *,
    worker: str,
    completion: GatewayCompletion | None = None,
    task_id: str | None = None,
    status: TaskStatus = TaskStatus.PASSED,
    context_sources: list[str] | None = None,
    tool_calls: int = 0,
    result: str = "",
    artefacts: list[str] | None = None,
    started: datetime | None = None,
    completed: datetime | None = None,
) -> RunReport:
    """Build a §12 RunReport from a gateway completion + supervision metadata."""
    now = datetime.now(timezone.utc)
    report = RunReport(
        worker=worker,
        model=completion.deployment if completion else "unassigned",
        provider=completion.provider if completion else "none",
        started=started or now,
        completed=completed or now,
        input_tokens=completion.input_tokens if completion else 0,
        output_tokens=completion.output_tokens if completion else 0,
        compressed_tokens_saved=0,
        cost=completion.cost if completion else 0.0,
        status=status,
        parent_task=task_id,
        context_sources=list(context_sources or []),
        tool_calls=tool_calls,
        result=result,
        artefacts=list(artefacts or []),
    )
    logger.bind(component="supervision", worker=worker, task_id=task_id).debug(
        "run report cost={} tokens={}/{}",
        report.cost,
        report.input_tokens,
        report.output_tokens,
    )
    return report


def decide_gate(decision: Decision, approvals_required: list[str]) -> str | None:
    """Return the approval gate name for a decision, or None if autonomous."""
    mapping = {"pass": "release", "retry": None, "escalate": "escalate"}
    gate = mapping.get(decision.action)
    if gate is not None and gate in approvals_required:
        return gate
    return None


__all__ = [
    "BoardRecord",
    "TaskControl",
    "apply_control",
    "board_record",
    "decide_gate",
    "run_report",
]
