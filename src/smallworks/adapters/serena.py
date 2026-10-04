"""Serena adapter: symbols/references/callers, never whole files (spec §7).

Real Serena integration arrives later (MCP/CLI call); this module defines the
result shape and a deterministic in-memory stand-in so packet building and
tests work without the service. The contract — symbols, not files — is what
matters: callers MUST NOT expect full source here.
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from smallworks.logging import logger


class SerenaResult(BaseModel):
    symbols: list[str] = Field(default_factory=list)
    references: list[str] = Field(default_factory=list)
    callers: list[str] = Field(default_factory=list)
    callees: list[str] = Field(default_factory=list)


# Deterministic fixture index used by tests and offline packet builds.
_FIXTURE_INDEX: dict[str, SerenaResult] = {
    "src/auth/token.py": SerenaResult(
        symbols=["validate(token) -> bool", "refresh(token) -> str", "TokenStore"],
        references=["src/auth/middleware.py", "tests/test_token.py"],
        callers=["middleware.require_auth"],
        callees=["store.lookup", "crypto.verify"],
    ),
}


def query_serena(path: str, *, symbol: str | None = None) -> SerenaResult:
    """Return symbols/references for ``path``; empty result for unknown paths."""
    log = logger.bind(component="serena", path=path, symbol=symbol)
    result = _FIXTURE_INDEX.get(path)
    if result is None:
        log.debug("no fixture for path, returning empty symbols")
        return SerenaResult()
    if symbol is not None:
        narrowed = [s for s in result.symbols if symbol.lower() in s.lower()]
        log.debug("narrowed {} symbols to {} for {:?}", len(result.symbols), len(narrowed), symbol)
        return result.model_copy(update={"symbols": narrowed or result.symbols})
    log.debug("symbols={} refs={}", len(result.symbols), len(result.references))
    return result
