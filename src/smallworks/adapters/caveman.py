"""Caveman adapter: concise structured worker reports, no prose (spec §7)."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from smallworks.logging import logger


class CavemanReport(BaseModel):
    status: Literal["passed", "failed", "blocked"]
    reason: str = Field(min_length=1)
    suspected_file: str | None = None
    next: str = Field(min_length=1)


def caveman_report(
    status: Literal["passed", "failed", "blocked"],
    reason: str,
    next_step: str,
    *,
    suspected_file: str | None = None,
) -> CavemanReport:
    report = CavemanReport(status=status, reason=reason, suspected_file=suspected_file, next=next_step)
    logger.bind(component="caveman", status=status, next=next_step).debug("report: {}", reason)
    return report
