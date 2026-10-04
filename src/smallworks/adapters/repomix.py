"""Repomix adapter: repo-level overview for Architect/Engineer ONLY (spec §7).

MUST NOT be injected into Developer packets by default — see
``context.build_packet`` which strips ``repo_overview`` layers unless the
caller passes ``include_repo_overview=True``.
"""

from __future__ import annotations

from smallworks.context import PacketSource
from smallworks.logging import logger


def repo_overview(repo: str, *, modules: list[str]) -> PacketSource:
    """Compressed repo structure stub: module names, not file contents."""
    log = logger.bind(component="repomix", repo=repo)
    content = "repo " + repo + " modules: " + ", ".join(modules)
    log.debug("overview modules={}", len(modules))
    return PacketSource(kind="repo_overview", ref=f"repomix:{repo}", content=content)
