"""In-memory run store + orchestrator chat + supervision state (plan 05, spec §10, §12).

Single-process; ``record_report`` also writes the RunReport JSON alongside
task artefacts when the store has a ``save_dir``. Raw shell/test output goes
through the RTK adapter: ``get_logs`` returns compressed summaries by default,
raw text only via ``raw=True`` (the ``--raw via ref`` path).
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field

from smallworks.adapters.rtk import RecallStore, compress_output, recall_output
from smallworks.logging import logger
from smallworks.schemas import Milestone, RunReport, TaskStatus
from smallworks.supervision import BoardRecord, TaskControl, apply_control


class ChatMessage(BaseModel):
    role: Literal["user", "orchestrator"]
    content: str = Field(min_length=1)
    at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class ChatThread(BaseModel):
    run_id: str
    messages: list[ChatMessage] = Field(default_factory=list)


class TaskNode(BaseModel):
    """One DAG node for the milestone panel: identity, edges, latest state."""

    task_id: str
    depends_on: list[str] = Field(default_factory=list)
    wave: int = 0
    outcome: str = "pending"  # pending/passed/retry/escalated
    action: str = ""  # last Decision.action
    reason: str = ""
    attempts: int = 0
    test_status: str = "unknown"
    latest_report: str = ""


class Todo(BaseModel):
    """Internal kanban card (local-only, no GitHub sync): title + TaskStatus."""

    id: str = Field(pattern=r"^[a-z0-9-]+$")
    title: str = Field(min_length=1)
    status: TaskStatus = TaskStatus.PENDING
    detail: str = ""
    updated: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class MilestoneView(BaseModel):
    """Render-ready snapshot of a convergence loop (panel polls this)."""

    milestone_id: str
    predicate: str = "all_tasks_pass"
    verdict: str = "open"
    rounds: int = 0
    max_rounds: int = 5
    nodes: list[TaskNode] = Field(default_factory=list)
    round_outcomes: list[dict[str, str]] = Field(default_factory=list)


class Run(BaseModel):
    run_id: str = Field(pattern=r"^[a-z0-9-]+$")
    task_id: str = Field(pattern=r"^[A-Z]+-\d+$")
    status: TaskStatus = TaskStatus.PENDING
    worker: str = "orchestrator"
    model: str = "unassigned"
    report: RunReport | None = None
    chat: ChatThread | None = None
    board: BoardRecord | None = None
    state: dict = Field(default_factory=lambda: {"status": "running"})
    log_refs: list[str] = Field(default_factory=list)
    log_summaries: list[str] = Field(default_factory=list)


def _demo_report() -> RunReport:
    now = datetime.now(timezone.utc)
    return RunReport(
        worker="orchestrator",
        model="unassigned",
        provider="none",
        started=now,
        completed=now,
        input_tokens=0,
        output_tokens=0,
        compressed_tokens_saved=0,
        cost=0.0,
        status=TaskStatus.PENDING,
        parent_task="DEMO-001",
    )


class Store:
    """Single-process store; durable JSON lives under ``save_dir`` when set."""

    def __init__(self, save_dir: Path | None = None) -> None:
        demo = Run(run_id="demo", task_id="DEMO-001", report=_demo_report())
        demo.chat = ChatThread(run_id="demo")
        self._runs: dict[str, Run] = {"demo": demo}
        self._todos: dict[str, Todo] = {}
        self._milestones: dict[str, MilestoneView] = {}
        self._seed_milestone_showcase()
        self._raw = RecallStore()
        self._save_dir = save_dir
        self._seed_showcase()

    def _seed_showcase(self) -> None:
        """Two static fixture runs so the console has depth on first boot."""
        for run_id, task_id, status in (("nostromo-1", "AUTH-017", TaskStatus.RUNNING),
                                        ("nostromo-2", "AUTH-018", TaskStatus.BLOCKED)):
            run = Run(run_id=run_id, task_id=task_id, status=status,
                      worker="developer", model="ollama/fast-a")
            run.chat = ChatThread(run_id=run_id)
            run.state = {"status": "paused" if status == TaskStatus.BLOCKED else "running"}
            self._runs[run_id] = run

    def _seed_milestone_showcase(self) -> None:
        """Three static milestones so the picker has states to show (mirrors _seed_showcase).

        AUTH-M1: open diamond, one task retrying. BILL-M2: breached (reviewer
        escalated — parked for a human). OPS-M3: met yesterday, kept for history.
        """
        self._milestones["AUTH-M1"] = MilestoneView(
            milestone_id="AUTH-M1",
            verdict="open",
            rounds=1,
            max_rounds=5,
            nodes=[
                TaskNode(task_id="AUTH-017", wave=0, outcome="passed", action="pass",
                         attempts=1, test_status="passed", latest_report="review PASS: looks good"),
                TaskNode(task_id="AUTH-018", wave=1, outcome="retry",
                         action="escalate", reason="retry budget spent after 1 attempts",
                         attempts=1, test_status="failed",
                         latest_report="tests failed (3 run, 1 failed)",
                         depends_on=["AUTH-017"]),
                TaskNode(task_id="AUTH-019", wave=1, outcome="passed", action="pass",
                         attempts=1, test_status="passed", latest_report="review PASS: looks good",
                         depends_on=["AUTH-017"]),
            ],
            round_outcomes=[{"AUTH-017": "pass", "AUTH-018": "escalate", "AUTH-019": "pass"}],
        )
        self._milestones["BILL-M2"] = MilestoneView(
            milestone_id="BILL-M2",
            predicate="all_tasks_pass",
            verdict="breached",
            rounds=2,
            max_rounds=5,
            nodes=[
                TaskNode(task_id="BILL-004", wave=0, outcome="passed", action="pass",
                         attempts=1, test_status="passed", latest_report="review PASS: looks good"),
                TaskNode(task_id="BILL-005", wave=1, outcome="escalated",
                         action="escalate", reason="reviewer escalated: scope drift",
                         attempts=2, test_status="passed",
                         latest_report="review ESCALATE: scope drift",
                         depends_on=["BILL-004"]),
            ],
            round_outcomes=[
                {"BILL-004": "pass", "BILL-005": "retry"},
                {"BILL-004": "pass", "BILL-005": "escalate"},
            ],
        )
        self._milestones["OPS-M3"] = MilestoneView(
            milestone_id="OPS-M3",
            predicate="all_tasks_pass",
            verdict="met",
            rounds=1,
            max_rounds=5,
            nodes=[
                TaskNode(task_id="OPS-001", wave=0, outcome="passed", action="pass",
                         attempts=1, test_status="passed", latest_report="review PASS: looks good"),
                TaskNode(task_id="OPS-002", wave=0, outcome="passed", action="pass",
                         attempts=1, test_status="passed", latest_report="review PASS: looks good"),
            ],
            round_outcomes=[{"OPS-001": "pass", "OPS-002": "pass"}],
        )

    def list_runs(self) -> list[Run]:
        return list(self._runs.values())

    def get_run(self, run_id: str) -> Run | None:
        return self._runs.get(run_id)

    def create_run(
        self, run_id: str, task_id: str, *, worker: str = "orchestrator", model: str = "unassigned"
    ) -> Run:
        log = logger.bind(component="store", run_id=run_id, task_id=task_id)
        if run_id in self._runs:
            raise ValueError(f"run {run_id!r} already exists")
        run = Run(run_id=run_id, task_id=task_id, worker=worker, model=model)
        run.chat = ChatThread(run_id=run_id)
        self._runs[run_id] = run
        log.debug("created run worker={} model={}", worker, model)
        return run

    def post_chat(self, run_id: str, content: str) -> ChatThread | None:
        run = self._runs.get(run_id)
        if run is None or not content.strip():
            return None
        if run.chat is None:
            run.chat = ChatThread(run_id=run_id)
        run.chat.messages.append(ChatMessage(role="user", content=content.strip()))
        run.chat.messages.append(
            ChatMessage(role="orchestrator", content=f"queued: {content.strip()}")
        )
        return run.chat

    def append_log(self, run_id: str, raw: str) -> str | None:
        run = self._runs.get(run_id)
        if run is None:
            return None
        summary, ref = compress_output(raw, self._raw)
        run.log_summaries.append(summary)
        run.log_refs.append(ref)
        logger.bind(component="store", run_id=run_id, ref=ref).debug(
            "log appended raw_chars={} summary_chars={}", len(raw), len(summary)
        )
        return ref

    def get_logs(self, run_id: str, *, raw: bool = False) -> list[str] | None:
        run = self._runs.get(run_id)
        if run is None:
            return None
        if not raw:
            return list(run.log_summaries)
        out: list[str] = []
        for ref, summary in zip(run.log_refs, run.log_summaries):
            out.append(recall_output(ref, self._raw) or summary)
        return out

    def record_report(self, run_id: str, report: RunReport) -> Run | None:
        run = self._runs.get(run_id)
        if run is None:
            return None
        run.report = report
        run.status = report.status
        log = logger.bind(component="store", run_id=run_id, worker=report.worker)
        log.debug("report recorded cost={} status={}", report.cost, report.status.value)
        if self._save_dir is not None:
            self._save_dir.mkdir(parents=True, exist_ok=True)
            target = self._save_dir / f"{run_id}.json"
            target.write_text(report.model_dump_json(indent=2), encoding="utf-8")
            log.debug("report persisted {}", str(target))
        return run

    def set_board(self, run_id: str, board: BoardRecord) -> Run | None:
        run = self._runs.get(run_id)
        if run is None:
            return None
        run.board = board
        return run

    def apply_run_control(self, run_id: str, control: TaskControl) -> dict | None:
        run = self._runs.get(run_id)
        if run is None:
            return None
        run.state = apply_control(run.state, control)
        if run.state.get("status") == "paused":
            run.status = TaskStatus.BLOCKED
        elif run.state.get("status") == "cancelled":
            run.status = TaskStatus.FAILED
        elif run.state.get("status") == "running":
            run.status = TaskStatus.RUNNING
        return run.state

    def record_milestone(self, view: MilestoneView) -> MilestoneView:
        self._milestones[view.milestone_id] = view
        return view

    def get_milestone(self, milestone_id: str) -> MilestoneView | None:
        return self._milestones.get(milestone_id)

    def list_milestones(self) -> list[MilestoneView]:
        return list(self._milestones.values())

    def list_todos(self) -> list[Todo]:
        return list(self._todos.values())

    def upsert_todo(self, todo: Todo) -> Todo:
        self._todos[todo.id] = todo
        logger.bind(component="store", todo_id=todo.id).debug("todo upserted status={}", todo.status.value)
        if self._save_dir is not None:
            self._save_dir.mkdir(parents=True, exist_ok=True)
            (self._save_dir / "todos.json").write_text(
                "[" + ",".join(t.model_dump_json() for t in self._todos.values()) + "]",
                encoding="utf-8",
            )
        return todo

    def move_todo(self, todo_id: str, status: TaskStatus) -> Todo | None:
        todo = self._todos.get(todo_id)
        if todo is None:
            return None
        todo.status = status
        todo.updated = datetime.now(timezone.utc)
        return self.upsert_todo(todo)

    def cost_for_task(self, task_id: str) -> tuple[float, int, int]:
        cost, inp, out = 0.0, 0, 0
        for run in self._runs.values():
            if run.task_id == task_id and run.report is not None:
                cost += run.report.cost
                inp += run.report.input_tokens
                out += run.report.output_tokens
        return cost, inp, out


STORE = Store()
