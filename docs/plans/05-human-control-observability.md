# Phase 05 — Supervision: gates, board, telemetry

Goal: run supervised — humans approve factory gates, follow everything from
the UI and GitHub, with full run telemetry.

Spec refs: §10 (human control), §12 (observability).
Factory policy: `configs/factory.yaml` (approval gates, budgets).

## Work items

1. Add `src/smallworks/board.py`: sync each task to a GitHub Issue (+ Projects
   field) exposing status, role, model, dependencies, attempt, test status,
   latest report, artefacts, cost/tokens. No custom UI.
2. Implement human controls as workflow inputs: pause, cancel, retry, escalate,
   change model, send back to Engineer, add instructions, approve/reject.
3. Emit a `RunReport` per run (worker, model, provider, timestamps, tokens in /
   out / saved, cost, context sources, tool calls, result, parent task,
   artefacts). Persist alongside task artefacts.
4. Extend CLI: `status`, `logs` (compressed by default, `--raw` via ref),
   `cost` per task/run.
5. Test board mapping and each human control against a fixture run.

## Acceptance

- Fixture run is visible on the board with all §10 fields; each human control
  drives the workflow (spot-check pause, retry, approve).
- Every run produces a complete `RunReport`.

## Non-goals

No custom dashboard or richer Projects automation (Phase 2 / plan 07).
