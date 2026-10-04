"""Tool adapters: thin interfaces over Serena/Repomix/RTK/Caveman (plan 03, spec §7)."""

from smallworks.adapters.caveman import caveman_report
from smallworks.adapters.repomix import repo_overview
from smallworks.adapters.rtk import RecallStore, compress_output, recall_output
from smallworks.adapters.serena import SerenaResult, query_serena

__all__ = [
    "SerenaResult",
    "query_serena",
    "repo_overview",
    "compress_output",
    "recall_output",
    "RecallStore",
    "caveman_report",
]
