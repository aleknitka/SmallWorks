"""Board sync: GitHub Issues + Projects surface (plan 05, spec §10).

The board is GitHub, not a custom dashboard. This module renders a
``BoardRecord`` to an Issue body and syncs via the ``gh`` CLI so no Python
GitHub dependency is needed. No network in tests: ``to_issue_body`` is pure;
``sync_record`` shells out and returns the issue URL (or raises).
"""

from __future__ import annotations

import subprocess

from smallworks.logging import logger
from smallworks.supervision import BoardRecord


def to_issue_body(record: BoardRecord) -> str:
    lines = [
        f"Task: {record.task_id}",
        f"Status: {record.status}",
        f"Role: {record.role or '-'}",
        f"Model: {record.model or '-'}",
        f"Attempt: {record.attempt}",
        f"Tests: {record.test_status}",
        f"Latest: {record.latest_report or '-'}",
        f"Artefacts: {', '.join(record.artefacts) or '-'}",
        f"Cost/tokens: {record.cost_tokens or '-'}",
        f"Dependencies: {', '.join(record.dependencies) or '-'}",
    ]
    return "\n".join(lines) + "\n"


def sync_payload_v2(record: BoardRecord, *, phase2) -> dict:
    """Richer Projects payload: verdict, tier, cost/context numbers (plan 07).

    Requires the ``board_sync_v2`` flag. Pure — the ``gh``/Projects call that
    ships the payload stays in the sync script, same split as ``sync_record``.
    """
    from smallworks.phase2 import Phase2Disabled

    if not phase2.board_sync_v2:
        raise Phase2Disabled("board_sync_v2 flag is off")
    return {
        "task": record.task_id,
        "status": record.status,
        "role": record.role,
        "model": record.model,
        "attempt": record.attempt,
        "tests": record.test_status,
        "latest": record.latest_report,
        "artefacts": list(record.artefacts),
        "cost_tokens": record.cost_tokens,
        "dependencies": list(record.dependencies),
    }


def sync_record(record: BoardRecord, *, repo: str, project: str | None = None) -> str:
    """Create/update a GitHub Issue for ``record`` via ``gh``. Returns issue URL."""
    log = logger.bind(component="board", task_id=record.task_id, repo=repo)
    body = to_issue_body(record)
    cmd = [
        "gh", "issue", "create",
        "--repo", repo,
        "--title", f"[{record.task_id}] {record.status}",
    ]
    log.debug("syncing board record status={}", record.status)
    try:
        out = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
    except OSError as exc:
        log.error("gh CLI unavailable: {}", exc)
        raise RuntimeError(f"gh CLI unavailable: {exc}") from exc
    if out.returncode != 0:
        log.error("gh issue create failed: {}", out.stderr.strip())
        raise RuntimeError(f"gh issue create failed: {out.stderr.strip()}")
    url = out.stdout.strip()
    log.info("board synced {}", url)
    if project:
        log.debug("project linking left to Projects automation (project={})", project)
    return url
