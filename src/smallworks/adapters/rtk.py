"""RTK adapter: compress shell output, always keep raw (spec §7).

``compress_output`` returns (summary, ref); the raw text is stored in a
``RecallStore`` and retrievable via the ref — exposed to workers as
``TestReport.raw_output_ref``. Head+tail compression keeps failures visible:
first lines (command/context) plus last lines (errors/tracebacks).
"""

from __future__ import annotations

import hashlib
import threading

from smallworks.logging import logger

HEAD_LINES = 10
TAIL_LINES = 20


class RecallStore:
    """In-memory raw-output artefacts; durable persistence arrives in plan 04."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._raw: dict[str, str] = {}

    def put(self, raw: str) -> str:
        digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()[:12]
        ref = f"rtk:{digest}"
        with self._lock:
            self._raw[ref] = raw
        logger.bind(component="rtk", ref=ref).debug("stored raw chars={}", len(raw))
        return ref

    def get(self, ref: str) -> str | None:
        with self._lock:
            return self._raw.get(ref)


def compress_output(raw: str, store: RecallStore, *, head: int = HEAD_LINES, tail: int = TAIL_LINES) -> tuple[str, str]:
    """Compress to head+tail lines; store raw and return (summary, ref)."""
    ref = store.put(raw)
    lines = raw.splitlines()
    log = logger.bind(component="rtk", ref=ref, lines=len(lines))
    if len(lines) <= head + tail:
        log.debug("short output, no truncation")
        return raw, ref
    omitted = len(lines) - head - tail
    summary = "\n".join(lines[:head] + [f"... [{omitted} lines omitted, see {ref}] ..."] + lines[-tail:])
    log.debug("compressed {} lines to {} (+1 marker), omitted={}", len(lines), head + tail, omitted)
    return summary, ref


def recall_output(ref: str, store: RecallStore) -> str | None:
    return store.get(ref)
