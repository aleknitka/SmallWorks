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


def test_control_pause_and_approve(tmp_path):
    from smallworks.store import STORE

    STORE.create_run("svc-ctl", "AUTH-017")
    paused = client.post("/api/runs/svc-ctl/control", json={"action": "pause"}).json()
    assert paused["state"]["status"] == "paused"
    assert client.get("/api/runs/svc-ctl").json()["status"] == "blocked"
    bad = client.post("/api/runs/svc-ctl/control", json={"action": "nonsense"})
    assert bad.status_code == 422


def test_logs_compressed_and_raw():
    from smallworks.store import STORE

    STORE.create_run("svc-log", "AUTH-018")
    STORE.append_log("svc-log", "\n".join(f"line {i}" for i in range(100)))
    short = client.get("/api/runs/svc-log/logs").json()
    assert "omitted" in short["logs"][0]
    full = client.get("/api/runs/svc-log/logs?raw=true").json()
    assert "line 50" in full["logs"][0] and "omitted" not in full["logs"][0]


def test_cost_endpoint():
    body = client.get("/api/tasks/DEMO-001/cost").json()
    assert body["task_id"] == "DEMO-001" and "cost" in body


def test_index_serves_ui():
    res = client.get("/")
    assert res.status_code == 200
    assert "Orchestrator chat" in res.text
