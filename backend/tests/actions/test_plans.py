import pytest
from pydantic import ValidationError
from sqlalchemy import create_engine
from sqlalchemy.dialects import mysql
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool
from sqlalchemy.schema import CreateTable

from app.actions.contracts import (
    ActionPlan,
    ActionPlanStatus,
    ActionPlanStep,
    ActionPlanStepStatus,
    ActionProposal,
    ActionStepReversibility,
)
from app.actions.models import ActionPlanRecord, ActionPlanStepRecord
from app.actions.plans import ActionPlanRepository, ActionPlanVersionError
from app.persistence.base import Base


@pytest.fixture
def session_factory() -> sessionmaker[Session]:
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, expire_on_commit=False)


def proposal(action_id: str, action_type: str) -> ActionProposal:
    return ActionProposal(
        action_id=action_id,
        action_type=action_type,
        target_resource=f"employee/EMP-3001/{action_type}",
        parameters={"subject_employee_id": "EMP-3001"},
        version=1,
        content_summary=action_type,
    )


def plan(version: int = 1) -> ActionPlan:
    return ActionPlan(
        plan_id="plan-1",
        scenario_key="employee_lifecycle",
        plan_type="ONBOARDING",
        subject_reference="EMP-3001",
        version=version,
        content_summary="为EMP-3001办理入职",
        steps=(
            ActionPlanStep(
                step_id="step-1",
                step_order=1,
                proposal=proposal("action-1", "create_pending_employee"),
                reversibility=ActionStepReversibility.MANUAL_ONLY,
            ),
            ActionPlanStep(
                step_id="step-2",
                step_order=2,
                depends_on_step_ids=("step-1",),
                proposal=proposal(
                    "action-2",
                    "create_disabled_corporate_account",
                ),
                reversibility=ActionStepReversibility.REVERSIBLE,
                compensation_action_type="remove_disabled_corporate_account",
            ),
        ),
    )


def test_plan_digest_protects_steps_dependencies_and_order() -> None:
    original = plan()
    changed = original.model_copy(
        update={
            "steps": (
                original.steps[0],
                original.steps[1].model_copy(
                    update={
                        "proposal": original.steps[1].proposal.model_copy(
                            update={"parameters": {"subject_employee_id": "EMP-OTHER"}}
                        )
                    }
                ),
            )
        }
    )

    assert len(original.content_digest) == 64
    assert changed.content_digest != original.content_digest


def test_plan_rejects_missing_or_forward_dependencies() -> None:
    with pytest.raises(ValidationError, match="dependency does not exist"):
        plan().model_copy(
            update={
                "steps": (
                    plan().steps[0],
                    plan().steps[1].model_copy(
                        update={"depends_on_step_ids": ("missing",)}
                    ),
                )
            }
        ).model_validate(
            plan().model_copy(
                update={
                    "steps": (
                        plan().steps[0],
                        plan().steps[1].model_copy(
                            update={"depends_on_step_ids": ("missing",)}
                        ),
                    )
                }
            ).model_dump()
        )

    with pytest.raises(ValidationError, match="earlier step"):
        ActionPlan(
            plan_id="plan-forward",
            scenario_key="employee_lifecycle",
            plan_type="ONBOARDING",
            subject_reference="EMP-3001",
            content_summary="非法依赖",
            steps=(
                ActionPlanStep(
                    step_id="step-1",
                    step_order=1,
                    depends_on_step_ids=("step-2",),
                    proposal=proposal("forward-1", "first"),
                ),
                ActionPlanStep(
                    step_id="step-2",
                    step_order=2,
                    proposal=proposal("forward-2", "second"),
                ),
            ),
        )


def test_plan_repository_round_trips_content_and_initial_step_states(
    session_factory: sessionmaker[Session],
) -> None:
    with session_factory.begin() as session:
        repository = ActionPlanRepository(session)
        record = repository.add(
            "run-1",
            plan(),
            status=ActionPlanStatus.PENDING_APPROVAL,
        )
        loaded = repository.get("plan-1", 1)
        repository.require_current(loaded)

        assert loaded == plan()
        assert record.status == ActionPlanStatus.PENDING_APPROVAL.value
        assert [step.status for step in record.steps] == [
            ActionPlanStepStatus.PENDING.value,
            ActionPlanStepStatus.BLOCKED.value,
        ]


def test_older_plan_version_is_not_current(
    session_factory: sessionmaker[Session],
) -> None:
    with session_factory.begin() as session:
        repository = ActionPlanRepository(session)
        repository.add("run-1", plan(1))
        newer = plan(2).model_copy(
            update={
                "steps": tuple(
                    step.model_copy(
                        update={
                            "proposal": step.proposal.model_copy(
                                update={"version": 2}
                            )
                        }
                    )
                    for step in plan(2).steps
                )
            }
        )
        repository.add("run-1", newer)

        with pytest.raises(ActionPlanVersionError):
            repository.require_current(plan(1))


def test_plan_tables_compile_for_mysql() -> None:
    plan_ddl = str(
        CreateTable(ActionPlanRecord.__table__).compile(dialect=mysql.dialect())
    )
    step_ddl = str(
        CreateTable(ActionPlanStepRecord.__table__).compile(dialect=mysql.dialect())
    )

    assert "CREATE TABLE action_plans" in plan_ddl
    assert "CREATE TABLE action_plan_steps" in step_ddl
    assert "fk_action_plan_step_proposal" in step_ddl
    assert "JSON" in step_ddl
