"""In-memory run store + orchestrator chat stub (real workflow arrives in plan 04)."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Literal

from pydantic import BaseModel, Field

from smallworks.schemas import RunReport, TaskStatus


class ChatMessage(BaseModel):
    role: Literal["user", "orchestrator"]
    content: str = Field(min_length=1)
    at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class ChatThread(BaseModel):
    run_id: str
    messages: list[ChatMessage] = Field(default_factory=list)


class Run(BaseModel):
    run_id: str = Field(pattern=r"^[a-z0-9-]+$")
    task_id: str = Field(pattern=r"^[A-Z]+-\d+$")
    status: TaskStatus = TaskStatus.PENDING
    worker: str = "orchestrator"
    model: str = "unassigned"
    report: RunReport | None = None
    chat: ChatThread | None = None


class Store:
    """Single-process store; durable persistence arrives with the workflow (plan 04)."""

    def __init__(self) -> None:
        now = datetime.now(timezone.utc)
        report = RunReport(
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
        demo = Run(run_id="demo", task_id="DEMO-001", report=report)
        demo.chat = ChatThread(run_id="demo")
        self._runs: dict[str, Run] = {"demo": demo}

    def list_runs(self) -> list[Run]:
        return list(self._runs.values())

    def get_run(self, run_id: str) -> Run | None:
        return self._runs.get(run_id)

    def post_chat(self, run_id: str, content: str) -> ChatThread | None:
        run = self._runs.get(run_id)
        if run is None or not content.strip():
            return None
        if run.chat is None:
            run.chat = ChatThread(run_id=run_id)
        run.chat.messages.append(ChatMessage(role="user", content=content.strip()))
        # Stub reply until the orchestrator agent lands (plan 04): echo intent.
        run.chat.messages.append(
            ChatMessage(role="orchestrator", content=f"queued: {content.strip()}")
        )
        return run.chat


STORE = Store()
