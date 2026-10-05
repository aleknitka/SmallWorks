"""Role workers: typed Pi-callable units (plan 04, spec §3, §9).

Each role is one small function with an explicit contract:
parsed model text in, artefact out. Parsing is strict — free-form prose never
flows downstream. Roles:

- engineer: Blueprint module -> ImplementationTasks (self-hosted medium).
- developer: bounded task -> Patch, allowed-files only, no whole-repo context.
- tester: task + patch -> independent TestReport (derives checks from criteria).
- reviewer: task + patch + report -> PASS/RETRY/ESCALATE verdict.
- writer: task + patch -> docstring/change-note text.
- orchestrator: routes only, NEVER writes implementation code (no prompt here).
"""

from __future__ import annotations

import json

from pydantic import ValidationError

from smallworks.config import WorkerConfig
from smallworks.gateway import Gateway
from smallworks.logging import logger
from smallworks.schemas import (
    Blueprint,
    ImplementationTask,
    ModuleSpec,
    Patch,
    ReviewReport,
    TestReport,
)
from smallworks.workers import prompts


class WorkerError(RuntimeError):
    """Role worker failed: transport exhausted, budget breach, or bad JSON."""


def _ask(gateway: Gateway, role: str, prompt: str, *, task_id: str) -> tuple[str, str, str]:
    """Run one gateway call; return (text, deployment, provider)."""
    done = gateway.complete(role, prompt, task_id=task_id)
    logger.bind(
        component="workers",
        role=role,
        task_id=task_id,
        deployment=done.deployment,
        provider=done.provider,
    ).debug("role completion chars={} attempts={}", len(done.text), done.attempts)
    return done.text, done.deployment, done.provider


def _parse_json(text: str, *, role: str, task_id: str) -> dict:
    """Parse one JSON object, tolerating fences/prose small models wrap it in.

    Tries bare parse, fenced block, then first balanced {...}; downstream
    schema validation still rejects wrong shapes — this only strips packaging.
    """
    log = logger.bind(component="workers", role=role, task_id=task_id)
    candidates = [text]
    stripped = text.strip()
    if stripped.startswith("```"):
        lines = stripped.splitlines()
        fenced = "\n".join(lines[1:-1] if lines[-1].startswith("```") else lines[1:])
        candidates.append(fenced)
    start = text.find("{")
    if start >= 0:
        depth, end = 0, -1
        for i in range(start, len(text)):
            if text[i] == "{":
                depth += 1
            elif text[i] == "}":
                depth -= 1
                if depth == 0:
                    end = i + 1
                    break
        if end > start:
            candidates.append(text[start:end])
    for candidate in candidates:
        try:
            data = json.loads(candidate)
        except json.JSONDecodeError:
            continue
        if isinstance(data, dict):
            return data
    log.error("unparseable {} output", role)
    raise WorkerError(f"{role} returned unparseable JSON")


def engineer_task(module: ModuleSpec, gateway: Gateway, worker: WorkerConfig) -> list[ImplementationTask]:
    del worker  # tier/concurrency already enforced by the gateway + config
    log = logger.bind(component="workers", role="engineer", module=module.name)
    data = _parse_json(
        _ask(gateway, "engineer", prompts.engineer_prompt(module), task_id="PLAN-0")[0],
        role="engineer",
        task_id="PLAN-0",
    )
    raw_tasks = data.get("tasks")
    if not isinstance(raw_tasks, list) or not raw_tasks:
        raise WorkerError("engineer must return a non-empty tasks list")
    try:
        tasks = [ImplementationTask.model_validate(t) for t in raw_tasks]
    except ValidationError as exc:
        log.error("engineer tasks invalid: {}", exc)
        raise WorkerError(f"engineer tasks invalid: {exc}") from exc
    log.debug("planned {} tasks", len(tasks))
    return tasks


def developer_task(
    task: ImplementationTask, gateway: Gateway, *, symbols: list[str] | None = None,
    existing: dict[str, str] | None = None, feedback: str | None = None,
) -> Patch:
    log = logger.bind(component="workers", role="developer", task_id=task.task_id)
    prompt = prompts.developer_prompt(task, symbols, existing, feedback)
    log.debug("developer prompt chars={} existing_files={} revise={}",
              len(prompt), sorted((existing or {})), bool(feedback))
    data = _parse_json(
        _ask(gateway, "developer", prompt, task_id=task.task_id)[0],
        role="developer",
        task_id=task.task_id,
    )
    try:
        patch = Patch(task_id=task.task_id, **{k: v for k, v in data.items() if k != "task_id"})
    except ValidationError as exc:
        log.error("developer patch invalid: {}", exc)
        raise WorkerError(f"developer patch invalid: {exc}") from exc
    outside = [f for f in patch.files_changed if f not in task.allowed_files]
    if outside:
        log.warning("patch touches files outside scope: {}", outside)
        raise WorkerError(f"developer touched out-of-scope files: {outside}")
    log.debug("patch files={}", patch.files_changed)
    return patch


def tester_task(
    task: ImplementationTask, patch: Patch, gateway: Gateway, *, verify: bool = False,
    root: object = None,
) -> TestReport:
    """Tester: model drafts the report, execution overrides it (spec §8).

    With ``verify=True`` (live runs, eval arm) the task's test files actually
    execute: the measured result overrides the model's numbers in BOTH
    directions, and zero runnable tests forces failure — an LLM cannot claim
    done when nothing ran. Unit tests keep ``verify=False`` (scripted models,
    no repo files asserted).
    """
    log = logger.bind(component="workers", role="tester", task_id=task.task_id)
    data = _parse_json(
        _ask(gateway, "tester", prompts.tester_prompt(task, patch), task_id=task.task_id)[0],
        role="tester",
        task_id=task.task_id,
    )
    try:
        report = TestReport(task_id=task.task_id, **{k: v for k, v in data.items() if k != "task_id"})
    except ValidationError as exc:
        log.error("tester report invalid: {}", exc)
        raise WorkerError(f"tester report invalid: {exc}") from exc
    if verify:
        report = _verify_report(task, report, root)
    log.debug("tests passed={} failed={}", report.passed, report.tests_failed)
    return report


def _verify_report(task: ImplementationTask, report: TestReport, root: object = None) -> TestReport:
    """Replace model-claimed numbers with measured pytest results (or fail).

    ``root`` is the worktree holding the applied patch; without it (unit-test
    temps with no repo) the claim is unverifiable and fails closed.
    """
    from pathlib import Path

    from smallworks.verify import verify_task_tests

    log = logger.bind(component="workers", role="tester", task_id=task.task_id)
    exec_root = Path(str(root)) if root is not None else None
    if exec_root is None or not exec_root.is_dir():
        log.warning("no execution root; treating claimed report as unverifiable")
        return TestReport(task_id=task.task_id, passed=False, tests_run=0, tests_failed=0)
    measured = verify_task_tests(task, exec_root)
    if measured is None:
        return TestReport(task_id=task.task_id, passed=False, tests_run=0, tests_failed=0)
    if measured.passed != report.passed:
        log.warning("model claimed passed={} but execution says {}; overriding",
                    report.passed, measured.passed)
    return TestReport(task_id=task.task_id, passed=measured.passed,
                      tests_run=measured.tests_run, tests_failed=measured.tests_failed)


def reviewer_task(task: ImplementationTask, patch: Patch, report: TestReport, gateway: Gateway) -> ReviewReport:
    log = logger.bind(component="workers", role="reviewer", task_id=task.task_id)
    data = _parse_json(
        _ask(
            gateway,
            "reviewer",
            prompts.reviewer_prompt(task, patch, report),
            task_id=task.task_id,
        )[0],
        role="reviewer",
        task_id=task.task_id,
    )
    try:
        review = ReviewReport(
            task_id=task.task_id, **{k: v for k, v in data.items() if k != "task_id"}
        )
    except ValidationError as exc:
        log.error("reviewer verdict invalid: {}", exc)
        raise WorkerError(f"reviewer verdict invalid: {exc}") from exc
    log.debug("verdict={}", review.verdict)
    return review


def writer_task(task: ImplementationTask, patch: Patch, gateway: Gateway) -> str:
    text, _, _ = _ask(gateway, "writer", prompts.writer_prompt(task, patch), task_id=task.task_id)
    return text.strip()


def architect_task(idea: str, gateway: Gateway, *, overview: str = "") -> Blueprint:
    """Frontier Architect: idea + Repomix overview -> validated Blueprint (plan 07)."""
    log = logger.bind(component="workers", role="architect")
    data = _parse_json(
        _ask(gateway, "architect", prompts.architect_prompt(idea, overview), task_id="PLAN-0")[0],
        role="architect",
        task_id="PLAN-0",
    )
    try:
        blueprint = Blueprint.model_validate(data)
    except ValidationError as exc:
        log.error("architect blueprint invalid: {}", exc)
        raise WorkerError(f"architect blueprint invalid: {exc}") from exc
    log.debug("blueprint project={} modules={}", blueprint.project, len(blueprint.modules))
    return blueprint


__all__ = [
    "WorkerError",
    "architect_task",
    "developer_task",
    "engineer_task",
    "reviewer_task",
    "tester_task",
    "writer_task",
]
