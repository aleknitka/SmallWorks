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

@app.get("/api/tasks/{task_id}/cost")
def get_cost(task_id: str) -> dict:
    cost, inp, out = STORE.cost_for_task(task_id)
    return {"task_id": task_id, "cost": cost, "input_tokens": inp, "output_tokens": out}


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
