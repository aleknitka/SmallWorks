# Phase 04 — Workflow MVP (supervised factory loop)

Goal: run the default dev loop autonomously within factory bounds:
frontier-led triage → self-hosted Engineer/Developer/Tester → gated Reviewer,
humans approve gates only.

Spec refs: §2 (TAKT + Pi reuse), §3 (roles), §8 (decision plane), §9 (workflow).
Factory policy: `configs/factory.yaml` (escalation, approvals, budgets).

Prerequisites: Phases 01–03 (schemas, gateway, context packets).

## Work items

1. Define the TAKT state machine for the §9 default workflow:
   Request → Engineer → EngineeringPlan → Developer ↔ Tester → Reviewer →
   decision (`pass` / `retry` / `escalate`) → Documentation → Complete.
   Independent modules run concurrently.
2. Add Pi worker configs/prompts per role (`src/smallworks/workers/`):
   Engineer (self-hosted medium), Developer (self-hosted small, bounded task
   only), Tester (independent test derivation), Reviewer (gated verdict).
   Orchestrator (frontier) routes; it does NOT write implementation code.
   Self-hosted failure retries locally, then escalates tiers per `max_retries`.
3. Isolate each task in a Git worktree: setup before Developer, teardown after
   decision. Forbidden-file violations reject via `validate_decision`.
4. Wire deterministic gates over model decisions: failing tests / non-PASS
   review ⇒ no `pass`; security gate failure ⇒ `escalate` (human approval).
   Budget breach ⇒ pause + human approval.
5. Test with fixture tasks: happy path, test-failure retry, frontier escalation
   after `max_retries`, security-escalate, two modules concurrently.

## Acceptance

- Fixture Blueprint completes end-to-end; retry and escalate paths exercised.
- Gate tests prove a `pass` decision cannot survive failing tests or review.

## Non-goals

No Architect role, no Clef/Jev routing, no auto-merge (plan 07 / spec §15).
