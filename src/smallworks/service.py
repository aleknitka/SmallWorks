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


@app.get("/", response_class=HTMLResponse)
def index() -> str:
    return (Path(__file__).parent / "ui.html").read_text(encoding="utf-8")


@app.get("/tube", response_class=HTMLResponse)
def tube() -> str:
    """Tube settings page: effect toggles + display (shares localStorage keys)."""
    return (Path(__file__).parent / "tube.html").read_text(encoding="utf-8")
