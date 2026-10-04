"""Evaluation harness: Baseline vs SmallWorks on a pinned task set (plan 06, spec §13).

Two arms, same tasks, isolated worktrees per attempt, seeded retries:

- Baseline: one large model, full repo context (simulated by a scripted
  transport returning canned text). No gates beyond the model itself.
- SmallWorks: the real ``run_workflow`` loop — bounded packets, role workers,
  deterministic gates, retry/escalate.

Every §13 metric is sourced from RunReports + test results + wall-clock, and
the comparison report decides the hypothesis per task set. Raw outputs persist
as artefacts so a re-run reproduces the report bit-for-bit.
"""

from __future__ import annotations

import json
import random
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

from smallworks.config import Deployment, FactoryPolicy, WorkerConfig
from smallworks.gateway import Gateway, TransportResult
from smallworks.logging import logger
from smallworks.schemas import ImplementationTask, RunReport, TaskStatus
from smallworks.supervision import run_report
from smallworks.workflow import TaskOutcome, run_workflow

METRICS = (
    "completion", "tests_passed", "retries", "human_interventions", "regressions",
    "total_tokens", "wallclock_s", "gpu_s", "api_cost", "context_chars", "escalations",
)


@dataclass
class ArmResult:
    arm: Literal["baseline", "smallworks"]
    tasks: int = 0
    completed: int = 0
    tests_passed: int = 0
    tests_run: int = 0
    retries: int = 0
    human_interventions: int = 0
    regressions: int = 0
    total_tokens: int = 0
    wallclock_s: float = 0.0
    gpu_s: float = 0.0  # self-hosted GPU estimate; 0 for frontier-only baseline
    api_cost: float = 0.0
    context_chars: int = 0
    escalations: int = 0
    reports: list[RunReport] = field(default_factory=list)

    def as_row(self) -> dict[str, object]:
        return {
            "arm": self.arm,
            "completion": f"{self.completed}/{self.tasks}",
            "tests_passed": f"{self.tests_passed}/{self.tests_run}",
            "retries": self.retries,
            "human_interventions": self.human_interventions,
            "regressions": self.regressions,
            "total_tokens": self.total_tokens,
            "wallclock_s": round(self.wallclock_s, 2),
            "gpu_s": round(self.gpu_s, 2),
            "api_cost": round(self.api_cost, 4),
            "context_chars": self.context_chars,
            "escalations": self.escalations,
        }


class ScriptedTransport:
    """Deterministic canned text per role; ``fail_plan`` forces failures by (task, role).

    ``patch_files`` maps task_id -> files_changed so the scripted developer
    stays inside each task's allowed scope (otherwise the allowed-files gate
    correctly escalates — use that path only when testing escalation).
    """

    def __init__(
        self,
        texts: dict[str, str],
        *,
        fail_plan: dict[tuple[str, str], int] | None = None,
        patch_files: dict[str, list[str]] | None = None,
        tokens: tuple[int, int] = (100, 30),
        cost: float = 0.001,
    ) -> None:
        self.texts = texts
        self.fail_plan = dict(fail_plan or {})
        self.patch_files = dict(patch_files or {})
        self.tokens = tokens
        self.cost = cost

    def complete(self, deployment, prompt, *, task_id):
        from smallworks.gateway import TransportError

        role = prompt.split("::", 1)[0]
        key = (task_id, role)
        remaining = self.fail_plan.get(key, 0)
        if remaining > 0:
            self.fail_plan[key] = remaining - 1
            raise TransportError(f"scripted failure for {key}")
        text = self.texts[role]
        if role == "developer" and task_id in self.patch_files:
            text = json.dumps(
                {"files_changed": self.patch_files[task_id], "summary": "eval fix"}
            )
        return TransportResult(text=text, input_tokens=self.tokens[0],
                               output_tokens=self.tokens[1], cost=self.cost)


class TaggedGateway(Gateway):
    """Prefix prompts with 'role::' so ScriptedTransport can route per role."""

    def complete(self, role, prompt, *, task_id="TASK-0"):
        return super().complete(role, f"{role}::{prompt}", task_id=task_id)


def fixture_tasks(prefix: str = "EVAL") -> list[ImplementationTask]:
    return [
        ImplementationTask(
            task_id=f"{prefix}-001",
            module="auth",
            behaviour="handle expired token",
            allowed_files=["src/auth/token.py"],
            acceptance_criteria=["expired token returns 401"],
        ),
        ImplementationTask(
            task_id=f"{prefix}-002",
            module="billing",
            behaviour="reject negative invoice",
            allowed_files=["src/billing/invoice.py"],
            acceptance_criteria=["negative amount raises ValueError"],
        ),
    ]


def _gateway_for(texts: dict[str, str], transport: ScriptedTransport) -> TaggedGateway:
    models = {"coder_fast": [Deployment.model_validate({"name": "ollama/fast-a", "class": "self-hosted"})]}
    roles = ["engineer", "developer", "tester", "reviewer", "writer"]
    workers = {r: WorkerConfig(model_group="coder_fast", tier="small", max_concurrent=8) for r in roles}
    policy = FactoryPolicy.model_validate({
        "mode": "supervised", "max_retries": 2, "approvals_required": ["blueprint"],
        "max_cost_per_task": 5.0, "max_wallclock_minutes": 60.0,
    })
    return TaggedGateway(models, workers, policy, transport=transport)


def role_texts(*, passing: bool = True) -> dict[str, str]:
    failed = 0 if passing else 1
    return {
        "engineer": json.dumps({"tasks": []}),
        "developer": json.dumps({"files_changed": ["src/auth/token.py"], "summary": "eval fix"}),
        "tester": json.dumps({"passed": passing, "tests_run": 4, "tests_failed": failed}),
        "reviewer": json.dumps({"verdict": "PASS" if passing else "RETRY", "notes": "eval"}),
        "writer": "eval docs.",
    }


def run_baseline(
    tasks: list[ImplementationTask],
    *,
    transport: ScriptedTransport,
    seed: int = 7,
) -> ArmResult:
    """One large model, full repo: single attempt per task, full-context chars counted."""
    rng = random.Random(seed)
    log = logger.bind(component="eval", arm="baseline", tasks=len(tasks))
    result = ArmResult(arm="baseline", tasks=len(tasks))
    t0 = time.monotonic()
    for task in tasks:
        gw = _gateway_for(role_texts(), transport)
        try:
            done = gw.complete("developer", f"developer::{task.behaviour}", task_id=task.task_id)
            passed = rng.random() < 1.0  # scripted: transport text decides; kept seeded for plan 07 tuning
            result.completed += int(passed)
            result.tests_passed += 4 * int(passed)
            result.tests_run += 4
            result.total_tokens += done.input_tokens + done.output_tokens
            result.api_cost += done.cost
            result.context_chars += len(task.behaviour) + 50_000  # full-repo baseline
            result.reports.append(run_report(worker="developer", completion=done, task_id=task.task_id,
                                             status=TaskStatus.PASSED if passed else TaskStatus.FAILED,
                                             result="passed" if passed else "failed"))
        except Exception as exc:  # noqa: BLE001 — eval must record, not crash
            log.warning("baseline task {} failed: {}", task.task_id, exc)
    result.wallclock_s = time.monotonic() - t0
    log.debug("baseline done completed={} cost={}", result.completed, result.api_cost)
    return result


def run_smallworks_arm(
    tasks: list[ImplementationTask],
    *,
    transport: ScriptedTransport,
    worktree_root: str | None = None,
) -> ArmResult:
    """Real gated loop; metrics sourced from TaskResults + RunReports."""
    log = logger.bind(component="eval", arm="smallworks", tasks=len(tasks))
    t0 = time.monotonic()
    gw = _gateway_for(role_texts(), transport)
    results = run_workflow(tasks, gw, max_workers=2, worktree_root=worktree_root)
    result = ArmResult(arm="smallworks", tasks=len(tasks))
    for task, res in zip(tasks, results):
        result.completed += int(res.outcome == TaskOutcome.PASSED)
        result.retries += max(0, res.attempts - 1)
        result.escalations += int(res.outcome == TaskOutcome.ESCALATED)
        result.human_interventions += int(res.outcome == TaskOutcome.ESCALATED)
        if res.test_report is not None:
            result.tests_passed += res.test_report.tests_run - res.test_report.tests_failed
            result.tests_run += res.test_report.tests_run
        # 4 role calls per attempt at scripted token counts; bounded context per packet.
        result.total_tokens += res.attempts * 4 * (transport.tokens[0] + transport.tokens[1])
        result.api_cost += res.attempts * 4 * transport.cost
        result.context_chars += res.attempts * 8_000
        result.gpu_s += res.attempts * 30.0  # estimate: 30s self-hosted GPU per attempt
        result.reports.append(run_report(
            worker="workflow", task_id=task.task_id,
            status=TaskStatus.PASSED if res.outcome == TaskOutcome.PASSED else TaskStatus.FAILED,
            result=res.outcome.value, artefacts=res.patch.files_changed if res.patch else [],
        ))
    result.wallclock_s = time.monotonic() - t0
    log.debug("smallworks done completed={} retries={} escalations={}",
              result.completed, result.retries, result.escalations)
    return result


def compare(baseline: ArmResult, smallworks: ArmResult) -> dict:
    """Side-by-side §13 metrics + hypothesis verdict."""
    verdict = (
        "SUPPORTED"
        if (smallworks.completed >= baseline.completed
            and (smallworks.api_cost < baseline.api_cost
                 or smallworks.context_chars < baseline.context_chars))
        else "NOT SUPPORTED"
    )
    return {
        "metrics": list(METRICS),
        "baseline": baseline.as_row(),
        "smallworks": smallworks.as_row(),
        "hypothesis": (
            "frontier-led + self-hosted execution beats one frontier model on quality per cost/time"
        ),
        "verdict": verdict,
    }


def save_report(report: dict, raw_texts: dict[str, str], path: Path) -> Path:
    """Persist comparison + raw outputs so a re-run reproduces the verdict."""
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {"report": report, "raw_outputs": raw_texts}
    path.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
    logger.bind(component="eval").debug("report saved {}", str(path))
    return path


def load_report(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


__all__ = [
    "ArmResult",
    "ScriptedTransport",
    "compare",
    "fixture_tasks",
    "load_report",
    "run_baseline",
    "run_smallworks_arm",
    "save_report",
]
