"""HTTP service: run visibility + orchestrator chat + supervision (spec §10 read path)."""

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field

from smallworks.logging import configure_logging, logger
from smallworks.store import STORE
from smallworks.supervision import TaskControl

configure_logging()

app = FastAPI(title="SmallWorks")


class ChatIn(BaseModel):
    content: str = Field(min_length=1)


class ControlIn(BaseModel):
    action: str = Field(min_length=1)
    argument: str = ""


@app.get("/api/runs")
def list_runs() -> list[dict]:
    runs = STORE.list_runs()
    logger.bind(component="service", route="list_runs").debug("runs={}", len(runs))
    return [r.model_dump(mode="json") for r in runs]


@app.get("/api/runs/{run_id}")
def get_run(run_id: str) -> dict:
    run = STORE.get_run(run_id)
    if run is None:
        logger.bind(component="service", route="get_run", run_id=run_id).warning("unknown run")
        raise HTTPException(status_code=404, detail="unknown run")
    logger.bind(component="service", route="get_run", run_id=run_id).debug("hit")
    return run.model_dump(mode="json")


@app.get("/api/runs/{run_id}/chat")
def get_chat(run_id: str) -> dict:
    run = STORE.get_run(run_id)
    if run is None or run.chat is None:
        logger.bind(component="service", route="get_chat", run_id=run_id).warning("unknown run")
        raise HTTPException(status_code=404, detail="unknown run")
    logger.bind(component="service", route="get_chat", run_id=run_id).debug(
        "messages={}", len(run.chat.messages)
    )
    return run.chat.model_dump(mode="json")


@app.post("/api/runs/{run_id}/chat")
def post_chat(run_id: str, body: ChatIn) -> dict:
    log = logger.bind(component="service", route="post_chat", run_id=run_id)
    log.debug("message chars={}", len(body.content))
    thread = STORE.post_chat(run_id, body.content)
    if thread is None:
        log.warning("unknown run")
        raise HTTPException(status_code=404, detail="unknown run")
    log.debug("thread messages={}", len(thread.messages))
    return thread.model_dump(mode="json")


@app.get("/api/runs/{run_id}/logs")
def get_logs(run_id: str, raw: bool = False) -> dict:
    body = STORE.get_logs(run_id, raw=raw)
    if body is None:
        raise HTTPException(status_code=404, detail="unknown run")
    return {"run_id": run_id, "raw": raw, "logs": body}


@app.post("/api/runs/{run_id}/control")
def post_control(run_id: str, body: ControlIn) -> dict:
    run = STORE.get_run(run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="unknown run")
    try:
        control = TaskControl(task_id=run.task_id, action=body.action, argument=body.argument)  # type: ignore[arg-type]
    except ValueError:
        raise HTTPException(status_code=422, detail=f"unknown action {body.action!r}")
    state = STORE.apply_run_control(run_id, control)
    logger.bind(component="service", route="post_control", run_id=run_id).info(
        "control {} -> status={}", control.action, state.get("status") if state else None
    )
    return {"run_id": run_id, "state": state}

class MilestoneControlIn(BaseModel):
    task_id: str = Field(min_length=1)  # task AUTH-017 or milestone AUTH-M1
    action: str = Field(min_length=1)
    argument: str = ""


@app.get("/api/milestones")
def list_milestones() -> dict:
    return {"milestones": [m.model_dump(mode="json") for m in STORE.list_milestones()]}


@app.get("/api/milestones/{milestone_id}")
def get_milestone(milestone_id: str) -> dict:
    view = STORE.get_milestone(milestone_id)
    if view is None:
        raise HTTPException(status_code=404, detail="unknown milestone")
    return view.model_dump(mode="json")


@app.post("/api/milestones/{milestone_id}/control")
def post_milestone_control(milestone_id: str, body: MilestoneControlIn) -> dict:
    """Queue a human control for a running loop (approve/reject/send_back/retry/cancel).

    Controls drain FIFO at the top of each milestone round; approve/reject on
    the milestone id close or park it without running further rounds.
    """
    from smallworks.store import MilestoneView
    from smallworks.supervision import TaskControl

    view = STORE.get_milestone(milestone_id)
    if view is None:
        raise HTTPException(status_code=404, detail="unknown milestone")
    try:
        control = TaskControl(task_id=body.task_id, action=body.action, argument=body.argument)  # type: ignore[arg-type]
    except ValueError:
        raise HTTPException(status_code=422, detail=f"unknown action {body.action!r}")
    pending = _milestone_controls.setdefault(milestone_id, [])
    pending.append(control)
    logger.bind(component="service", route="post_milestone_control",
                milestone_id=milestone_id).info("control queued: {} on {}", control.action, control.task_id)
    _ = MilestoneView
    return {"milestone_id": milestone_id, "queued": len(pending)}


_milestone_threads: dict[str, object] = {}


def _run_demo_milestone(milestone_id: str) -> None:
    """Background scripted loop: wave-0 passes, AUTH-018 fails round 1, all pass round 2."""
    import json as _json
    import threading as _threading

    from smallworks.config import Deployment, FactoryPolicy, WorkerConfig
    from smallworks.gateway import Gateway, TransportResult
    from smallworks.schemas import ImplementationTask, Milestone
    from smallworks.store import MilestoneView
    from smallworks.workflow import run_until_milestone

    texts = {
        "engineer": _json.dumps({"tasks": []}),
        "developer": _json.dumps({"files_changed": ["src/auth/token.py"], "summary": "fix expiry"}),
        "tester": _json.dumps({"passed": True, "tests_run": 3, "tests_failed": 0}),
        "reviewer": _json.dumps({"verdict": "PASS", "notes": "looks good"}),
        "writer": "docs updated.",
    }

    class DemoTransport:
        def __init__(self):
            self.failed: set[str] = set()

        def complete(self, deployment, prompt, *, task_id):
            import time as _time

            _time.sleep(1.2)  # visible round pacing for the panel
            role = prompt.split("::", 1)[0]
            if role == "tester" and task_id == "AUTH-018" and task_id not in self.failed:
                self.failed.add(task_id)
                bad = dict(_json.loads(texts["tester"]))
                bad.update(passed=False, tests_failed=1)
                return TransportResult(text=_json.dumps(bad), input_tokens=1, output_tokens=1)
            return TransportResult(text=texts[role], input_tokens=1, output_tokens=1)

    class DemoGateway(Gateway):
        def complete(self, role, prompt, *, task_id="TASK-0"):
            return super().complete(role, f"{role}::{prompt}", task_id=task_id)

    models = {"coder_fast": [Deployment(provider="ollama", model="demo", model_class="self-hosted")]}
    workers = {
        r: WorkerConfig(model_group="coder_fast", tier="small", max_concurrent=8)
        for r in ("engineer", "developer", "tester", "reviewer", "writer")
    }
    policy = FactoryPolicy.model_validate(
        {
            "mode": "supervised",
            "max_retries": 1,
            "approvals_required": ["blueprint"],
            "max_cost_per_task": 5.0,
            "max_wallclock_minutes": 60.0,
        }
    )
    gw = DemoGateway(models, workers, policy, transport=DemoTransport())

    def task(tid: str, *deps: str) -> ImplementationTask:
        return ImplementationTask(
            task_id=tid, module="auth", behaviour="handle expired token",
            allowed_files=["src/auth/token.py"],
            acceptance_criteria=["expired token returns 401"], depends_on=list(deps),
        )

    tasks = [task("AUTH-017"), task("AUTH-018", "AUTH-017"), task("AUTH-019", "AUTH-017")]
    ms = Milestone(
        milestone_id=milestone_id, engineering_plan="plan-demo",
        predicate="all_tasks_pass", tasks=[t.task_id for t in tasks], max_rounds=5,
    )

    def on_round(view: dict) -> None:
        STORE.record_milestone(MilestoneView.model_validate(view))

    run_until_milestone(
        tasks, ms, gw, max_retries=0, max_workers=2,
        on_round=on_round, fresh_controls=lambda: pop_milestone_controls(milestone_id),
    )

    _milestone_threads.pop(milestone_id, None)


@app.post("/api/milestones/demo/run")
def run_demo_milestone() -> dict:
    """Start (or re-start) the scripted showcase loop; the panel polls its rounds."""
    import threading

    from smallworks.store import MilestoneView

    milestone_id = "AUTH-M1"
    STORE.record_milestone(
        MilestoneView(
            milestone_id=milestone_id, verdict="open", rounds=0, max_rounds=5,
            nodes=[
                {"task_id": "AUTH-017", "depends_on": [], "wave": 0, "outcome": "pending"},
                {"task_id": "AUTH-018", "depends_on": ["AUTH-017"], "wave": 1, "outcome": "pending"},
                {"task_id": "AUTH-019", "depends_on": ["AUTH-017"], "wave": 1, "outcome": "pending"},
            ],
            round_outcomes=[],
        )
    )
    if milestone_id not in _milestone_threads:
        thread = threading.Thread(target=_run_demo_milestone, args=(milestone_id,), daemon=True)
        _milestone_threads[milestone_id] = thread
        thread.start()
    logger.bind(component="service", route="run_demo_milestone").info("demo loop started")
    return {"milestone_id": milestone_id, "started": True}


@app.post("/api/milestones/live/run")
def run_live_milestone() -> dict:
    """Real pooled-Ollama loop in a background thread; rounds stream to STORE.

    Two tasks: implement ``slugify`` in ``src/smallworks/textutils.py``, then
    test it in ``tests/test_textutils.py`` (dependent — wave 2 unlocks on pass).
    Watch it on the console Milestones column.
    """
    import threading

    from smallworks.store import MilestoneView

    milestone_id = "LIVE-M1"
    STORE.record_milestone(
        MilestoneView(
            milestone_id=milestone_id, predicate="all_tasks_pass",
            verdict="open", rounds=0, max_rounds=4,
            nodes=[
                {"task_id": "LIVE-101", "depends_on": [], "wave": 0, "outcome": "pending"},
                {"task_id": "LIVE-102", "depends_on": ["LIVE-101"], "wave": 1, "outcome": "pending"},
            ],
            round_outcomes=[],
        )
    )
    if milestone_id not in _milestone_threads:
        thread = threading.Thread(target=_run_live_milestone, args=(milestone_id,), daemon=True)
        _milestone_threads[milestone_id] = thread
        thread.start()
    logger.bind(component="service", route="run_live_milestone").info("live loop started")
    return {"milestone_id": milestone_id, "started": True}


def _run_live_milestone(milestone_id: str) -> None:
    from smallworks.config import FactoryPolicy, default_config_dir, load_configs
    from smallworks.gateway import Gateway, OpenAICompatibleTransport
    from smallworks.schemas import ImplementationTask, Milestone
    from smallworks.store import MilestoneView
    from smallworks.workflow import run_until_milestone

    import yaml

    cfg = default_config_dir()
    loaded = load_configs(cfg / "models.yaml", cfg / "workers.yaml", cfg / "providers.yaml")
    raw = yaml.safe_load((cfg / "factory.yaml").read_text())["factory"]
    policy = FactoryPolicy.model_validate(
        {
            "mode": raw["mode"],
            "max_retries": 3,
            "approvals_required": raw["approvals_required"],
            "max_cost_per_task": raw["budgets"]["max_cost_per_task"],
            "max_wallclock_minutes": raw["budgets"]["max_wallclock_minutes"],
        }
    )
    gw = Gateway(
        loaded.models, dict(loaded.workers), policy,
        transport=OpenAICompatibleTransport(timeout_s=300.0), groups=loaded.groups,
    )
    tasks = [
        ImplementationTask(
            task_id="LIVE-101", module="retrier",
            behaviour=(
                "implement a dependency-free retry policy engine: RetryPolicy(total, "
                "backoff_base, backoff_cap, retryable) with delay_for(attempt) "
                "(exponential backoff capped, deterministic full jitter from a seeded "
                "Random), should_retry(exc, attempt) (retryable exception types only, "
                "attempts counted from 1, never past total), and run(fn) (calls fn, "
                "sleeps the computed delay between attempts, re-raises the LAST "
                "exception after total attempts, returns fn value on success). "
                "Non-retryable exceptions propagate immediately with no sleep. "
                "sleep must be injectable (default time.sleep)."
            ),
            allowed_files=["src/smallworks/retrier.py"],
            acceptance_criteria=[
                "delay_for grows exponentially and never exceeds backoff_cap",
                "same seed -> identical delay sequence (deterministic jitter)",
                "should_retry False for non-retryable types and attempt > total",
                "run returns fn value on eventual success; sleeps between attempts",
                "run re-raises the LAST exception after total attempts exhausted",
                "non-retryable exception propagates with zero sleeps",
            ],
        ),

        ImplementationTask(
            task_id="LIVE-102", module="textutils",
            behaviour="add unit tests for slugify covering spaces, punctuation and empty string",
            allowed_files=["tests/test_textutils.py"],
            acceptance_criteria=["pytest tests/test_textutils.py passes"],
            depends_on=["LIVE-101"],
        ),
    ]
    ms = Milestone(
        milestone_id=milestone_id, engineering_plan="plan-live",
        predicate="all_tasks_pass", tasks=[t.task_id for t in tasks], max_rounds=4,
    )

    def on_round(view: dict) -> None:
        STORE.record_milestone(MilestoneView.model_validate(view))

    log = logger.bind(component="service", milestone_id=milestone_id)
    log.info("live thread: loading configs")
    try:
        run_until_milestone(
            tasks, ms, gw, max_retries=1, max_workers=1,
            on_round=on_round, fresh_controls=lambda: pop_milestone_controls(milestone_id),
            verify=True,  # grounded: measured pytest overrides model claims
        )
    except Exception:
        log.opt(exception=True).error("live thread failed")
        STORE.record_milestone(
            MilestoneView(
                milestone_id=milestone_id, predicate="all_tasks_pass",
                verdict="breached", rounds=0, max_rounds=4,
                nodes=[
                    {"task_id": "LIVE-101", "depends_on": [], "wave": 0,
                     "outcome": "escalated", "action": "escalate",
                     "reason": "live thread crashed — see server log"},
                    {"task_id": "LIVE-102", "depends_on": ["LIVE-101"], "wave": 1,
                     "outcome": "pending"},
                ],
                round_outcomes=[],
            )
        )
    finally:
        _milestone_threads.pop(milestone_id, None)


@app.get("/api/tasks/{task_id}/cost")
def get_cost(task_id: str) -> dict:
    cost, inp, out = STORE.cost_for_task(task_id)
    return {"task_id": task_id, "cost": cost, "input_tokens": inp, "output_tokens": out}


class TodoIn(BaseModel):
    id: str = Field(min_length=1)
    title: str = Field(min_length=1)
    detail: str = ""


class TodoMoveIn(BaseModel):
    status: str = Field(min_length=1)


@app.get("/api/todos")
def list_todos() -> dict:
    from smallworks.store import Todo

    _ = Todo  # re-export anchor for the kanban panel
    return {"todos": [t.model_dump(mode="json") for t in STORE.list_todos()]}


@app.put("/api/todos")
def put_todo(body: TodoIn) -> dict:
    """Create or rename a card; status stays where the board put it."""
    from smallworks.store import Todo

    existing = next((t for t in STORE.list_todos() if t.id == body.id), None)
    todo = Todo(id=body.id, title=body.title, detail=body.detail,
                status=existing.status if existing else "pending")
    STORE.upsert_todo(todo)
    logger.bind(component="service", route="put_todo", todo_id=body.id).info("todo saved")
    return {"todos": [t.model_dump(mode="json") for t in STORE.list_todos()]}


@app.post("/api/todos/{todo_id}/move")
def move_todo(todo_id: str, body: TodoMoveIn) -> dict:
    """Advance a card: pending -> running -> passed (done), or blocked/failed (parked)."""
    from smallworks.schemas import TaskStatus

    try:
        status = TaskStatus(body.status)
    except ValueError:
        raise HTTPException(status_code=422, detail=f"bad status {body.status!r}")
    todo = STORE.move_todo(todo_id, status)
    if todo is None:
        raise HTTPException(status_code=404, detail="unknown todo")
    return todo.model_dump(mode="json")


@app.get("/api/runs/{run_id}/events")
def run_events(run_id: str):
    """SSE stream of run snapshots (plan 07); same data as polling, pushed.

    Requires the ``event_stream`` flag. Polling ``GET /api/runs/{id}`` stays
    the default UI path with all flags off.
    """
    from fastapi.responses import StreamingResponse

    from smallworks.phase2 import Phase2Disabled, load_phase2

    try:
        if not load_phase2().event_stream:
            raise Phase2Disabled("event_stream flag is off")
    except Phase2Disabled as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    run = STORE.get_run(run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="unknown run")
    logger.bind(component="service", route="run_events", run_id=run_id).debug("stream start")

    def gen():
        import json as _json

        snapshot = run.model_dump(mode="json")
        yield f"event: snapshot\ndata: {_json.dumps(snapshot)}\n\n"
        yield "event: end\ndata: {}\n\n"

    return StreamingResponse(gen(), media_type="text/event-stream")


class ProviderIn(BaseModel):
    base_url: str = Field(default="", min_length=0)
    api_key: str = Field(default="", min_length=0)
    api_key_env: str = Field(default="", min_length=0)
    default_model: str = Field(default="", min_length=0)


class ProvidersIn(BaseModel):
    providers: dict[str, ProviderIn]


def _providers_path() -> Path:
    from smallworks.config import default_config_dir

    return default_config_dir() / "providers.yaml"


_milestone_controls: dict[str, list] = {}


def pop_milestone_controls(milestone_id: str) -> list:
    """Drain queued controls for a loop (service owns the queue; workflow consumes)."""
    return _milestone_controls.pop(milestone_id, [])


def _dotenv_path() -> Path:
    return Path.cwd() / ".env"


def _read_dotenv_values(path: Path) -> dict[str, str]:
    from smallworks.gateway import load_dotenv

    return load_dotenv(path)


def _configured_providers() -> dict:
    """Explicit slots from providers.yaml (missing file = empty); defaults fill the rest."""
    from smallworks.config import default_providers, load_providers

    try:
        return load_providers(_providers_path())
    except ValueError:
        return default_providers()


def _providers_status() -> list[dict]:
    """Provider rows: base URL (with override source), key state (never the value).

    ``configured`` marks explicit slots in providers.yaml (the ones the page
    shows as forms); every known vendor is listed so [+] can add it.
    """
    import os

    from smallworks.config import KNOWN_PROVIDERS, PROVIDERS_REQUIRING_KEY, default_providers

    explicit = _configured_providers()
    providers = default_providers()
    providers.update(explicit)
    file_env = _read_dotenv_values(_dotenv_path())
    rows = []
    for name in list(explicit) + [k for k in KNOWN_PROVIDERS if k not in explicit]:
        cfg = providers[name]
        override = os.environ.get(f"{name.upper()}_BASE_URL", "")
        rows.append(
            {
                "name": name,
                "configured": name in explicit,
                "needs_key": name in PROVIDERS_REQUIRING_KEY,
                "base_url": cfg.base_url,
                "effective_base_url": override or cfg.base_url,
                "base_overridden": bool(override),
                "api_key_env": cfg.api_key_env,
                "key_source": "env"
                if cfg.api_key_env and cfg.api_key_env in os.environ
                else ("dotenv" if cfg.api_key_env and cfg.api_key_env in file_env else "missing"),
                "key_set": bool(
                    cfg.api_key_env
                    and (cfg.api_key_env in os.environ or cfg.api_key_env in file_env)
                ),
                "default_model": cfg.default_model,
            }
        )
    return rows


@app.get("/api/providers")
def list_providers() -> dict:
    return {"providers": _providers_status()}


@app.put("/api/providers")
def put_providers(body: ProvidersIn) -> dict:
    """Save provider base URLs to providers.yaml and API keys to .env (never YAML).

    Empty ``api_key`` leaves the stored key untouched; the response reports
    key presence only, never values.
    """
    import re

    import yaml

    from smallworks.config import KNOWN_PROVIDERS, ProviderConfig, default_providers

    current = _configured_providers()
    defaults = default_providers()
    for name, update in body.providers.items():
        if not name or not __import__("re").fullmatch(r"[A-Za-z][A-Za-z0-9_-]*", name):
            raise HTTPException(status_code=422, detail=f"bad provider name {name!r}")
        cfg = current.get(name)
        if cfg is None:  # [+] a new slot: seed known defaults, else blank custom
            seed = defaults.get(name)
            if seed is not None:
                cfg = ProviderConfig(
                    base_url=seed.base_url, api_key_env=seed.api_key_env, default_model=seed.default_model
                )
            else:
                cfg = ProviderConfig(base_url="http://localhost:8000/v1", api_key_env="")
            current[name] = cfg
        if update.base_url:
            cfg.base_url = update.base_url
        if update.api_key_env:
            if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", update.api_key_env):
                raise HTTPException(status_code=422, detail=f"bad env var {update.api_key_env!r}")
            cfg.api_key_env = update.api_key_env
        if update.default_model:
            cfg.default_model = update.default_model
    payload = {
        "providers": {
            name: {
                "base_url": cfg.base_url,
                "api_key_env": cfg.api_key_env,
                "default_model": cfg.default_model,
            }
            for name, cfg in current.items()
        }
    }
    _providers_path().write_text(yaml.safe_dump(payload, sort_keys=True), encoding="utf-8")
    file_env = _read_dotenv_values(_dotenv_path())
    for name, update in body.providers.items():
        if update.api_key:
            file_env[current[name].api_key_env or f"{name.upper()}_API_KEY"] = update.api_key
    lines = [f"{k}={v}" for k, v in sorted(file_env.items())]
    _dotenv_path().write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")
    logger.bind(component="service", route="put_providers").info(
        "providers updated: {}", sorted(body.providers)
    )
    return {"providers": _providers_status()}


class AssignmentIn(BaseModel):
    role: str = Field(min_length=1)
    provider: str = Field(min_length=1)
    model: str = Field(min_length=1)
    endpoint: str = Field(default="", min_length=0)
    position: str = Field(default="append")


@app.get("/api/models/groups")
def list_model_groups() -> dict:
    """Role -> deployments (provider/endpoint/model) for the settings UI.

    Groups still exist underneath (fallback order), but the page assigns
    models to roles directly; each assignment appends a deployment to the
    role's group (or replaces it when ``position == "only"``).
    """
    from smallworks.config import load_configs, default_config_dir

    cfg = default_config_dir()
    loaded = load_configs(cfg / "models.yaml", cfg / "workers.yaml", cfg / "providers.yaml")
    groups = {}
    for group, deployments in loaded.models.items():
        groups[group] = [
            {
                "provider": d.provider,
                "endpoint": d.endpoint,
                "model": d.model,
                "params": d.params,
                "class": d.model_class,
            }
            for d in deployments
        ]
    return {"roles": loaded.role_mapping(), "groups": groups}


@app.put("/api/models/assign")
def assign_model(body: AssignmentIn) -> dict:
    """Assign a provider model to a role: append to (or replace) its group chain.

    ``position``: ``"append"`` (default, extra fallback), ``"prepend"``
    (try first), ``"only"`` (this model alone). Writes models.yaml.
    """
    import yaml

    from smallworks.config import default_config_dir, load_configs

    cfg = default_config_dir()
    models_path = cfg / "models.yaml"
    loaded = load_configs(models_path, cfg / "workers.yaml", cfg / "providers.yaml")
    worker = loaded.workers.get(body.role)
    if worker is None or not worker.enabled:
        raise HTTPException(status_code=422, detail=f"unknown role {body.role!r}")
    if body.position not in ("append", "prepend", "only"):
        raise HTTPException(status_code=422, detail=f"bad position {body.position!r}")
    raw = yaml.safe_load(models_path.read_text(encoding="utf-8")) or {}
    groups = raw.get("models", {})
    entry: dict = {"provider": body.provider, "model": body.model, "class": "frontier"}
    if body.endpoint:
        entry["endpoint"] = body.endpoint
    chain = groups.get(worker.model_group, [])
    if body.position == "only":
        chain = [entry]
    elif body.position == "prepend":
        chain = [entry] + [d for d in chain if not _same_deployment(d, body)]
    else:
        chain = [d for d in chain if not _same_deployment(d, body)] + [entry]
    groups[worker.model_group] = chain
    raw["models"] = groups
    models_path.write_text(yaml.safe_dump(raw, sort_keys=False), encoding="utf-8")
    logger.bind(component="service", route="assign_model").info(
        "role {} -> {}:{} ({})", body.role, body.provider, body.model, body.position
    )
    return list_model_groups()


def _same_deployment(raw_dep: dict, body: AssignmentIn) -> bool:
    """True when a models.yaml entry already routes this provider+model."""
    if not isinstance(raw_dep, dict):
        return False
    provider = raw_dep.get("provider", "")
    model = raw_dep.get("model", "")
    if not provider and "name" in raw_dep:
        provider, _, model = str(raw_dep["name"]).partition("/")
    return provider == body.provider and model == body.model


@app.get("/", response_class=HTMLResponse)
def index() -> str:
    return (Path(__file__).parent / "ui.html").read_text(encoding="utf-8")


@app.get("/tube", response_class=HTMLResponse)
def tube() -> str:
    """Tube settings page: effect toggles + display (shares localStorage keys)."""
    return (Path(__file__).parent / "tube.html").read_text(encoding="utf-8")
