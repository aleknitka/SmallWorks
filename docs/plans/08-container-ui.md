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

## Acceptance (done except container build)

- `uv run pytest` green (9 passed); endpoints smoke-tested via TestClient.
- `compose.yaml` parses; `Dockerfile` base + sync flow valid.
- NOT done: `docker build` — daemon not running on this machine. Run
  `podman-compose up --build` (or `docker compose up --build`) where a
  runtime is available.

## Follow-ups (plan 04/05)

- Replace chat stub with real orchestrator routing; persist store; stream
  workflow events into the UI instead of polling.
