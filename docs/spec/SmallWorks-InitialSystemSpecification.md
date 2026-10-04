# SmallWorks

**Status:** Draft v0.1  
**Purpose:** Multi-model software engineering orchestration optimised for small/local LLMs

## 1. Objective

SmallWorks is an agentic software-development environment designed to allow relatively small models to perform useful engineering work by:

- decomposing large problems into bounded tasks;
- giving each model only the context needed for that task;
- using specialised roles rather than general-purpose agents;
- compressing code, shell output and inter-agent communication;
- running local and external models concurrently;
- escalating difficult work to stronger models;
- verifying work using deterministic tests and review gates;
- keeping human users informed and able to intervene.

The primary hypothesis is:

> Small models can produce substantially better software when task complexity, context size, tool output and responsibilities are deliberately constrained by the surrounding system.

---

# 2. Foundation

SmallWorks SHOULD reuse existing components rather than implement agent primitives from scratch.

Initial stack:

| Capability | Component |
|---|---|
| Workflow orchestration | TAKT |
| Agent runtime | Pi |
| Model gateway | LiteLLM |
| Local serving | Ollama and/or vLLM |
| Code structure/retrieval | Serena |
| Terminal compression | RTK |
| Repository overview | Repomix |
| Concise agent communication | Caveman |
| Decision routing | Clef/Jev |
| Human task board | GitHub Issues + Projects initially |
| Source isolation | Git worktrees |

TAKT provides the declarative workflow/state-machine layer and can invoke Pi as an agent provider.

Pi provides individual agent sessions, tools, skills and extensions.

SmallWorks itself should initially consist mainly of configuration, adapters, schemas, evaluation tooling and specialised workflows.

---

# 3. Agent hierarchy

## Orchestrator

Responsible for:

- understanding project-level objectives;
- creating work requests;
- selecting appropriate workflow;
- choosing/escalating model tier;
- receiving worker reports;
- identifying blocked tasks;
- requesting human intervention when required.

Typical model:

**Large / strong model**, local or cloud.

The Orchestrator SHOULD NOT write implementation code.

---

## Architect

Transforms an idea or feature request into a **Blueprint**.

Responsibilities:

- identify modules;
- define module boundaries;
- define dependencies;
- define interfaces;
- establish architectural constraints;
- define module-level acceptance criteria.

Output:

`Blueprint`

Typical model:

**Strong reasoning model.**

---

## Engineer

Takes one Blueprint module and produces implementation tasks.

Responsibilities:

- define functions/classes/components;
- define inputs and outputs;
- describe expected behaviour;
- identify failure modes;
- define tests;
- define allowed files/scope;
- identify dependencies.

Output:

`EngineeringPlan`

Typical model:

**Medium model**, e.g. 12–30B.

---

## Developer

Implements bounded tasks.

Receives:

- task contract;
- relevant source symbols;
- local coding standards;
- required tests;
- minimal project context.

Does NOT receive the entire project history.

Typical model:

**8–14B coding model**.

Multiple Developers MAY execute concurrently.

---

## Tester

Creates and/or runs tests independently of the Developer.

Responsibilities:

- derive tests from acceptance criteria;
- identify missing edge cases;
- execute test suites;
- report failures concisely.

Typical model:

**8–14B model**.

---

## Reviewer / Integrator

Determines whether implementation:

- satisfies the Engineering Plan;
- respects architecture;
- does not introduce integration regressions;
- satisfies acceptance criteria.

Typical model:

**12–30B** or escalated external model.

---

## Technical Writer

Produces:

- docstrings;
- API documentation;
- module documentation;
- change notes where required.

Typical model:

**8B class**.

---

# 4. Model abstraction

All workers MUST refer to **logical model classes**, not provider-specific model IDs.

Example:

```yaml
models:
  architect:
    group: strong_reasoning

  engineer:
    group: engineering

  developer:
    group: coder_fast

  tester:
    group: coder_fast

  reviewer:
    group: reviewer
```

LiteLLM should provide these virtual model groups.

Example:

```yaml
coder_fast:
  - ollama/qwen-coder-local
  - vllm/qwen-coder-server
  - external/qwen-coder-api

strong_reasoning:
  - anthropic/...
  - openai/...
  - bedrock/...
```

LiteLLM supports routing across deployments, including weighting, latency-aware routing, retries and fallbacks.

Local models served through vLLM can expose an OpenAI-compatible API, simplifying integration with the same gateway.

---

# 5. Concurrent model execution

Local and external models MUST be usable simultaneously.

Example:

```text
Architect
Cloud strong model
       │
       ▼
Blueprint
       │
 ┌─────┼───────────────┐
 ▼     ▼               ▼
Eng A  Eng B           Eng C
14B    cloud model     27B local
 │      │               │
 ▼      ▼               ▼
Dev1   Dev2            Dev3
8B     12B             8B
local  local           external
```

Concurrency MUST be bounded by:

- GPU memory;
- provider rate limits;
- cost budgets;
- task dependencies;
- configured worker limits.

A model should therefore be considered a **resource pool**, not an agent identity.

---

# 6. Context strategy

Workers SHOULD NOT inherit conversations from parent agents.

Every task receives a freshly compiled **Context Packet**.

Example:

```yaml
task:
  id: AUTH-017

goal:
  handle_expired_token

context:
  project_summary: ...
  module_contract: ...
  relevant_symbols: [...]
  related_tests: [...]
  coding_rules: [...]

references:
  raw_repo: ...
  previous_run: ...
```

Context should use progressive disclosure:

```text
summary
   ↓
relevant symbols
   ↓
relevant source
   ↓
full file
```

Raw information remains available but is not included unless requested.

---

# 7. Compression stack

## RTK

Intercept shell operations and reduce:

- test output;
- git output;
- build output;
- logs;
- search results.

Raw output MUST remain available as an artefact.

---

## Serena

Primary developer code-navigation mechanism.

Workers should preferably request:

- symbols;
- references;
- callers;
- callees;
- relevant definitions;

instead of complete source files.

---

## Repomix

Used primarily for:

- Architect;
- Engineer;
- initial project analysis.

Provides compressed repository-level structure.

It SHOULD NOT normally be injected into Developer context.

---

## Caveman

Used for worker reports and internal agent communication.

Reports SHOULD favour structured concise output over prose.

Example:

```yaml
status: failed

reason:
  test: test_expired_token
  expected: 401
  actual: 500

suspected_file:
  src/auth/token.py

next:
  developer_retry
```

---

# 8. Decision plane

Clef/Jev SHOULD be evaluated for frequent bounded decisions such as:

```text
next worker?
retry?
escalate?
accept?
needs architect?
needs human?
complexity level?
```

Example decision:

```text
developer_retry    0.61
engineer_review    0.21
large_model        0.14
human              0.04
```

Deterministic rules override model decisions where appropriate.

Examples:

- tests failing → cannot mark complete;
- type checking failing → cannot merge;
- forbidden files modified → reject;
- security gate failing → escalate.

---

# 9. Workflow

Default development workflow:

```text
Request
   ↓
Architect
   ↓
Blueprint
   ↓
Engineer
   ↓
Engineering Plan
   ↓
Developer ───── Tester
   │               │
   └──────┬────────┘
          ▼
       Reviewer
          │
       decision
      /    |     \
    pass  retry  escalate
     │      │       │
     ▼      └───────┘
Documentation
     │
     ▼
Complete
```

Independent modules MAY execute concurrently.

---

# 10. Human control

Initial UI SHOULD reuse **GitHub Issues and GitHub Projects** rather than build a new dashboard.

Each task should expose:

- status;
- assigned role;
- model used;
- dependencies;
- current attempt;
- test status;
- latest report;
- artefacts;
- cost/token usage.

Human controls:

- pause;
- cancel;
- retry;
- escalate;
- change model;
- send back to Engineer;
- add instructions;
- approve/reject.

A custom UI should only be developed after the workflow is proven.

---

# 11. Artefacts

Agents communicate primarily through durable artefacts.

Core artefacts:

```text
ProjectSpec
Blueprint
ModuleSpec
EngineeringPlan
ImplementationTask
Patch
TestReport
ReviewReport
Decision
RunReport
```

Artefacts SHOULD use typed schemas.

Where the control layer is Python, Pydantic SHOULD define canonical schemas.

---

# 12. Observability

Every run records:

```yaml
worker:
model:
provider:
started:
completed:

tokens:
  input:
  output:
  compressed_saved:

cost:

context:
  tokens:
  sources:

tools:
  calls:

result:
  status:

parent_task:
artefacts:
```

This data is necessary to determine whether smaller models actually benefit from the architecture.

---

# 13. Evaluation

The project must evaluate the central hypothesis empirically.

Compare:

### Baseline

One large model operating directly on repository.

### SmallWorks

Specialised local models + compressed context + orchestration.

Metrics:

- task completion;
- tests passed;
- retries;
- human interventions;
- regressions;
- total tokens;
- wall-clock time;
- GPU time;
- API cost;
- context size;
- escalation frequency.

---

# 14. MVP

The MVP SHOULD contain only:

```text
TAKT
Pi
LiteLLM
Ollama/vLLM
RTK
Serena
Repomix

Engineer
Developer
Tester
Reviewer

Git worktrees
GitHub Issues/Projects
structured artefacts
basic telemetry
```

Do NOT initially build:

- custom web UI;
- long-term semantic memory;
- sophisticated automatic architecture generation;
- autonomous PR merging;
- large agent societies.

---

# 15. Phase 2

After MVP validation:

- Architect role;
- Clef/Jev decision routing;
- dynamic model escalation;
- automatic model benchmarking;
- cost-aware routing;
- context-budget optimisation;
- semantic retrieval;
- richer GitHub board synchronisation;
- custom dashboard if GitHub Projects becomes limiting.

---

# 16. Architectural principle

The system should optimise the **environment around the model**, rather than expecting the model to compensate for poor context.

Therefore:

> Strong models decide broadly.  
> Small models perform bounded work.  
> Tools provide precise context.  
> Deterministic systems verify results.  
> Humans retain control.