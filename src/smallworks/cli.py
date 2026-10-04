"""SmallWorks CLI."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from smallworks import __version__

ROLES = ("orchestrator", "engineer", "developer", "tester", "reviewer", "writer")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="smallworks", description="SmallWorks orchestration CLI")
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

    cfg_dir = default_config_dir()
    try:
        loaded = load_configs(models or cfg_dir / "models.yaml", workers or cfg_dir / "workers.yaml")
    except (ValueError, OSError) as exc:
        print(f"invalid config: {exc}", file=sys.stderr)
        return 1
    for role in sorted(loaded.role_mapping()):
        print(f"{role} -> {loaded.role_mapping()[role]}")
    return 0


def main(argv: list[str] | None = None) -> None:
    args = build_parser().parse_args(argv)
    if args.command == "info":
        print(f"smallworks {__version__}")
        print(f"roles: {', '.join(ROLES)}")
        print("spec: docs/spec/SmallWorks-InitialSystemSpecification.md")
    elif args.command == "validate-config":
        raise SystemExit(cmd_validate_config(args.models, args.workers))
    elif args.command == "serve":
        import uvicorn

        uvicorn.run("smallworks.service:app", host=args.host, port=args.port)


if __name__ == "__main__":
    main()
