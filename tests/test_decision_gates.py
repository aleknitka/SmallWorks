from smallworks.schemas import Decision, ReviewReport
from smallworks.schemas import TestReport as TestReportModel
from smallworks.schemas import validate_decision


def _report(passed: bool, failed: int = 0) -> TestReportModel:
    return TestReportModel(task_id="AUTH-017", passed=passed, tests_run=3, tests_failed=failed)


def test_failing_tests_block_pass():
    d = validate_decision(
        Decision(task_id="AUTH-017", action="pass"),
        test_report=_report(False, 1),
        review=None,
    )
    assert d.action == "retry"


def test_non_pass_review_blocks_pass():
    d = validate_decision(
        Decision(task_id="AUTH-017", action="pass"),
        test_report=_report(True),
        review=ReviewReport(task_id="AUTH-017", verdict="RETRY"),
    )
    assert d.action == "retry"


def test_forbidden_files_reject():
    d = validate_decision(
        Decision(task_id="AUTH-017", action="pass"),
        test_report=_report(True),
        review=ReviewReport(task_id="AUTH-017", verdict="PASS"),
        forbidden_files_touched=True,
    )
    assert d.action == "retry"


def test_security_failure_escalates():
    d = validate_decision(
        Decision(task_id="AUTH-017", action="pass"),
        test_report=_report(True),
        review=ReviewReport(task_id="AUTH-017", verdict="PASS"),
        security_gate_passed=False,
    )
    assert d.action == "escalate"


def test_clean_pass_stays_pass():
    d = validate_decision(
        Decision(task_id="AUTH-017", action="pass"),
        test_report=_report(True),
        review=ReviewReport(task_id="AUTH-017", verdict="PASS"),
    )
    assert d.action == "pass"

