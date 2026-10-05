"""Phase-2 extensions, all flag-gated (plan 07, spec §15).

With every flag off the MVP path (plans 01–06) runs unchanged: no caller in
the MVP loop touches this module. Each flag maps to one plan-07 item so the
eval-delta of each item can be measured in the plan-06 harness before it
becomes default. Flags load from the optional ``phase2`` mapping in
``factory.yaml`` overlaid with the ``SMALLWORKS_PHASE2`` env var (comma
list, e.g. ``architect,release_gate``); unknown names fail fast.

Entries that *act* (architect run, bounded routing, tier escalation, release
merge) raise ``Phase2Disabled`` when their flag is off. Pure inert helpers
(ranking, recall, field renderers, delta math) carry no flag check — the flag
gates their call sites, exactly like the MVP adapters.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, Field

from smallworks.logging import logger

Tier = Literal["small", "medium", "large"]
_TIER_ORDER: tuple[Tier, ...] = ("small", "medium", "large")

FLAG_NAMES = (
    "architect",
    "decision_routing",
    "dynamic_escalation",
    "cost_aware_routing",
    "benchmarking",
    "context_optimisation",
    "semantic_retrieval",
    "release_gate",
    "board_sync_v2",
    "event_stream",
)


class Phase2Disabled(RuntimeError):
    """Extension called while its flag is off; MVP path must not call it."""


class NeedsHuman(RuntimeError):
    """Dynamic escalation exhausted the largest tier; a human decides."""


class Phase2Flags(BaseModel):
    """One bool per plan-07 item; all off by default (MVP unchanged)."""

    architect: bool = False
    decision_routing: bool = False
    dynamic_escalation: bool = False
    cost_aware_routing: bool = False
    benchmarking: bool = False
    context_optimisation: bool = False
    semantic_retrieval: bool = False
    release_gate: bool = False
    board_sync_v2: bool = False
    event_stream: bool = False

    def enabled(self, name: str) -> bool:
        if name not in FLAG_NAMES:
            raise ValueError(f"unknown phase-2 flag {name!r}")
        return bool(getattr(self, name))

    def any_on(self) -> bool:
        return any(bool(getattr(self, name)) for name in FLAG_NAMES)


def _parse_list(raw: str) -> list[str]:
    return [p.strip().lower() for p in raw.split(",") if p.strip()]


def flags_from_env(env: dict[str, str] | None = None) -> Phase2Flags:
    """Parse ``SMALLWORKS_PHASE2`` (comma list); unknown names fail fast."""
    src = env if env is not None else os.environ
    names = _parse_list(src.get("SMALLWORKS_PHASE2", ""))
    unknown = [n for n in names if n not in FLAG_NAMES]
    if unknown:
        raise ValueError(f"unknown phase-2 flag(s): {', '.join(unknown)}")
    return Phase2Flags(**{n: True for n in names})


def load_phase2(path: Path | None = None) -> Phase2Flags:
    """Load flags from ``factory.yaml`` ``phase2`` mapping, env overlaid.

    Missing file/key ⇒ all off. File says on + env lists a flag ⇒ on; env
    cannot turn a file-enabled flag back off (explicit is safer than silent).
    """
    merged: dict[str, bool] = {}
    cfg_path = path or (Path(__file__).resolve().parents[2] / "configs" / "factory.yaml")
    try:
        data = yaml.safe_load(cfg_path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        data = {}
    if isinstance(data, dict):
        raw = data.get("factory", {})
        if isinstance(raw, dict):
            phase2 = raw.get("phase2", {})
            if phase2 is None:
                phase2 = {}
            if not isinstance(phase2, dict):
                raise ValueError(f"config {cfg_path} 'factory.phase2' must be a mapping")
            for name, value in phase2.items():
                if name not in FLAG_NAMES:
                    raise ValueError(f"config {cfg_path} unknown phase-2 flag {name!r}")
                merged[name] = bool(value)
    try:
        env_flags = flags_from_env()
    except ValueError:
        raise
    for name in FLAG_NAMES:
        if getattr(env_flags, name):
            merged[name] = True
    flags = Phase2Flags(**merged)
    logger.bind(component="phase2").debug(
        "flags on={}", [n for n in FLAG_NAMES if getattr(flags, n)]
    )
    return flags


# --- 1. Architect (frontier): idea -> Blueprint, human approves ---


def architect_approval_gate() -> str:
    """Blueprint approval is always the ``blueprint`` human gate (spec §10)."""
    return "blueprint"


def run_architect(idea: str, gateway, overview: str, flags: Phase2Flags):
    """Run the Architect role; requires the ``architect`` flag.

    Takes a free-text idea plus a Repomix repo overview and returns a
    validated ``Blueprint``. Approval stays human: feed the result through
    the ``blueprint`` gate before any Engineer work starts.
    """
    from smallworks.workers.roles import architect_task

    if not flags.architect:
        raise Phase2Disabled("architect flag is off")
    log = logger.bind(component="phase2", role="architect")
    log.debug("architect idea_chars={} overview_chars={}", len(idea), len(overview))
    return architect_task(idea, gateway, overview=overview)


# --- 2. Bounded decision routing (Clef/Jev placeholder, overrides rule) ---

BoundedChoice = Literal[
    "next_developer",
    "next_tester",
    "next_reviewer",
    "retry",
    "escalate",
    "accept",
    "needs_architect",
    "needs_human",
]


def route_choice(
    scores: dict[str, float],
    *,
    deterministic: str | None = None,
    flags: Phase2Flags,
) -> str:
    """Pick the highest-scoring bounded choice; deterministic wins outright.

    ``deterministic`` carries the verdict of ``validate_decision``/gates.
    Model scores never override it — Clef/Jev (or any future router) only
    decides among the options the gates leave open.
    """
    if not flags.decision_routing:
        raise Phase2Disabled("decision_routing flag is off")
    if deterministic is not None:
        logger.bind(component="phase2", route="bounded").debug(
            "deterministic override {} beats scores {}", deterministic, scores
        )
        return deterministic
    if not scores:
        raise ValueError("route_choice needs a non-empty scores mapping")
    choice = max(scores, key=lambda k: scores[k])
    logger.bind(component="phase2", route="bounded").debug("routed {} scores={}", choice, scores)
    return choice


def assess_complexity(*, chars: int, files: int) -> Tier:
    """Length/span heuristic for tier escalation input (documented limits).

    Borders are deliberately crude — exact thresholds are plan-07 tuning
    material, measured against the eval harness, not hand-tuned here.
    """
    if chars > 20_000 or files > 10:
        return "large"
    if chars > 6_000 or files > 3:
        return "medium"
    return "small"


# --- 3. Dynamic escalation, cost-aware routing, benchmarking ---


def next_tier(current: Tier, failures: int, *, flags: Phase2Flags) -> Tier:
    """Climb small -> medium -> large on consecutive failures.

    ``failures`` counts consecutive self-hosted failures for the task.
    Zero failures ⇒ stay. Past large ⇒ ``NeedsHuman`` (escalation, not retry).
    """
    if not flags.dynamic_escalation:
        raise Phase2Disabled("dynamic_escalation flag is off")
    if failures <= 0:
        return current
    idx = _TIER_ORDER.index(current)
    if idx >= len(_TIER_ORDER) - 1:
        raise NeedsHuman(f"tier {current!r} still failing after {failures} failures")
    target = _TIER_ORDER[idx + 1]
    logger.bind(component="phase2").debug("escalating {} -> {} (failures={})", current, target, failures)
    return target


def rank_by_cost(deployments, known_cost: dict[str, float]):
    """Cheapest-first ordering; unknown cost keeps declared order at the end.

    Pure helper — the flag gates the gateway call site that uses it, so the
    default class-order routing in ``Gateway`` is untouched.
    """
    known = [d for d in deployments if d.name in known_cost]
    unknown = [d for d in deployments if d.name not in known_cost]
    known.sort(key=lambda d: known_cost[d.name])
    return known + unknown


class BenchmarkStore:
    """Measured per-deployment stats; manual policy becomes measured policy."""

    def __init__(self) -> None:
        self._attempts: dict[str, int] = {}
        self._errors: dict[str, int] = {}
        self._latency_ms: dict[str, int] = {}
        self._cost: dict[str, float] = {}

    def observe(self, completion, *, ok: bool = True) -> None:
        name = completion.deployment
        self._attempts[name] = self._attempts.get(name, 0) + 1
        if not ok:
            self._errors[name] = self._errors.get(name, 0) + 1
        self._latency_ms[name] = self._latency_ms.get(name, 0) + completion.latency_ms
        self._cost[name] = self._cost.get(name, 0.0) + completion.cost
        logger.bind(component="phase2", deployment=name, ok=ok).debug(
            "benchmark attempts={}", self._attempts[name]
        )

    def avg_cost(self) -> dict[str, float]:
        return {
            name: self._cost[name] / n for name, n in self._attempts.items() if n > 0
        }

    def to_report(self) -> dict[str, dict[str, float]]:
        report: dict[str, dict[str, float]] = {}
        for name, n in self._attempts.items():
            report[name] = {
                "attempts": float(n),
                "error_rate": self._errors.get(name, 0) / n,
                "avg_latency_ms": self._latency_ms.get(name, 0) / n,
                "avg_cost": self._cost.get(name, 0.0) / n,
            }
        return report


# --- 4. Context optimisation + semantic (keyword) retrieval, same interfaces ---


def tune_budget(default_chars: int, used_chars_history: list[int], *, flags: Phase2Flags) -> int:
    """Shrink the packet budget toward measured use; never below 25% of default.

    Takes recent fitted-packet sizes and caps the budget at max observed use,
    floored so one tiny task cannot starve the next normal one. Same
    ``build_packet`` interface — only the ``budget_chars`` argument changes.
    """
    if not flags.context_optimisation:
        raise Phase2Disabled("context_optimisation flag is off")
    if not used_chars_history:
        return default_chars
    tuned = max(int(default_chars * 0.25), max(used_chars_history))
    logger.bind(component="phase2").debug("budget {} -> {} (history={})", default_chars, tuned, used_chars_history)
    return tuned


def keyword_recall(query: str, docs: list[tuple[str, str]], *, limit: int = 5) -> list[str]:
    """Rank doc refs by query-token overlap; deterministic, no embeddings.

    ``docs`` are (ref, text) pairs — e.g. Serena symbols or file summaries.
    Returns up to ``limit`` refs, best overlap first; ties keep input order.
    Same downstream shape as the Serena adapter (refs into packet layers).
    """
    tokens = {t.lower() for t in query.replace("/", " ").replace("_", " ").split() if t}
    scored: list[tuple[int, int, str]] = []
    for pos, (ref, text) in enumerate(docs):
        hay = text.lower()
        overlap = sum(1 for t in tokens if t in hay)
        scored.append((-overlap, pos, ref))
    scored.sort()
    best = [ref for neg, _, ref in scored[:limit] if neg < 0]
    logger.bind(component="phase2").debug("recall query={!r} hits={}", query[:60], best)
    return best


# --- 5. Release gate: human approves merge/deploy; never autonomous ---


def request_release(state: dict) -> dict:
    """Mark the run state as awaiting the ``release`` human gate."""
    state = dict(state)
    state["pending_approval"] = "release"
    logger.bind(component="phase2").debug("release requested")
    return state


def can_release(state: dict, *, flags: Phase2Flags) -> bool:
    """True only with the flag on AND a human ``release`` approval recorded."""
    if not flags.release_gate:
        raise Phase2Disabled("release_gate flag is off")
    return state.get("last_gate") == "release" and state.get("last_gate_decision") == "approved"


def merge_allowed(state: dict, *, flags: Phase2Flags) -> bool:
    """Merge/deploy allowed ⇔ human-approved release. No autonomous merging, ever."""
    try:
        return can_release(state, flags=flags)
    except Phase2Disabled:
        return False


# --- 7. Eval-delta: per-item measurement against the plan-06 baseline ---


class EvalDelta(BaseModel):
    """Cost/context/completion delta of a flag-enabled arm vs baseline."""

    flag: str = Field(min_length=1)
    completion_delta: int = 0
    cost_delta: float = 0.0
    context_delta: int = 0
    verdict: str = "NEUTRAL"

    def is_neutral_or_better(self) -> bool:
        return self.verdict in ("NEUTRAL", "BETTER")


def eval_delta(flag: str, baseline: dict, candidate: dict) -> EvalDelta:
    """Compare two ``compare()`` rows; better = same+ completion at ≤ cost/context.

    ``baseline``/``candidate`` are the ``as_row()`` dicts (completion like
    ``"2/2"``). Verdicts: BETTER (strictly cheaper/smaller at same completion),
    NEUTRAL (equal or completion gain outweighs), WORSE otherwise.
    """
    if flag not in FLAG_NAMES:
        raise ValueError(f"unknown phase-2 flag {flag!r}")
    base_done = int(str(baseline.get("completion", "0/0")).split("/")[0])
    cand_done = int(str(candidate.get("completion", "0/0")).split("/")[0])
    cost_d = float(candidate.get("api_cost", 0.0)) - float(baseline.get("api_cost", 0.0))
    ctx_d = int(candidate.get("context_chars", 0)) - int(baseline.get("context_chars", 0))
    comp_d = cand_done - base_done
    if comp_d > 0 and cost_d <= 0 and ctx_d <= 0:
        verdict = "BETTER"
    elif comp_d == 0 and cost_d <= 0 and ctx_d <= 0:
        verdict = "BETTER" if (cost_d < 0 or ctx_d < 0) else "NEUTRAL"
    elif comp_d >= 0 and (cost_d < 0 or ctx_d < 0):
        verdict = "NEUTRAL"
    else:
        verdict = "WORSE"
    delta = EvalDelta(flag=flag, completion_delta=comp_d, cost_delta=cost_d,
                      context_delta=ctx_d, verdict=verdict)
    logger.bind(component="phase2", flag=flag).debug(
        "eval-delta completion={:+} cost={:+} ctx={:+} verdict={}",
        comp_d, cost_d, ctx_d, verdict,
    )
    return delta


__all__ = [
    "BenchmarkStore",
    "BoundedChoice",
    "EvalDelta",
    "NeedsHuman",
    "Phase2Disabled",
    "Phase2Flags",
    "architect_approval_gate",
    "assess_complexity",
    "can_release",
    "eval_delta",
    "flags_from_env",
    "keyword_recall",
    "load_phase2",
    "merge_allowed",
    "next_tier",
    "rank_by_cost",
    "request_release",
    "route_choice",
    "run_architect",
    "tune_budget",
]
