# Phase 06 — Evaluation harness

Goal: prove the factory thesis — frontier-led + self-hosted execution beats
one frontier model on the repo (quality per cost/time, not just quality).

Spec refs: §12 (run data), §13 (evaluation).

## Work items

1. Add `src/smallworks/eval/`: fixed task set with acceptance criteria; two
   arms — Baseline (single large model, full repo) vs SmallWorks pipeline.
2. Capture all §13 metrics per arm: completion, tests passed, retries, human
   interventions, regressions, tokens, wall-clock/GPU time, API cost, context
   size, escalation frequency (sourced from `RunReport`s + test results).
3. Generate a comparison report deciding the hypothesis per task set; store raw
   outputs as artefacts for audit.
4. Keep the harness deterministic: pinned task set, seeded retries, isolated
   worktrees per attempt.

## Acceptance

- Same task set runs under both arms; report shows every §13 metric side by
  side with a hypothesis verdict.
- A re-run reproduces the report from stored artefacts.

## Non-goals

No automatic tuning from results — findings feed plan 07, not this phase.
