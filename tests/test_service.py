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


def test_tube_serves_settings():
    res = client.get("/tube")
    assert res.status_code == 200
    assert "Tube effects" in res.text and "SCANLINES" in res.text
    assert "sw-fx-" in res.text  # shares toggle keys with the console


def test_providers_status_masks_keys():
    res = client.get("/api/providers")
    assert res.status_code == 200
    names = {p["name"] for p in res.json()["providers"]}
    assert {"ollama", "github", "openai"} <= names
    for p in res.json()["providers"]:
        assert "api_key" not in p and "key_set" in p  # presence only, never values
    assert any(p["needs_key"] for p in res.json()["providers"] if p["name"] == "github")


def test_providers_first_row_is_configured_slot():
    rows = client.get("/api/providers").json()["providers"]
    assert rows[0]["configured"] is True  # one slot ships; the rest wait for [+]
    assert any(not p["configured"] for p in rows[1:])


def test_groups_show_provider_routing():
    body = client.get("/api/models/groups").json()
    assert body["roles"]["developer"] == "coder_fast"
    first = body["groups"]["strong_reasoning"][0]
    assert first["provider"] == "ollama" and first["model"] == "gpt-oss:latest"


def test_milestone_showcase_listed():
    body = client.get("/api/milestones").json()
    assert any(m["milestone_id"] == "AUTH-M1" for m in body["milestones"])


def test_milestone_detail_has_waves():
    body = client.get("/api/milestones/AUTH-M1").json()
    waves = {n["task_id"]: n["wave"] for n in body["nodes"]}
    assert waves == {"AUTH-017": 0, "AUTH-018": 1, "AUTH-019": 1}
    assert body["round_outcomes"][0]["AUTH-018"] == "escalate"


def test_milestone_unknown_404():
    assert client.get("/api/milestones/NOPE-M9").status_code == 404


def test_milestone_control_queues():
    res = client.post("/api/milestones/AUTH-M1/control",
                      json={"task_id": "AUTH-M1", "action": "approve"}).json()
    assert res["queued"] >= 1
    bad = client.post("/api/milestones/AUTH-M1/control",
                      json={"task_id": "AUTH-M1", "action": "nonsense"})
    assert bad.status_code == 422


def test_put_providers_accepts_custom_vendor(tmp_path, monkeypatch):
    # Open registry: unknown names become custom slots (blank seed + user base_url).
    import shutil

    import smallworks.config as config_mod
    import smallworks.service as service_mod

    real_dir = config_mod.default_config_dir()
    fake = tmp_path / "configs"
    fake.mkdir()
    shutil.copy(real_dir / "models.yaml", fake / "models.yaml")
    shutil.copy(real_dir / "workers.yaml", fake / "workers.yaml")
    shutil.copy(real_dir / "providers.yaml", fake / "providers.yaml")
    monkeypatch.setattr(config_mod, "default_config_dir", lambda: fake)
    monkeypatch.setattr(service_mod, "_dotenv_path", lambda: tmp_path / ".env")
    res = client.put(
        "/api/providers", json={"providers": {"mycloud": {"base_url": "http://x/v1"}}}
    )
    assert res.status_code == 200
    rows = {p["name"]: p for p in res.json()["providers"]}
    assert rows["mycloud"]["base_url"] == "http://x/v1"


def test_put_providers_rejects_bad_name():
    res = client.put("/api/providers", json={"providers": {"no pe": {"base_url": "http://x"}}})
    assert res.status_code == 422


def test_assign_model_rejected_for_unknown_role():
    res = client.put(
        "/api/models/assign",
        json={"role": "janitor", "provider": "github", "model": "openai/gpt-4o-mini"},
    )
    assert res.status_code == 422


def test_todos_crud_and_move():
    assert client.get("/api/todos").json() == {"todos": []}
    saved = client.put("/api/todos", json={"id": "wire-writer", "title": "Wire writer role"}).json()
    assert [t["id"] for t in saved["todos"]] == ["wire-writer"]
    moved = client.post("/api/todos/wire-writer/move", json={"status": "running"}).json()
    assert moved["status"] == "running"
    assert client.post("/api/todos/wire-writer/move", json={"status": "done-ish"}).status_code == 422
    assert client.post("/api/todos/nope/move", json={"status": "passed"}).status_code == 404
