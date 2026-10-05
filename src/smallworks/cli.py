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
    vc = sub.add_parser("validate-config", help="Load models/workers/providers YAMLs and print role -> group mapping")
    vc.add_argument("--models", type=Path, default=None, help="Path to models.yaml")
    vc.add_argument("--workers", type=Path, default=None, help="Path to workers.yaml")
    vc.add_argument("--providers", type=Path, default=None, help="Path to providers.yaml")
    st = sub.add_parser("status", help="Show run status from the store (spec §10 board fields)")
    st.add_argument("run_id", nargs="?", default=None, help="Run id; omit for all runs")
    lg = sub.add_parser("logs", help="Show run logs (compressed by default, --raw via ref)")
    lg.add_argument("run_id", help="Run id")
    lg.add_argument("--raw", action="store_true", help="Recall full raw output via RTK ref")
    ct = sub.add_parser("cost", help="Show cost/tokens for a task across runs")
    ct.add_argument("task_id", help="Task id, e.g. AUTH-017")
    serve = sub.add_parser("serve", help="Run the container web service (runs + orchestrator chat)")
    serve.add_argument("--host", default="0.0.0.0")
    serve.add_argument("--port", type=int, default=8000)
    return parser


def cmd_validate_config(models: Path | None, workers: Path | None, providers: Path | None = None) -> int:
    from smallworks.config import default_config_dir, load_configs

    log = logger.bind(component="cli", command="validate-config")
    log.debug("validating models={} workers={} providers={}", models, workers, providers)
    cfg_dir = default_config_dir()
    try:
        loaded = load_configs(
            models or cfg_dir / "models.yaml",
            workers or cfg_dir / "workers.yaml",
            providers or cfg_dir / "providers.yaml",
        )
    except (ValueError, OSError) as exc:
        log.error("invalid config: {}", exc)
        print(f"invalid config: {exc}", file=sys.stderr)
        return 1
    mapping = loaded.role_mapping()
    log.debug("config valid roles={}", sorted(mapping))
    for role in sorted(mapping):
        print(f"{role} -> {mapping[role]}")
    return 0


def cmd_status(run_id: str | None) -> int:
    from smallworks.store import STORE

    log = logger.bind(component="cli", command="status", run_id=run_id)
    if run_id is not None:
        run = STORE.get_run(run_id)
        if run is None:
            print(f"unknown run: {run_id}", file=sys.stderr)
            return 1
        runs = [run]
    else:
        runs = STORE.list_runs()
    for run in runs:
        board = run.board
        test_status = board.test_status if board else "-"
        print(f"{run.run_id} {run.task_id} {run.status.value} {run.worker} {run.model} tests={test_status}")
        if board and board.latest_report:
            print(f"  latest: {board.latest_report}")
    log.debug("status runs={}", len(runs))
    return 0


def cmd_logs(run_id: str, *, raw: bool = False) -> int:
    from smallworks.store import STORE

    log = logger.bind(component="cli", command="logs", run_id=run_id, raw=raw)
    body = STORE.get_logs(run_id, raw=raw)
    if body is None:
        print(f"unknown run: {run_id}", file=sys.stderr)
        return 1
    if not body:
        print("(no logs)")
        return 0
    for chunk in body:
        print(chunk)
        print("---")
    log.debug("logs chunks={}", len(body))
    return 0


def cmd_cost(task_id: str) -> int:
    from smallworks.store import STORE

    cost, inp, out = STORE.cost_for_task(task_id)
    logger.bind(component="cli", command="cost", task_id=task_id).debug(
        "cost={} in={} out={}", cost, inp, out
    )
    print(f"{task_id}: ${cost:.4f} in={inp} out={out}")
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
        raise SystemExit(cmd_validate_config(args.models, args.workers, args.providers))
    elif args.command == "status":
        raise SystemExit(cmd_status(args.run_id))
    elif args.command == "logs":
        raise SystemExit(cmd_logs(args.run_id, raw=args.raw))
    elif args.command == "cost":
        raise SystemExit(cmd_cost(args.task_id))
    elif args.command == "serve":
        import uvicorn

        logger.bind(component="cli", command="serve").info("serving host={} port={}", args.host, args.port)
        uvicorn.run("smallworks.service:app", host=args.host, port=args.port)


if __name__ == "__main__":
    main()
