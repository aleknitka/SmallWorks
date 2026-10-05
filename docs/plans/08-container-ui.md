# Phase 08 — Container app + minimal UI (explicit user request)

Goal: ship SmallWorks as a container application (podman-compatible) with a
small UI to follow runs and chat with the orchestrator.

Note: spec §10/§14 says no custom web UI initially (GitHub Issues board). This
phase exists per explicit user request; the UI is a read + chat surface only,
NOT a replacement board. Plan 05 board sync still stands.

## Shipped

- `src/smallworks/service.py` — FastAPI: `GET /api/runs`, `GET /api/runs/{id}`,
  `GET/POST /api/runs/{id}/chat`, `GET /` dashboard.
- `src/smallworks/store.py` — in-memory run store + chat stub (`queued: …`
  reply until the orchestrator agent lands in plan 04). Single-process;
  durable persistence arrives with the workflow.
- `src/smallworks/ui.html` — runs list + orchestrator chat, 5s poll.
- `src/smallworks/cli.py` — `smallworks serve --host --port` (same as container CMD).
- `Dockerfile` — `ghcr.io/astral-sh/uv:python3.14-bookworm-slim`, layered
  `uv sync`, exposes 8000.
- `compose.yaml` — podman + docker compatible (`podman-compose up --build` or
  `podman compose up --build`); `configs/` mounted read-only; healthcheck on
  `/api/runs`.
- `tests/test_service.py` — runs list, chat round-trip, 404s, UI served.

## Acceptance

- `uv run pytest` green (78 passed, Phase 07); endpoints smoke-tested via
  TestClient: `/api/runs`, chat round-trip, `logs`, `cost`, UI served with
  control buttons + `EventSource` stream hookup.
- `compose.yaml` parses; `Dockerfile` base + sync flow valid.
- `GET /api/runs/{id}/events` verified live: 404 with flags off, 200
  `text/event-stream` snapshot with `SMALLWORKS_PHASE2=event_stream`.
- NOT done: `docker build` — Docker Desktop daemon not running on this machine
  (client 29.5.2, no engine at `dockerDesktopLinuxEngine`). Run
  `podman-compose up --build` (or `docker compose up --build`) where a
  runtime is available.

## Follow-ups (all landed in Phases 04/05/07)

- Chat routes to the orchestrator run thread (`store.post_chat` + control API);
  durable `RunReport` JSON via `Store(save_dir)`; UI streams `snapshot` events
  over SSE when `event_stream` is on, polling otherwise.
