"""Eval tests: both arms run, metrics side-by-side, report reproduces (plan 06)."""

import json

from smallworks.eval import (
    ScriptedTransport,
    compare,
    fixture_tasks,
    load_report,
    role_texts,
    run_baseline,
    run_smallworks_arm,
    save_report,
)


def _patches() -> dict[str, list[str]]:
    return {"EVAL-001": ["src/auth/token.py"], "EVAL-002": ["src/billing/invoice.py"]}


def test_both_arms_complete_same_task_set(tmp_path):
    tasks = fixture_tasks()
    texts = role_texts()
    sw = run_smallworks_arm(
        tasks, transport=ScriptedTransport(dict(texts), patch_files=_patches()),
        worktree_root=str(tmp_path),
    )
    bl = run_baseline(tasks, transport=ScriptedTransport(dict(texts)))
    assert sw.tasks == bl.tasks == 2
    assert sw.completed == 2 and bl.completed == 2
    assert sw.tests_run == 8 and bl.tests_run == 8

def test_retry_path_recorded_in_metrics(tmp_path):
    tasks = fixture_tasks()
    texts = role_texts()
    # Fail the tester once for EVAL-001: workflow retries, metrics record it.
    transport = ScriptedTransport(
        dict(texts), fail_plan={("EVAL-001", "tester"): 1}, patch_files=_patches()
    )
    sw = run_smallworks_arm(tasks, transport=transport, worktree_root=str(tmp_path))
    assert sw.retries >= 1  # one scripted tester failure forces a develop<->test loop
    assert sw.completed == 2


def test_comparison_verdict_supported(tmp_path):
    tasks = fixture_tasks()
    texts = role_texts()
    sw = run_smallworks_arm(
        tasks, transport=ScriptedTransport(dict(texts), patch_files=_patches()),
        worktree_root=str(tmp_path),
    )
    bl = run_baseline(tasks, transport=ScriptedTransport(dict(texts)))
    report = compare(bl, sw)
    assert set(report["metrics"]) >= {"completion", "api_cost", "context_chars", "retries"}
    assert report["baseline"]["arm"] == "baseline"
    assert report["smallworks"]["arm"] == "smallworks"
    # SmallWorks bounded context (2 tasks x 8000) beats full-repo baseline (2 x 50000+).
    assert report["verdict"] == "SUPPORTED"


def test_report_reproduces_from_artefacts(tmp_path):
    path = tmp_path / "eval-report.json"
    tasks = fixture_tasks()
    texts = role_texts()
    sw = run_smallworks_arm(
        tasks, transport=ScriptedTransport(dict(texts), patch_files=_patches()),
        worktree_root=str(tmp_path),
    )
    bl = run_baseline(tasks, transport=ScriptedTransport(dict(texts)))
    report = compare(bl, sw)
    save_report(report, texts, path)
    reloaded = load_report(path)
    assert reloaded["report"]["verdict"] == report["verdict"]
    assert reloaded["raw_outputs"]["reviewer"] == texts["reviewer"]
    assert json.loads(path.read_text())["report"]["smallworks"]["completion"] == "2/2"
