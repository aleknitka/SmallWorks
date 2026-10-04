"""Rich debug logging for SmallWorks (investigation-first).

Console (stderr) + rotating file sinks via loguru, DEBUG by default.
Idempotent: repeated calls are no-ops unless ``force=True``.

File default: ``<repo>/logs/smallworks.log`` (``SMALLWORKS_LOG_DIR`` overrides,
``--log-file`` overrides both). Container mounts ``./logs:/app/logs``.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

from loguru import logger

__all__ = ["logger", "configure_logging", "default_log_dir", "default_log_file"]

_CONFIGURED = False

CONSOLE_FORMAT = (
    "<green>{time:YYYY-MM-DD HH:mm:ss.SSS}</green> | "
    "<level>{level: <8}</level> | "
    "<cyan>{name}</cyan>:<cyan>{function}</cyan>:<cyan>{line}</cyan> | "
    "<level>{message}</level>"
    " <dim>{extra}</dim>"
)
FILE_FORMAT = (
    "{time:YYYY-MM-DD HH:mm:ss.SSS} | {level: <8} | "
    "{name}:{function}:{line} | {message} | {extra}"
)


def default_log_dir() -> Path:
    override = os.environ.get("SMALLWORKS_LOG_DIR")
    if override:
        return Path(override)
    return Path(__file__).resolve().parents[2] / "logs"


def default_log_file() -> Path:
    return default_log_dir() / "smallworks.log"


def configure_logging(
    level: str = "DEBUG",
    log_file: Path | str | None = None,
    *,
    console: bool = True,
    force: bool = False,
) -> Path:
    """Configure loguru: stderr + file sinks at ``level``. Returns file path."""
    global _CONFIGURED
    if _CONFIGURED and not force:
        return Path(log_file) if log_file else default_log_file()

    lvl = (level or "DEBUG").upper()
    if lvl not in {"TRACE", "DEBUG", "INFO", "SUCCESS", "WARNING", "ERROR", "CRITICAL"}:
        lvl = "DEBUG"
    logger.remove()
    if console:
        logger.add(
            sys.stderr,
            level=lvl,
            format=CONSOLE_FORMAT,
            colorize=True,
            backtrace=True,
            diagnose=True,
        )
    target = Path(log_file) if log_file else default_log_file()
    target.parent.mkdir(parents=True, exist_ok=True)
    logger.add(
        str(target),
        level=lvl,
        format=FILE_FORMAT,
        rotation="10 MB",
        retention="7 days",
        backtrace=True,
        diagnose=True,
    )
    logger.bind(component="logging").debug(
        "logging configured level={} file={} console={}", lvl, str(target), console
    )
    _CONFIGURED = True
    return target
