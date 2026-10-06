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
        f"GOAL: turn this idea into a buildable Blueprint: {idea}.\n"
        f"CONTEXT (repo as it stands — respect boundaries, reuse modules):\n{overview or '(no overview — greenfield design)'}\n"
        "RULES: 2-5 modules, each with ONE responsibility; name dependencies "
        "explicitly; stdlib-only unless the idea demands otherwise. "
        f"{JSON_ONLY} Shape: "
        '{"project": "<name>", "modules": [{"name": "<m>", "responsibility": "...", '
        '"interface": "...", "acceptance_criteria": ["..."]}], "dependencies": ["a -> b"], '
        '"acceptance_criteria": ["..."]}. Dependencies are STRINGS like "cache -> store", '
        "never objects. "
        "Module interfaces pin function/class names, signatures, and error "
        "behaviour — the Engineer will treat them as binding."
    )


def engineer_prompt(module: ModuleSpec) -> str:
    acc = "\n".join(f"- {c}" for c in module.acceptance_criteria)
    return (
        f"GOAL: break module {module.name} into bounded implementation tasks one developer call can finish.\n"
        f"CONTRACT (the Architect's binding interface — every task must serve it):\n{module.interface}\n"
        f"RESPONSIBILITY: {module.responsibility}.\n"
        f"ACCEPTANCE (each criterion needs at least one task that proves it):\n{acc}\n"
        "RULES: task_ids like RETRY-001 (letters, dash, at least 3 digits — "
        "RETRY-1 is invalid); each task touches 1-2 files under src/smallworks/; "
        "allowed_files MUST be concrete paths; acceptance_criteria MUST be executable checks. "
        "Do NOT redesign the CONTRACT: task behaviour MUST call the interface as specified. "
        "Every task MUST include its test file in allowed_files "
        "(tests/test_<module>.py, created or extended) — a task with no runnable "
        "tests can never pass verification. "
        f"{JSON_ONLY} Shape: "
        '{"tasks": [{"task_id": "RETRY-001", "module": "<name>", "behaviour": "...", '
        '"allowed_files": ["src/..."], "acceptance_criteria": ["..."]}]}.'
    )


def developer_prompt(
    task: ImplementationTask,
    symbols: list[str] | None = None,
    existing: dict[str, str] | None = None,
    feedback: str | None = None,
) -> str:
    """Context Packet for the developer: goal + contract + symbols + tests + rules.

    Every section is load-bearing: GOAL scopes the change, CONTRACT pins the
    public API (names, types, errors), SYMBOLS names what's already available,
    TESTS lists the exact cases the hidden suite checks, RULES pins shape and
    determinism, FILES gives HEAD content to edit from (or names what's absent).
    """
    syms = "; ".join(symbols or []) or "(stdlib only — no repo symbols available)"
    tests = "\n".join(f"- {c}" for c in task.acceptance_criteria) or "- (none)"
    files = "\n".join(f"--- {path} ---\n{text}" for path, text in (existing or {}).items())
    if not files:
        files = "(no in-scope files exist yet — create them)"
    return (
        f"GOAL: implement task {task.task_id} in module {task.module}: {task.behaviour}.\n"
        f"CONTRACT (public API — names, signatures, and errors are binding):\n{tests}\n"
        f"SYMBOLS available: {syms}.\n"
        f"TESTS the hidden suite will run (every one must pass):\n{tests}\n"
        f"RULES: touch ONLY these files: {', '.join(task.allowed_files)}. "
        f"Stdlib only, no new dependencies. {JSON_ONLY} Shape: "
        '{"files_changed": ["src/..."], "summary": "<one line>", '
        '"contents": {"<path>": "<COMPLETE new file text>"}}. '
        "contents MUST hold every changed file in full; an unwritten file fails verification.\n"
        f"FILES (HEAD content — edit from this, keep unrelated code intact):\n{files}"
        + (
            f"\nREVISE: your previous attempt failed verification with this measured result:\n{feedback}\n"
            "Fix ONLY what the failure names; keep everything that passed intact. "
            "Emit the COMPLETE corrected files again."
            if feedback else ""
        )
    )


def tester_prompt(task: ImplementationTask, patch: Patch) -> str:
    files = "\n".join(
        f"--- {path} ---\n{text}" for path, text in patch.contents.items()
    ) or "(no file contents — names only)"
    acc = "\n".join(f"- {c}" for c in task.acceptance_criteria)
    return (
        f"GOAL: independently verify task {task.task_id}: {task.behaviour}.\n"
        f"ACCEPTANCE (every criterion must hold — test each one mentally):\n{acc}\n"
        "RULES: read the code below line by line; report what the code DOES, "
        "not what the summary claims. Edge cases (empty input, wrong types, "
        "boundary attempts) count as failures if unhandled. "
        f"{JSON_ONLY} Shape: "
        '{"passed": true, "tests_run": 3, "tests_failed": 0}\n'
        f"CHANGED FILES ({patch.summary}):\n{files}"
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
