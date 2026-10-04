"""HTTP service: run visibility + orchestrator chat (spec §10 read path)."""

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field

from smallworks.logging import configure_logging, logger
from smallworks.store import STORE

configure_logging()

app = FastAPI(title="SmallWorks")


class ChatIn(BaseModel):
    content: str = Field(min_length=1)


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


@app.get("/", response_class=HTMLResponse)
def index() -> str:
    return (Path(__file__).parent / "ui.html").read_text(encoding="utf-8")
