# Loops & Graphs → Semi-Autonomous Milestone Factory

**Date:** 2026-10-05 · **Status:** research → proposal · **Scope:** SmallWorks control layer
(`src/smallworks/workflow.py`, `schemas.py`, `supervision.py`, spec §8–§11)

## 1. Problem

Today the factory runs a **linear gated pipeline**: `request → engineer → plan →
develop ↔ test → review → decide → done`, one task at a time, driven by a human
invocation per task or per run. Ask: **run itself until a milestone is met** —
e.g. "all Blueprint modules pass", "coverage gate holds for a whole
EngineeringPlan" — with humans intervening only at gates, on escalation, or on
budget breach. That needs two missing concepts:

1. **Loops** — bounded re-entry (retry a task, re-plan a module, re-run a suite).
2. **Graphs** — tasks as nodes, dependencies as edges, milestones as predicates
   over graph state.

## 2. Where SmallWorks stands today

| Primitive | Current form | Gap to milestone autonomy |
|---|---|---|
| Retry loop | `Workflow.run_task`: `while True`, `attempts > max_retries → escalate` (workflow.py:84–143) | Loop bound is per-task attempts only; no re-plan, no cross-task loop |
| Fan-out | `run_workflow`: `ThreadPoolExecutor`, independent tasks, no dependency edges (workflow.py:146–164) | No DAG: can't express "module B needs module A's Patch" |
| Termination | Per-task `TaskOutcome`: `passed / retry / escalated` | No run-level predicate ("milestone met?"); escalation parks instead of re-routing |
| Gates | `validate_decision`: failing tests ⇒ no `pass`; forbidden files ⇒ reject; security ⇒ escalate (schemas.py) | Gates judge one task; nothing aggregates task outcomes into a milestone verdict |
| Human control | `TaskControl` FIFO: pause/cancel/retry/escalate/approve… (supervision.py) | Controls address single tasks; no "approve milestone", no run-level pause scoping |
| Artefacts | `Blueprint → EngineeringPlan → Patch → TestReport → ReviewReport → Decision → RunReport` | No `Milestone` artefact; `RunReport` records one run, not convergence over loops |
| State | In-memory `states: list[str]` per task; `STORE` for chat/control | No durable checkpoint: a crashed run loses loop position |

Net: **the task loop is solved; the run loop is missing.** The factory can retry
a task forever-bounded, but cannot ask "are we done yet?" over a set of tasks.

## 3. What the field does (verified)

### 3.1 LangGraph — cycles + recursion limit + checkpointers

- Graph of nodes/edges; **loops are cycles** in the graph (conditional edges
  route back). No special loop syntax — any cycle is a loop.
- Bound: **`recursion_limit`** (default 25 super-steps); exceeding it raises
  `GRAPH_RECURSION_LIMIT` — the framework assumes unbounded cycles are bugs and
  fails fast. Raise explicitly for legitimately long loops.
  ([docs](https://docs.langchain.com/oss/python/langgraph/errors/GRAPH_RECURSION_LIMIT))
- Durable state: **checkpointers** persist a snapshot per super-step into
  threads; resume, time-travel, and human-in-the-loop all ride on checkpoints.
  ([docs](https://docs.langchain.com/oss/python/langgraph/checkpointers.md))
- HITL: `interrupt()` pauses before a node; human approves/edits state, graph
  resumes from the checkpoint.
- **Takeaway:** separate *loop bound* (recursion limit ≈ our `max_retries`,
  but run-scoped) from *loop state* (checkpoints ≈ durable `states` list).
  SmallWorks has the first per-task, neither run-scoped.

### 3.2 TAKT — YAML workflow owns the process (our declared foundation)

- Workflow = `steps` + `initial_step` + **`max_steps`**; each step has persona,
  permissions, and `rules: [{condition, next}]`. `COMPLETE`/`ABORT` are terminal.
  Fix loops are **explicit transitions** (`review → implement`), not code.
  ([nrslib/takt README](https://github.com/nrslib/takt/blob/main/README.md))
- Context discipline matches spec §6: each step gets only its persona/policy/
  knowledge — SmallWorks' ContextPacket is the same idea.
- Human checkpoints are steps, and every step leaves logs/reports (traceability).
- **Takeaway:** milestone logic belongs in **declarative transition rules**,
  not Python `while` bodies. Our `run_task` loop should become rules on a
  run-graph: `review --needs-fix--> develop`, `decide --all-pass--> milestone`.

### 3.3 Temporal — durable workflows, signals, continue-as-new

- Workflows are durable functions; **crash = resume from event history**, not
  restart. Loops are plain code, bounded by workflow-task history limits.
- **`continue-as-new`** closes an execution and starts a fresh one with the
  same ID when history grows — the canonical pattern for *unbounded* iteration
  with bounded state. ([docs](https://docs.temporal.io/workflow-execution/continue-as-new))
- **`signals`** inject external input (approvals, new tasks) into a running loop.
- **Takeaway:** for "run until milestone", **persist loop position durably**
  (STORE-backed run-state, not in-memory `states`) and treat human approval as
  a *signal into the running loop* (`TaskControl` already shaped like this —
  it just needs a run-level consumer).

### 3.4 AutoGen / CrewAI / ControlFlow — convergent patterns

- AutoGen graphs: cycles + `repeat` bound on loops; CrewAI Flows: `times`
  parameter; ControlFlow: DAG + replan. Common shape: **DAG for dependencies,
  bounded cycle for convergence, predicate for exit**.
  (Surveyed via scout; treat vendor-specific API claims as unverified.)
- **Takeaway:** the converged industry shape is exactly what §5's ASCII diagram
  wants: **DAG fan-out for independent modules + bounded retry cycle per node +
  exit predicate per milestone**.

## 4. Proposal: milestone loop on SmallWorks schema

Minimal delta, no framework import — express the graph in artefacts + rules we
already own.

### 4.1 New artefact: `Milestone`

```python
class Milestone(BaseModel):
    milestone_id: str
    engineering_plan: str          # which EngineeringPlan it closes
    predicate: Literal["all_tasks_pass", "coverage_holds", "human_approves"]
    tasks: list[str]               # ImplementationTask ids in scope
    budget: ...                    # reuse FactoryPolicy caps (retries, cost, wallclock)
    verdict: Literal["open", "met", "breached"] = "open"
```

`validate_decision` gains a sibling, `validate_milestone(tasks, reports) ->
verdict`, evaluated **deterministically** like all gates (spec §8: models never
override predicates).

### 4.2 Run-graph: DAG + convergence loop

```
EngineeringPlan ──fan-out──▶ task DAG (edges = module dependencies)
       │                           │ develop ↔ test → review
       │                           ▼
       │                      validate_decision (per task)
       ▼
validate_milestone ◀── all task Decisions ──▶ retry tasks (bounded) / escalate
       │ pass                              │ budget spent
       ▼                                   ▼
   milestone MET ──human approve──▶ Complete / human decides
```

Concretely:

1. **Edges:** `ImplementationTask` gains `depends_on: list[str]`; `run_workflow`
   topologically fans out (ready tasks run, blocked tasks wait) instead of
   blind `ThreadPoolExecutor` over all tasks.
2. **Outer loop:** wrap `run_workflow` in `run_until_milestone(plan, milestone)`:
   run ready tasks → collect Decisions → `validate_milestone` → if open and
   budget remains, re-queue `retry` tasks (with fresh ContextPackets, per §6);
   escalated tasks park for human *signal* (`TaskControl.retry` re-enters them).
3. **Bounds (three, like LangGraph + Temporal):** per-task `max_retries`
   (exists) + per-milestone `max_rounds` (≈ `recursion_limit`) + history cap:
   persist only latest artefacts per task per round (older rounds → artefact
   refs, RTK-style — never replay full history into context).
4. **Durable position:** run-state (`milestone_id → {task_id → last Decision,
   round}`) lives in `STORE`, not memory — crash resumes the loop.
5. **HITL as signal:** `pause`/`approve`/`send_back` already exist in
   `TaskControl`; scope them to milestone level (`approve milestone`,
   `send_back module to Engineer` per spec §10) and consume them at the top of
   each round.

### 4.3 What stays human (spec §16: humans retain control)

- `Blueprint` approval, `release` approval, escalations, budget breach — all
  remain human gates; the loop **parks, never bypasses**.
- The loop only automates the *convergence*: retry → re-test → re-review until
  the predicate holds or a bound trips.

## 5. Suggested build order

1. `depends_on` on `ImplementationTask` + topological fan-out in `run_workflow`
   (pure DAG, no loop change; test: diamond dependency).
2. `Milestone` schema + `validate_milestone` + `all_tasks_pass` predicate
   (deterministic; test: mixed pass/retry/escalate verdicts).
3. `run_until_milestone` outer loop with `max_rounds`, STORE-backed position,
   round-scoped ContextPackets (test: scripted transports, no live models).
4. Milestone-scoped `TaskControl` (`approve milestone`, `send_back module`).
5. Live proof: fixture Blueprint end-to-end on pooled local models; then board
   sync (each round posts task status to its GitHub Issue).

## 6. Sources

- LangGraph recursion limit:
  <https://docs.langchain.com/oss/python/langgraph/errors/GRAPH_RECURSION_LIMIT>
- LangGraph checkpointers:
  <https://docs.langchain.com/oss/python/langgraph/checkpointers.md>
- TAKT workflows (steps/rules/COMPLETE/ABORT, fix loops as transitions):
  <https://github.com/nrslib/takt/blob/main/README.md>
- Temporal continue-as-new:
  <https://docs.temporal.io/workflow-execution/continue-as-new>
- SmallWorks spec §5–§11, `src/smallworks/workflow.py:69–167`,
  `schemas.py::validate_decision`, `supervision.py::TaskControl`
