from fastapi.testclient import TestClient

from smallworks.service import app

client = TestClient(app)


def test_list_runs_has_demo():
    body = client.get("/api/runs").json()
    assert any(r["run_id"] == "demo" for r in body)


def test_chat_roundtrip():
    posted = client.post("/api/runs/demo/chat", json={"content": "hello"}).json()
    roles = [m["role"] for m in posted["messages"]]
    assert roles[-2:] == ["user", "orchestrator"]
    fetched = client.get("/api/runs/demo/chat").json()
    assert fetched["messages"][-1]["content"] == "queued: hello"


def test_unknown_run_404():
    assert client.get("/api/runs/nope").status_code == 404
    assert client.post("/api/runs/nope/chat", json={"content": "x"}).status_code == 404


def test_index_serves_ui():
    res = client.get("/")
    assert res.status_code == 200
    assert "Orchestrator chat" in res.text
