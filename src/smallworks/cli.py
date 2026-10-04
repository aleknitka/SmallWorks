"""SmallWorks CLI."""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from smallworks import __version__
from smallworks.logging import configure_logging, logger

ROLES = ("orchestrator", "engineer", "developer", "tester", "reviewer", "writer")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="smallworks", description="SmallWorks orchestration CLI")
    parser.add_argument(
        "--log-level",
        default=os.environ.get("SMALLWORKS_LOG_LEVEL", "DEBUG"),
        help="Loguru level (DEBUG by default; SMALLWORKS_LOG_LEVEL overrides)",
    )
    parser.add_argument(
        "--log-file",
        type=Path,
        default=None,
        help="Log file path (default logs/smallworks.log; SMALLWORKS_LOG_DIR overrides dir)",
    )
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("info", help="Show version, roles, and spec pointer")
    vc = sub.add_parser("validate-config", help="Load models/workers YAMLs and print role -> group mapping")
    vc.add_argument("--models", type=Path, default=None, help="Path to models.yaml")
    vc.add_argument("--workers", type=Path, default=None, help="Path to workers.yaml")
    serve = sub.add_parser("serve", help="Run the container web service (runs + orchestrator chat)")
    serve.add_argument("--host", default="0.0.0.0")
    serve.add_argument("--port", type=int, default=8000)
    return parser


def cmd_validate_config(models: Path | None, workers: Path | None) -> int:
    from smallworks.config import default_config_dir, load_configs

    log = logger.bind(component="cli", command="validate-config")
    log.debug("validating models={} workers={}", models, workers)
    cfg_dir = default_config_dir()
    try:
        loaded = load_configs(models or cfg_dir / "models.yaml", workers or cfg_dir / "workers.yaml")
    except (ValueError, OSError) as exc:
        log.error("invalid config: {}", exc)
        print(f"invalid config: {exc}", file=sys.stderr)
        return 1
    mapping = loaded.role_mapping()
    log.debug("config valid roles={}", sorted(mapping))
    for role in sorted(mapping):
        print(f"{role} -> {mapping[role]}")
    return 0


def main(argv: list[str] | None = None) -> None:
    args = build_parser().parse_args(argv)
    target = configure_logging(level=args.log_level, log_file=args.log_file)
    logger.bind(component="cli", command=args.command).debug("start log_file={}", str(target))
    if args.command == "info":
        print(f"smallworks {__version__}")
        print(f"roles: {', '.join(ROLES)}")
        print("spec: docs/spec/SmallWorks-InitialSystemSpecification.md")
    elif args.command == "validate-config":
        raise SystemExit(cmd_validate_config(args.models, args.workers))
    elif args.command == "serve":
        import uvicorn

        logger.bind(component="cli", command="serve").info("serving host={} port={}", args.host, args.port)
        uvicorn.run("smallworks.service:app", host=args.host, port=args.port)


if __name__ == "__main__":
    main()
