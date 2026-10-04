# Phase 03 — Context and compression

Goal: give each worker a fresh, minimal Context Packet with progressive
disclosure and compressed tool output.

Spec refs: §6 (context strategy), §7 (RTK / Serena / Repomix / Caveman).

Prerequisites: Phase 01 (schemas). Needs only fixtures from real repos.

## Work items

1. Add `src/smallworks/context.py`: build `ContextPacket` from module contract
   + Serena symbols + related tests + coding rules. Enforce a token/size budget;
   order content summary → symbols → source → full file, truncating with refs.
2. Add thin adapters in `src/smallworks/adapters/`:
   - `serena.py`: symbols, references, callers/callees, definitions (Developer's
     primary navigation — return symbols, not whole files).
   - `repomix.py`: repo-level overview for Architect/Engineer only; MUST NOT be
     injected into Developer packets by default.
   - `rtk.py`: compress test/git/build/log/search output; always store raw
     output and expose it as `TestReport.raw_output_ref`.
   - `caveman.py`: concise structured worker reports (status/reason/next),
     no prose.
3. Unit-test with fixtures: budget enforcement, Developer packet excludes full
   repo dump, raw output retrievable via ref.

## Acceptance

- Packet for a sample task contains goal, contract, symbols, tests, rules and
   fits budget; full-file content only via explicit reference.
- Compressed shell output round-trips to raw artefact.

## Non-goals

No semantic retrieval or context-budget optimisation beyond fixed budgets
(Phase 2 / plan 07).
