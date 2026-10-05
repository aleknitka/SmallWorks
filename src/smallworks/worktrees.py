"""Git worktree isolation: one task, one directory (plan 04, spec §14).

Setup runs before Developer, teardown after the decision. Falls back to a
plain temp directory when git is unavailable (tests, non-repo runs) — the
contract (isolated dir in, cleanup after) holds either way.
"""

from __future__ import annotations

import shutil
import subprocess
import tempfile
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from smallworks.logging import logger


class WorktreeError(RuntimeError):
    pass


def _git_root() -> Path | None:
    try:
        out = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            capture_output=True,
            text=True,
            timeout=10,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if out.returncode != 0:
        return None
    return Path(out.stdout.strip())


@contextmanager
def worktree_for(task_id: str, *, root: str | None = None, branch: str | None = None) -> Iterator[Path]:
    """Yield an isolated dir for ``task_id``; remove it on exit.

    Real git worktree when inside a repo, temp dir otherwise. ``root`` pins the
    parent directory (tests use a tmp_path); ``branch`` names the worktree
    branch (defaults to ``sw/<task_id>``).
    """
    log = logger.bind(component="worktree", task_id=task_id)
    name = branch or f"sw/{task_id}"
    git_top = _git_root()
    if git_top is not None and root is None:
        target = git_top.parent / f"{git_top.name}-{task_id.lower()}"
        log.debug("creating git worktree {} at {}", name, target)
        proc = subprocess.run(
            ["git", "-C", str(git_top), "worktree", "add", "--detach", str(target)],
            capture_output=True,
            text=True,
            timeout=60,
        )
        if proc.returncode != 0 and target.exists():
            # Stale dir from a killed run (admin entry pruned, files left):
            # clear once and retry instead of failing the task.
            log.warning("clearing stale worktree dir {}", target)
            shutil.rmtree(target, ignore_errors=True)
            subprocess.run(
                ["git", "-C", str(git_top), "worktree", "prune"],
                capture_output=True, text=True, timeout=30,
            )
            proc = subprocess.run(
                ["git", "-C", str(git_top), "worktree", "add", "--detach", str(target)],
                capture_output=True,
                text=True,
                timeout=60,
            )
        if proc.returncode != 0:
            log.error("git worktree add failed: {}", proc.stderr.strip())
            raise WorktreeError(f"git worktree add failed: {proc.stderr.strip()}")
        try:
            yield target
        finally:
            log.debug("removing git worktree {}", target)
            subprocess.run(
                ["git", "-C", str(git_top), "worktree", "remove", "--force", str(target)],
                capture_output=True,
                text=True,
                timeout=60,
            )
        return
    parent = Path(root) if root else Path(tempfile.gettempdir())
    parent.mkdir(parents=True, exist_ok=True)
    tmp = Path(tempfile.mkdtemp(prefix=f"sw-{task_id.lower()}-", dir=str(parent)))
    log.debug("using temp isolation dir {} (git repo: {})", tmp, git_top is not None)
    try:
        yield tmp
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
        log.debug("removed isolation dir {}", tmp)
