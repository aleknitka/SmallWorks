"""HTTP service: run visibility + orchestrator chat (spec §10 read path)."""

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field

from smallworks.store import STORE

app = FastAPI(title="SmallWorks")


class ChatIn(BaseModel):
    content: str = Field(min_length=1)


@app.get("/api/runs")
def list_runs() -> list[dict]:
    return [r.model_dump(mode="json") for r in STORE.list_runs()]


@app.get("/api/runs/{run_id}")
def get_run(run_id: str) -> dict:
    run = STORE.get_run(run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="unknown run")
    return run.model_dump(mode="json")


@app.get("/api/runs/{run_id}/chat")
def get_chat(run_id: str) -> dict:
    run = STORE.get_run(run_id)
    if run is None or run.chat is None:
        raise HTTPException(status_code=404, detail="unknown run")
    return run.chat.model_dump(mode="json")


@app.post("/api/runs/{run_id}/chat")
def post_chat(run_id: str, body: ChatIn) -> dict:
    thread = STORE.post_chat(run_id, body.content)
    if thread is None:
        raise HTTPException(status_code=404, detail="unknown run")
    return thread.model_dump(mode="json")


@app.get("/", response_class=HTMLResponse)
def index() -> str:
    return (Path(__file__).parent / "ui.html").read_text(encoding="utf-8")
