# Phase 02 — Model gateway (frontier + self-hosted)

Goal: route all workers through logical model groups, frontier-first for
decisions, self-hosted-first for execution, with fallback across the group.

Spec refs: §4 (model abstraction), §5 (concurrent execution).
Factory policy: `configs/factory.yaml` (tiers, escalation).

Prerequisites: Phase 01 (validated `models.yaml` / `workers.yaml` / `factory.yaml`).

## Work items

1. Add `src/smallworks/gateway.py`: resolve `group → deployment` from
   `configs/models.yaml` (respect declared `class` order); implement retries +
   fallback to next deployment (self-hosted failure ⇒ next self-hosted, then
   frontier per `max_retries`); latency-aware preference where LiteLLM exposes it.
2. Wire self-hosted serving: Ollama and/or vLLM via OpenAI-compatible endpoints;
   frontier via LiteLLM external deployments — same gateway interface.
3. Enforce concurrency bounds from `workers.yaml` (`max_concurrent` per role)
   plus task-dependency gating. Treat models as resource pools, not identities.
4. Enforce factory budgets (cost, wall-clock) per call: breach pauses and asks a human.
5. Record per-call `model`, `provider`, `class` (frontier/self-hosted), token counts,
   cost for later `RunReport`.
6. Add `tests/test_gateway.py` with mocked providers: class-order preference,
   retry-then-escalate, concurrency cap, budget breach pauses.

## Acceptance

- Requesting `coder_fast` hits self-hosted first, falls back to frontier
  after `max_retries` — verified with mocked transports.
- Requesting `strong_reasoning` hits frontier first.
- Concurrent dispatch never exceeds per-role `max_concurrent`.

## Non-goals

No cost-aware routing, benchmarking, or automatic tuning (plan 07).

