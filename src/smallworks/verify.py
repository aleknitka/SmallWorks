"""Executed test verification: run pytest, measure, override model claims (spec §8).

A TestReport built from model text alone is a claim. These helpers execute the
suite for a task's test files and return measured results; ``tester_task`` uses
them to override the model's numbers in both directions. Zero runnable tests is
itself a failure — an unverifiable done-claim cannot pass.
"""

from __future__ import annotations

import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

from smallworks.logging import logger
from smallworks.schemas import ImplementationTask

DEFAULT_TIMEOUT_S = 120.0


@dataclass(frozen=True)
class ExecutedTests:
    passed: bool
    tests_run: int
    tests_failed: int
    summary: str  # one compressed line for the report/log
    command: str


def tests_for_task(task: ImplementationTask, root: Path) -> list[Path]:
    """On-disk test files covering ``task``: allowed test files + module convention."""
    found: list[Path] = []
    for name in task.allowed_files:
        rel = name.replace("\\", "/")
        candidate = root / name
        if (
            candidate.suffix == ".py"
            and ("tests/" in rel or Path(rel).name.startswith("test_"))
            and candidate.is_file()
        ):
            found.append(candidate)
    conventional = root / "tests" / f"test_{task.module}.py"
    if conventional.is_file() and conventional not in found:
        found.append(conventional)
    return found


def run_pytest(paths: list[Path], *, cwd: Path, timeout_s: float = DEFAULT_TIMEOUT_S) -> ExecutedTests:
    """Run pytest over ``paths``; parse measured counts from the summary line."""
    command = [sys.executable, "-m", "pytest", *[str(p) for p in paths], "-q", "-p", "no:cacheprovider"]
    cmd_str = " ".join(command)
    try:
        proc = subprocess.run(command, capture_output=True, text=True, timeout=timeout_s, cwd=str(cwd))
    except subprocess.TimeoutExpired:
        log = logger.bind(component="verify", command=cmd_str)
        log.warning("pytest timed out after {}s", timeout_s)
        return ExecutedTests(False, 0, 0, f"timed out after {timeout_s}s: {cmd_str}", cmd_str)
    except OSError as exc:
        logger.bind(component="verify", command=cmd_str).error("pytest launch failed: {}", exc)
        return ExecutedTests(False, 0, 0, f"pytest unavailable: {exc}", cmd_str)
    tail = "\n".join(line for line in proc.stdout.splitlines() if line.strip())[-1500:]
    summary = tail.splitlines()[-1][:200] if tail.splitlines() else f"exit={proc.returncode}"
    passed_n = sum(int(n) for n in re.findall(r"(\d+) passed", tail))
    failed_n = sum(int(n) for n in re.findall(r"(\d+) failed", tail))
    error_n = sum(int(n) for n in re.findall(r"(\d+) error", tail))
    ran = passed_n + failed_n + error_n
    passed = proc.returncode == 0 and failed_n == 0 and error_n == 0 and passed_n > 0
    logger.bind(component="verify", command=cmd_str).debug(
        "exit={} passed={} failed={} errors={} summary={}", proc.returncode, passed_n, failed_n, error_n, summary
    )
    return ExecutedTests(passed, ran, failed_n + error_n, summary, cmd_str)


def verify_task_tests(
    task: ImplementationTask, root: Path, *, timeout_s: float = DEFAULT_TIMEOUT_S
) -> ExecutedTests | None:
    """Execute the task's tests; None when no runnable test file exists."""
    paths = tests_for_task(task, root)
    if not paths:
        logger.bind(component="verify", task_id=task.task_id).warning(
            "no executable tests for module {}", task.module
        )
        return None
    return run_pytest(paths, cwd=root, timeout_s=timeout_s)
