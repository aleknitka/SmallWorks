# AGENTS.md — SmallWorks

> Supervised software factory: frontier models decide and rescue, self-hosted models execute.
> Source of truth: `docs/spec/SmallWorks-InitialSystemSpecification.md` (Draft v0.1).
> Autonomy bounds: `configs/factory.yaml` (tiers, escalation, approval gates, budgets).

## Stack (reuse, don't reimplement)

TAKT (workflow/state machine) · Pi (agent runtime) · LiteLLM (model gateway) ·
Ollama/vLLM (self-hosted serving) · Serena (symbol retrieval) · RTK (terminal compression) ·
Repomix (repo overview) · Caveman (concise reports) · Git worktrees (isolation) ·
GitHub Issues + Projects (task board).

## Repo layout

- `src/smallworks/` — control layer: `cli.py`, `config.py` (incl. `FactoryPolicy`),
  `gateway.py` (group routing + fallback + budgets), `logging.py` (loguru DEBUG),
  `context.py` (budgeted packets) + `adapters/` (serena/repomix/rtk/caveman),
  `schemas.py`, `service.py` (FastAPI: runs + orchestrator chat), `store.py`, `ui.html`
- `configs/` — `models.yaml` (logical model groups → deployments with class),
  `workers.yaml` (roles, model tiers, concurrency bounds),
  `factory.yaml` (supervised-autonomy: tiers, escalation, gates, budgets)
- `logs/` — gitignored loguru file sink; check first when investigating
- `docs/spec/` — system specification (normative)
- `tests/` — contract/behaviour tests
- `Dockerfile`, `compose.yaml` — container app (podman + docker compatible)

## Skills (repo-dev workflow only — NOT SmallWorks runtime rules)

- `agent-authorship` (`skill://agent-authorship`) — how agents working ON this
  repo tag PRs/reviews: `[auth:<model>:<harness>]`. Canonical text in
  `.omp/skills/agent-authorship/SKILL.md`, mirrored to `.agents/`, `.claude/`,
  `.codex/`, `.github/`, `.opencode/` so every harness discovers it natively.
  A SmallWorks-native equivalent (authorship on factory artefacts) is a
  possible later-stage option, NOT current behaviour.

## Commands

- `uv sync` — install
- `podman-compose up --build` (or `podman compose up --build`) — container app
- `uv run pytest` — full suite, MUST pass before yielding

## Conventions

1. Code references **logical model groups** (`coder_fast`, `strong_reasoning`, …),
   NEVER provider IDs. Each group lists deployments with `class: frontier |
   self-hosted`; order is preference — frontier-first for decisions
   (`strong_reasoning`), self-hosted-first for execution. Routing lives in
   `configs/models.yaml` (LiteLLM); autonomy bounds in `configs/factory.yaml`.
2. Supervised factory: routine work runs autonomously within `factory.yaml`
   (tier rules, `max_retries` before escalation, approval gates, budgets).
   Humans approve gates (blueprint, release, escalate, security, budget) —
   NEVER every step. Budget breach pauses the run and asks a human.
3. New artefacts → Pydantic schemas in `src/smallworks/schemas.py`. Reports use
   Caveman-concise structured fields, not prose.
4. Tasks ship as fresh **Context Packets** (`schemas.ContextPacket`): goal + module
   contract + relevant symbols + related tests + coding rules. No parent-conversation
   inheritance; progressive disclosure (summary → symbols → source → full file).
5. Shell/test/log output is compressed (RTK); raw output kept as an artefact
   reference (`TestReport.raw_output_ref`), not pasted inline.
6. Role boundaries: Orchestrator (frontier) plans and routes, does NOT write
   implementation. Developer (self-hosted) implements one bounded task, escalates
   to frontier after `max_retries`. Tester derives/runs tests independently.
   Reviewer gates integration; frontier breaks ties.
7. Deterministic gates override model decisions — enforced by
   `schemas.validate_decision`: failing tests ⇒ no `pass`; non-`PASS` review ⇒ no
   `pass`; forbidden files touched ⇒ reject; security gate failing ⇒ escalate.
8. Concurrency is bounded by GPU memory, rate limits, cost budgets, dependencies,
   and `configs/workers.yaml` limits. Models are resource pools, not identities.

## MVP scope (do NOT build)

No long-term semantic memory, no autonomous PR merging, no agent societies — see
spec §14. Architect role and Clef/Jev routing are Phase 2 (spec §15).
Note: spec §10/§14 says no custom web UI initially (GitHub Issues board); the
minimal container UI (`service.py`, `ui.html`) exists per explicit user request
as a read + chat surface only, NOT a replacement board.

## Before yielding

`uv run pytest` green; new behaviour covered by a behaviour test; every caller,
config, and doc updated; no stubs or `TODO: implement`.
