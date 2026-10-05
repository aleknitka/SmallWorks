"""Pi worker prompts per role (plan 04, spec §3).

The orchestrator routes work and MUST NOT write implementation code — there is
deliberately no code-writing prompt for it. Every code-writing prompt demands a
single JSON object so output parses deterministically into artefacts.
"""

from __future__ import annotations

from smallworks.schemas import ImplementationTask, ModuleSpec, Patch, TestReport

JSON_ONLY = "Reply with exactly one JSON object, no prose, no fences."


def architect_prompt(idea: str, overview: str) -> str:
    return (
        "Turn this idea into a Blueprint: modules, boundaries, dependencies, "
        "interfaces, constraints, acceptance criteria. "
        f"{JSON_ONLY} Shape: "
        '{"project": "<name>", "modules": [{"name": "<m>", "responsibility": "...", '
        '"interface": "...", "acceptance_criteria": ["..."]}], "dependencies": [], '
        '"acceptance_criteria": ["..."]}. '
        f"Idea: {idea}. Repo overview: {overview}"
    )


def engineer_prompt(module: ModuleSpec) -> str:
    return (
        "Break this Blueprint module into bounded implementation tasks. "
        '{"tasks": [{"task_id": "PREFIX-1", "module": "<name>", "behaviour": "...", '
        '"allowed_files": ["src/..."], "acceptance_criteria": ["..."]}]}. '
        f"Module: {module.name}: {module.responsibility}. "
        f"Interface: {module.interface}. "
        f"Acceptance: {'; '.join(module.acceptance_criteria)}"
    )


def developer_prompt(task: ImplementationTask, symbols: list[str] | None = None) -> str:
    syms = "; ".join(symbols or [])
    return (
        f"Implement task {task.task_id} in module {task.module}: {task.behaviour}. "
        f"Touch ONLY these files: {', '.join(task.allowed_files)}. "
        f"Acceptance: {'; '.join(task.acceptance_criteria)}. "
        f"Relevant symbols: {syms}. {JSON_ONLY} Shape: "
        '{"files_changed": ["src/..."], "summary": "<one line>"}'
    )


def tester_prompt(task: ImplementationTask, patch: Patch) -> str:
    return (
        f"Independently verify task {task.task_id} ({task.behaviour}). "
        f"Changed files: {', '.join(patch.files_changed)}. "
        f"Acceptance: {'; '.join(task.acceptance_criteria)}. "
        f"{JSON_ONLY} Shape: "
        '{"passed": true, "tests_run": 3, "tests_failed": 0}'
    )


def reviewer_prompt(task: ImplementationTask, patch: Patch, report: TestReport) -> str:
    return (
        f"Review task {task.task_id}: does it satisfy the plan, respect "
        f"architecture, and meet acceptance? Changed: {', '.join(patch.files_changed)} "
        f"({patch.summary}). Tests: passed={report.passed} "
        f"run={report.tests_run} failed={report.tests_failed}. "
        f"{JSON_ONLY} Shape: "
        '{"verdict": "PASS" | "RETRY" | "ESCALATE", "notes": "<one line>"}'
    )


def writer_prompt(task: ImplementationTask, patch: Patch) -> str:
    return (
        f"Write docstrings/change notes for task {task.task_id} "
        f"({patch.summary}). Files: {', '.join(patch.files_changed)}. "
        "One short paragraph, no prose beyond what ships in the docs."
    )
