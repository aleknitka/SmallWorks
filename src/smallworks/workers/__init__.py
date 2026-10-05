"""Role worker package (spec §3). Orchestrator routes; it never codes."""

from smallworks.workers import prompts
from smallworks.workers.roles import (
    WorkerError,
    architect_task,
    developer_task,
    engineer_task,
    reviewer_task,
    tester_task,
    writer_task,
)

__all__ = [
    "WorkerError",
    "architect_task",
    "developer_task",
    "engineer_task",
    "prompts",
    "reviewer_task",
    "tester_task",
    "writer_task",
]
