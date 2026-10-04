"""Context packets: fresh minimal per-task context (plan 03, spec §6).

Progressive disclosure order: summary → symbols → source → full file.
A char budget caps the packet; overflow truncates with ``ref:`` pointers so
raw content stays retrievable without bloating the worker's window.
Developer packets NEVER include a repo-wide dump (Repomix is
Architect/Engineer-only); pass ``include_repo_overview=True`` explicitly for
those roles.
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from smallworks.logging import logger
from smallworks.schemas import ContextPacket

DEFAULT_BUDGET_CHARS = 8_000


class PacketSource(BaseModel):
    """One candidate content layer, in disclosure order."""

    kind: str = Field(min_length=1)  # summary | symbols | source | file | repo_overview
    ref: str = Field(min_length=1)  # retrievable pointer, e.g. file path or artefact id
    content: str = ""


def _chars(items: list[str]) -> int:
    return sum(len(i) for i in items)


def build_packet(
    *,
    task_id: str,
    goal: str,
    module_contract: str,
    symbols: list[str] | None = None,
    related_tests: list[str] | None = None,
    coding_rules: list[str] | None = None,
    sources: list[PacketSource] | None = None,
    include_repo_overview: bool = False,
    budget_chars: int = DEFAULT_BUDGET_CHARS,
) -> tuple[ContextPacket, list[str]]:
    """Build a budget-fitting packet. Returns (packet, dropped_refs).

    Layers fill in order; when the next layer would overflow, it is skipped
    and its ``ref:`` pointer is recorded so the worker can request it.
    """
    log = logger.bind(component="context", task_id=task_id)
    symbols = list(symbols or [])
    related_tests = list(related_tests or [])
    coding_rules = list(coding_rules or [])
    layers = list(sources or [])
    if not include_repo_overview:
        dropped_repo = [s.ref for s in layers if s.kind == "repo_overview"]
        if dropped_repo:
            log.debug("developer packet: repo_overview excluded refs={}", dropped_repo)
        layers = [s for s in layers if s.kind != "repo_overview"]

    used = len(goal) + len(module_contract) + _chars(symbols) + _chars(related_tests) + _chars(coding_rules)
    log.debug(
        "base chars={} budget={} layers={}",
        used,
        budget_chars,
        [(s.kind, s.ref) for s in layers],
    )
    kept_symbols = list(symbols)
    dropped_refs: list[str] = []
    # Progressive disclosure: whole layers first, then tail-trim list fields.
    for layer in layers:
        if layer.kind in {"symbols", "source", "file", "repo_overview"}:
            target = kept_symbols
        elif layer.kind == "summary":
            target = None  # summaries always fit by construction; record only
        else:
            target = None
        if target is not None and used + len(layer.content) <= budget_chars:
            target.append(f"[{layer.kind}:{layer.ref}] {layer.content}")
            used += len(layer.content)
            log.debug("kept layer {} ref={} chars={}", layer.kind, layer.ref, len(layer.content))
        elif target is not None:
            dropped_refs.append(f"ref:{layer.ref}")
            log.debug("dropped layer {} ref={} chars={}", layer.kind, layer.ref, len(layer.content))
    # Tail-trim symbols/tests/rules lists if still over budget (cheapest last).
    while used > budget_chars and (kept_symbols or related_tests or coding_rules):
        if coding_rules:
            dropped = coding_rules.pop()
            used -= len(dropped)
            dropped_refs.append(f"ref:rule:{dropped[:40]}")
        elif related_tests:
            dropped = related_tests.pop()
            used -= len(dropped)
            dropped_refs.append(f"ref:test:{dropped[:40]}")
        elif kept_symbols:
            dropped = kept_symbols.pop()
            used -= len(dropped)
            dropped_refs.append(f"ref:symbol:{dropped[:40]}")
    packet = ContextPacket(
        task_id=task_id,
        goal=goal,
        module_contract=module_contract,
        relevant_symbols=kept_symbols,
        related_tests=related_tests,
        coding_rules=coding_rules,
    )
    log.debug("packet built chars={} dropped={}", used, dropped_refs)
    return packet, dropped_refs
