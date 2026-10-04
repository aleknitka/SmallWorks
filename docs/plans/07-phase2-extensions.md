# Phase 07 — Phase 2 extensions (post-MVP)

Goal: expand only after MVP validation (plan 06 verdict). Each item ships
behind a flag and is measured against the eval baseline.

Spec refs: §15, plus §3 (Architect), §8 (Clef/Jev).

## Work items

1. Architect role (frontier): idea → `Blueprint` (modules, boundaries, dependencies,
   interfaces, constraints, acceptance criteria) using Repomix overview.
   Blueprint approval is a human gate per `factory.yaml`.
2. Clef/Jev decision routing for bounded choices (next worker, retry,
   escalate, accept, needs-architect, needs-human, complexity) with
   deterministic overrides kept authoritative.
3. Dynamic tier escalation + cost-aware routing + automatic benchmarking
   (extends plan 02 class-order routing with measured policy).
4. Context-budget optimisation and semantic retrieval (upgrade of plan 03
   adapters, same interfaces).
5. Release gate: human-approved merge/deploy; no autonomous merging.
6. Richer GitHub board sync; UI streams workflow events instead of polling.
7. Each item: flag-gated, eval-delta measured in plan 06 harness before
   becoming default.

## Acceptance

- Every shipped item shows a neutral-or-better eval delta with cost/context
  numbers attached.
- MVP path (plans 01–06) still runs unchanged with all flags off.

## Non-goals

No agent societies, long-term semantic memory, or autonomous merging —
explicitly out of scope per spec §14. Release stays human-approved.
