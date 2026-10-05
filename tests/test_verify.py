"""Executed verification: measured pytest overrides model claims (grounded tests)."""

import textwrap

from smallworks.schemas import Decision, ImplementationTask, TestReport
from smallworks.schemas import validate_decision
from smallworks.verify import run_pytest, verify_task_tests
from smallworks.verify import tests_for_task as resolve_tests
from smallworks.workflow import Workflow, _apply_patch
from smallworks.workers.roles import WorkerError


def _task(**over) -> ImplementationTask:
    base = {
        "task_id": "AUTH-017",
        "module": "auth",
        "behaviour": "handle expired token",
        "allowed_files": ["tests/test_auth_probe.py"],
        "acceptance_criteria": ["expired token returns 401"],
    }
    base.update(over)
    return ImplementationTask.model_validate(base)


def _write(path, text):
    path.write_text(textwrap.dedent(text), encoding="utf-8")


def test_runner_measures_passing_suite(tmp_path):
    _write(tmp_path / "test_auth_probe.py", """
        def test_ok():
            assert 1 + 1 == 2
    """)
    done = run_pytest([tmp_path / "test_auth_probe.py"], cwd=tmp_path)
    assert done.passed and done.tests_run == 1 and done.tests_failed == 0


def test_runner_measures_failing_suite(tmp_path):
    _write(tmp_path / "test_auth_probe.py", """
        def test_broken():
            assert 1 + 1 == 3
    """)
    done = run_pytest([tmp_path / "test_auth_probe.py"], cwd=tmp_path)
    assert not done.passed and done.tests_failed == 1


def test_resolves_conventional_module_file(tmp_path):
    (tmp_path / "tests").mkdir()
    _write(tmp_path / "tests" / "test_auth.py", "def test_x():\n    assert True\n")
    found = resolve_tests(_task(allowed_files=["src/auth/token.py"]), tmp_path)
    assert [p.name for p in found] == ["test_auth.py"]


def test_missing_tests_verify_none(tmp_path):
    assert verify_task_tests(_task(), tmp_path) is None


def test_zero_run_report_cannot_pass():
    # passed=True with zero runs is the hollow claim: nothing measured.
    report = TestReport(task_id="AUTH-017", passed=True, tests_run=0, tests_failed=0)
    d = validate_decision(Decision(task_id="AUTH-017", action="pass"), test_report=report, review=None)
    assert d.action == "retry" and "no tests executed" in d.reason


def test_claimed_pass_without_files_fails_execution(tmp_path):
    """The LIVE-M1 lesson: model says passed, nothing on disk → measured fail."""
    from smallworks.workers.roles import _verify_report

    claimed = TestReport(task_id="AUTH-017", passed=True, tests_run=3, tests_failed=0)
    measured = _verify_report(_task(), claimed, tmp_path)
    assert not measured.passed and measured.tests_run == 0


def test_measured_pass_overrides_model_downgrade(tmp_path):
    """Override runs both directions: model pessimism cannot fail green files."""
    from smallworks.workers.roles import _verify_report

    (tmp_path / "tests").mkdir(exist_ok=True)
    _write(tmp_path / "tests" / "test_auth_probe.py", "def test_x():\n    assert True\n")
    claimed = TestReport(task_id="AUTH-017", passed=False, tests_run=1, tests_failed=1)
    task = _task()
    measured = _verify_report(task, claimed, tmp_path)
    assert measured.passed and measured.tests_run == 1


def test_apply_rejects_unwritten_files_under_verify(tmp_path):
    from smallworks.schemas import Patch

    patch = Patch(task_id="AUTH-017", files_changed=["src/auth/token.py"], summary="fix")
    try:
        _apply_patch(_task(), patch, tmp_path, verify=True)
    except WorkerError as exc:
        assert "unwritten" in str(exc)
    else:
        raise AssertionError("expected WorkerError")
    # Scripted runs keep names-only patches.
    _apply_patch(_task(), patch, tmp_path, verify=False)


def test_apply_materializes_contents(tmp_path):
    from smallworks.schemas import Patch

    patch = Patch(
        task_id="AUTH-017", files_changed=["pkg/mod.py"], summary="add",
        contents={"pkg/mod.py": "X = 1\n"},
    )
    _apply_patch(_task(), patch, tmp_path, verify=True)
    assert (tmp_path / "pkg" / "mod.py").read_text(encoding="utf-8") == "X = 1\n"


def test_workflow_rejects_claimed_pass_without_execution(tmp_path):
    """End-to-end: passing model text + real verify root with no test files → no pass."""
    import json
    import importlib.util
    from pathlib import Path

    import smallworks.workflow as workflow_mod

    spec = importlib.util.spec_from_file_location(
        "tw", Path("tests/test_workflow.py"))
    tw = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(tw)
    KeyedTransport, RoleGateway = tw.KeyedTransport, tw.RoleGateway
    _models, _policy, _workers = tw._models, tw._policy, tw._workers

    texts = {
        "engineer": json.dumps({"tasks": []}),
        "developer": json.dumps(
            {"files_changed": ["tests/test_auth_probe.py"], "summary": "fix",
             "contents": {"tests/test_auth_probe.py": "x = 1\n"}}
        ),
        "tester": json.dumps({"passed": True, "tests_run": 3, "tests_failed": 0}),
        "reviewer": json.dumps({"verdict": "PASS", "notes": "fine"}),
        "writer": "docs.",
    }
    gw = RoleGateway(_models(), _workers(), _policy(), transport=KeyedTransport(texts))
    # No tests/test_auth.py under tmp root → verify measures nothing → retry, never pass.
    (tmp_path / "tests").mkdir()
    result = workflow_mod.Workflow(gw, max_retries=0, worktree_root=str(tmp_path),
                                   verify=True).run_task(_task())
    assert result.decision.action != "pass"
    assert result.test_report is not None and result.test_report.tests_run == 0


def test_prompt_carries_existing_file_text():
    from smallworks.workers.prompts import developer_prompt

    task = _task()
    plain = developer_prompt(task, None)
    assert "Current file content" not in plain
    rich = developer_prompt(task, None, {"src/smallworks/textutils.py": "X = 1\n"})
    assert "--- src/smallworks/textutils.py ---" in rich and "X = 1" in rich
    for section in ("GOAL:", "CONTRACT", "SYMBOLS", "TESTS", "RULES:", "FILES"):
        assert section in rich
    absent = developer_prompt(task, None, {})
    assert "no in-scope files exist yet" in absent


def test_read_existing_skips_missing_and_non_python(tmp_path):
    from smallworks.workflow import _read_existing

    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "have.py").write_text("Y = 2\n", encoding="utf-8")
    got = _read_existing(
        _task(allowed_files=["src/have.py", "src/missing.py", "docs/notes.md"]), tmp_path)
    assert got == {"src/have.py": "Y = 2\n"}


def test_temperature_zero_reaches_wire_payload():
    from smallworks.gateway import OpenAICompatibleTransport, resolve_endpoint
    from smallworks.config import default_config_dir, load_configs

    d = default_config_dir()
    cfg = load_configs(d / "models.yaml", d / "workers.yaml", d / "providers.yaml")
    local = [x for x in cfg.models["coder_fast"] if x.provider == "ollama"][0]
    assert local.params.get("temperature") == 0
    resolved = resolve_endpoint(local, cfg.providers)
    assert resolved.params.get("temperature") == 0
    seen = {}

    class SpyTransport(OpenAICompatibleTransport):
        def complete(self, deployment, prompt, *, task_id):
            import httpx

            orig = httpx.post
            def spy(url, json=None, **kw):
                seen.update(json)
                raise RuntimeError("stop before network")

            httpx.post = spy
            try:
                return super().complete(deployment, prompt, task_id=task_id)
            finally:
                httpx.post = orig

    try:
        SpyTransport().complete(local, "hi", task_id="T-0")
    except RuntimeError:
        pass
    assert seen.get("temperature") == 0


def test_revise_block_absent_without_feedback():
    from smallworks.workers.prompts import developer_prompt

    assert "REVISE" not in developer_prompt(_task(), None, {})
    revised = developer_prompt(_task(), None, {}, "gate decision: retry — boom")
    assert "REVISE" in revised and "boom" in revised


def test_failure_feedback_names_measured_result():
    from smallworks.schemas import Decision, Patch
    from smallworks.workflow import _failure_feedback

    fb = _failure_feedback(
        Patch(task_id="AUTH-017", files_changed=["a.py"], summary="s"),
        TestReport(task_id="AUTH-017", passed=False, tests_run=2, tests_failed=1),
        None,
        Decision(task_id="AUTH-017", action="retry", reason="tests failing"),
    )
    assert "run=2 failed=1" in fb and "tests failing" in fb


def test_retry_carries_feedback_into_next_prompt():
    import json

    from smallworks.schemas import ImplementationTask
    from smallworks.workflow import Workflow
    from tests_helper import KeyedTransport, RoleGateway, _models, _policy, _workers

    texts = {
        "engineer": json.dumps({"tasks": []}),
        "developer": json.dumps({"files_changed": ["a.py"], "summary": "s",
                                 "contents": {"a.py": "x = 1\n"}}),
        "tester": json.dumps({"passed": False, "tests_run": 1, "tests_failed": 1}),
        "reviewer": json.dumps({"verdict": "RETRY", "notes": "red"}),
        "writer": "docs.",
    }
    prompts_seen = []
    base = KeyedTransport(texts)
    orig = base.complete

    class SpyTransport(KeyedTransport):
        def complete(self, deployment, prompt, *, task_id):
            prompts_seen.append(prompt)
            return orig(deployment, prompt, task_id=task_id)

    gw = RoleGateway(_models(), _workers(), _policy(), transport=SpyTransport(texts))
    task = ImplementationTask(task_id="AUTH-017", module="auth", behaviour="b",
                              allowed_files=["a.py"], acceptance_criteria=["c"])
    Workflow(gw, max_retries=1).run_task(task)
    dev_prompts = [x for x in prompts_seen if "GOAL: implement task" in x]
    assert len(dev_prompts) == 2
    assert "REVISE" in dev_prompts[1] and "red" in dev_prompts[1]
