# Phase 01 — Foundation: control layer

Goal: close out the typed control layer everything else builds on.

Spec refs: §11 (artefacts), §4 (logical model groups), §8 (deterministic gates).

Prerequisites: none — builds on existing `schemas.py`, `configs/`, `cli.py`.

## Work items

1. Finish Pydantic schemas in `src/smallworks/schemas.py`: add `ProjectSpec`,
   `Blueprint`, `ModuleSpec`, `EngineeringPlan`, `Patch`. Keep existing
   `ContextPacket`, `ImplementationTask`, `TestReport`, `ReviewReport`,
   `Decision`, `RunReport`, `validate_decision` as canonical.
2. Add config loaders in `src/smallworks/config.py`: parse and validate
   `configs/models.yaml` (group → deployments) and `configs/workers.yaml`
   (role → group, tier, `max_concurrent`). Fail fast on unknown group/tier.
3. Extend CLI in `src/smallworks/cli.py`: `info`, `validate-config`
   (loads both YAMLs, prints role → group mapping). No workflow commands yet.
4. Extend `tests/`: schema round-trip tests + gate tests alongside existing
   `tests/test_decision_gates.py`.

## Acceptance

- `uv run smallworks validate-config` passes on shipped YAMLs, fails on bad group.
- `uv run pytest` green: schemas accept spec-shaped artefacts, reject bad ones.

## Non-goals

No gateway routing logic, no context builders, no TAKT/Pi wiring (later phases).
