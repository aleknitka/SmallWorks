"""Schema round-trips for plan-01 artefacts (spec §11)."""

import pytest
from pydantic import ValidationError

from smallworks.schemas import (
    Blueprint,
    Decision,
    EngineeringPlan,
    ImplementationTask,
    Milestone,
    ModuleSpec,
    Patch,
    ProjectSpec,
    validate_milestone,
)


def _task(task_id: str = "AUTH-017") -> ImplementationTask:
    return ImplementationTask(
        task_id=task_id,
        module="auth",
        behaviour="handle expired token",
        allowed_files=["src/auth/token.py"],
        acceptance_criteria=["expired token returns 401"],
    )


def test_project_spec_roundtrip():
    spec = ProjectSpec(name="SmallWorks", goal="ship MVP", acceptance_criteria=["tests green"])
    assert ProjectSpec.model_validate(spec.model_dump()).name == "SmallWorks"


def test_blueprint_requires_module():
    with pytest.raises(ValidationError):
        Blueprint(project="p", modules=[], acceptance_criteria=["x"])


def test_module_spec_roundtrip():
    module = ModuleSpec(
        name="auth",
        responsibility="tokens",
        interface="validate(token) -> bool",
        acceptance_criteria=["expired rejected"],
    )
    blueprint = Blueprint(project="p", modules=[module], acceptance_criteria=["auth works"])
    assert Blueprint.model_validate(blueprint.model_dump()).modules[0].name == "auth"


def test_engineering_plan_roundtrip_and_requires_task():
    plan = EngineeringPlan(project="p", tasks=[_task()])
    assert EngineeringPlan.model_validate(plan.model_dump()).tasks[0].task_id == "AUTH-017"
    with pytest.raises(ValidationError):
        EngineeringPlan(project="p", tasks=[])


def test_patch_roundtrip_and_rejects_empty_files():
    patch = Patch(task_id="AUTH-017", files_changed=["src/auth/token.py"], summary="fix expiry check")
    assert Patch.model_validate(patch.model_dump()).files_changed == ["src/auth/token.py"]
    with pytest.raises(ValidationError):
        Patch(task_id="AUTH-017", files_changed=[], summary="empty")


def test_patch_rejects_bad_task_id():
    with pytest.raises(ValidationError):
        Patch(task_id="nope", files_changed=["f.py"], summary="bad id")


def _milestone(**over) -> Milestone:
    base: dict = {
        "milestone_id": "AUTH-M1",
        "engineering_plan": "plan-auth",
        "predicate": "all_tasks_pass",
        "tasks": ["AUTH-017", "AUTH-018"],
    }
    base.update(over)
    return Milestone.model_validate(base)


def _decisions(*actions: str, retryable: bool = False) -> dict[str, Decision]:
    tids = ["AUTH-017", "AUTH-018"]
    return {t: Decision(task_id=t, action=a, retryable=retryable) for t, a in zip(tids, actions)}


def test_milestone_met_when_all_pass():
    assert validate_milestone(_milestone(), _decisions("pass", "pass")).verdict == "met"


def test_milestone_open_on_retry_or_missing():
    assert validate_milestone(_milestone(), _decisions("pass", "retry")).verdict == "open"
    assert validate_milestone(_milestone(), _decisions("pass")).verdict == "open"


def test_milestone_breached_on_escalate():
    m = validate_milestone(_milestone(), _decisions("pass", "escalate"))
    assert m.verdict == "breached"


def test_milestone_open_on_retryable_escalate():
    # Retry budget spent: another round with fresh context may succeed.
    m = validate_milestone(_milestone(), _decisions("pass", "escalate", retryable=True))
    assert m.verdict == "open"


def test_milestone_strict_predicate_breaches_instead_of_lingering():
    m = _milestone(predicate="no_open_escalations")
    assert validate_milestone(m, _decisions("pass", "pass")).verdict == "met"
    assert validate_milestone(m, _decisions("pass", "retry")).verdict == "breached"
    # Retryable failures keep the lenient predicate open — a round may fix them.
    assert validate_milestone(_milestone(), _decisions("pass", "retry")).verdict == "open"


def test_milestone_rejects_bad_id():
    with pytest.raises(ValidationError):
        _milestone(milestone_id="nope")
