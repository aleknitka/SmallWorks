"""Schema round-trips for plan-01 artefacts (spec §11)."""

import pytest
from pydantic import ValidationError

from smallworks.schemas import (
    Blueprint,
    EngineeringPlan,
    ImplementationTask,
    ModuleSpec,
    Patch,
    ProjectSpec,
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
