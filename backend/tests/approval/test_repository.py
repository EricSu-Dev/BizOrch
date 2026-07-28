import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.dialects import mysql
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool
from sqlalchemy.schema import CreateTable

from app.actions.contracts import (
    ActionPlan,
    ActionPlanStep,
    ActionProposal,
    ActionStepReversibility,
)
from app.approval.contracts import ApprovalDecisionType, ApprovalStatus
from app.approval.models import ApprovalDecisionRecord, ApprovalTask
from app.approval.repository import (
    ApprovalActorMismatchError,
    ApprovalConflictError,
    ApprovalRepository,
    ApprovalValidationError,
)
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


def proposal(**overrides: object) -> ActionProposal:
    values: dict[str, object] = {
        "action_id": "action-1",
        "action_type": "grant_access",
        "target_resource": "application/CRM/user/EMP-1001",
        "parameters": {"role_code": "read_only", "duration_days": 30},
        "version": 1,
        "content_summary": "Grant CRM read-only access for 30 days",
    }
    values.update(overrides)
    return ActionProposal(**values)


def composite_plan(**overrides: object) -> ActionPlan:
    values: dict[str, object] = {
        "plan_id": "plan-1",
        "scenario_key": "employee_lifecycle",
        "plan_type": "ONBOARDING",
        "subject_reference": "EMP-3001",
        "version": 1,
        "content_summary": "为EMP-3001办理入职",
        "steps": (
            ActionPlanStep(
                step_id="step-1",
                step_order=1,
                proposal=proposal(),
                reversibility=ActionStepReversibility.MANUAL_ONLY,
            ),
        ),
    }
    values.update(overrides)
    return ActionPlan(**values)


def test_approval_binds_exact_action_version_and_digest(
    session_factory: sessionmaker[Session],
) -> None:
    with session_factory.begin() as session:
        repository = ApprovalRepository(session)
        task = repository.create(
            "run-1", proposal(), "EMP-MANAGER", approval_id="approval-1"
        )
        task = repository.decide(
            task.id,
            actor_id="EMP-MANAGER",
            decision=ApprovalDecisionType.APPROVE,
            comment="Approved for the project",
        )

        assert task.approval_status is ApprovalStatus.APPROVED
        assert repository.require_approved(task.id, proposal()).id == task.id
        decision = session.scalar(
            select(ApprovalDecisionRecord).where(
                ApprovalDecisionRecord.approval_id == task.id
            )
        )
        assert decision is not None
        assert decision.decided_by == "EMP-MANAGER"


@pytest.mark.parametrize(
    "changed_proposal",
    [
        proposal(version=2),
        proposal(parameters={"role_code": "admin", "duration_days": 30}),
        proposal(target_resource="application/ERP/user/EMP-1001"),
    ],
)
def test_changed_action_invalidates_existing_approval(
    session_factory: sessionmaker[Session],
    changed_proposal: ActionProposal,
) -> None:
    with session_factory.begin() as session:
        repository = ApprovalRepository(session)
        repository.create(
            "run-1", proposal(), "EMP-MANAGER", approval_id="approval-1"
        )
        repository.decide(
            "approval-1",
            actor_id="EMP-MANAGER",
            decision=ApprovalDecisionType.APPROVE,
        )

        with pytest.raises(ApprovalValidationError):
            repository.require_approved("approval-1", changed_proposal)


def test_only_assigned_approver_can_decide(
    session_factory: sessionmaker[Session],
) -> None:
    with session_factory.begin() as session:
        repository = ApprovalRepository(session)
        repository.create(
            "run-1", proposal(), "EMP-MANAGER", approval_id="approval-1"
        )

        with pytest.raises(ApprovalActorMismatchError):
            repository.decide(
                "approval-1",
                actor_id="EMP-OTHER",
                decision=ApprovalDecisionType.APPROVE,
            )

        assert repository.get("approval-1").approval_status is ApprovalStatus.PENDING


def test_approval_binds_exact_plan_and_only_its_steps(
    session_factory: sessionmaker[Session],
) -> None:
    with session_factory.begin() as session:
        repository = ApprovalRepository(session)
        bound_plan = composite_plan()
        repository.create_for_plan(
            "run-1",
            bound_plan,
            "EMP-MANAGER",
            approval_id="approval-plan-1",
        )
        repository.decide(
            "approval-plan-1",
            actor_id="EMP-MANAGER",
            decision=ApprovalDecisionType.APPROVE,
        )

        assert repository.require_plan_approved(
            "approval-plan-1",
            bound_plan,
        ).id == "approval-plan-1"
        assert repository.require_approved_plan_step(
            "approval-plan-1",
            bound_plan,
            bound_plan.steps[0].proposal,
        ).id == "approval-plan-1"

        with pytest.raises(ApprovalValidationError):
            repository.require_plan_approved(
                "approval-plan-1",
                bound_plan.model_copy(update={"version": 2}),
            )
        with pytest.raises(ApprovalValidationError):
            repository.require_approved_plan_step(
                "approval-plan-1",
                bound_plan,
                proposal(action_id="other-action"),
            )


def test_approval_can_only_receive_one_final_decision(
    session_factory: sessionmaker[Session],
) -> None:
    with session_factory.begin() as session:
        repository = ApprovalRepository(session)
        repository.create(
            "run-1", proposal(), "EMP-MANAGER", approval_id="approval-1"
        )
        repository.decide(
            "approval-1",
            actor_id="EMP-MANAGER",
            decision=ApprovalDecisionType.REJECT,
        )

        with pytest.raises(ApprovalConflictError):
            repository.decide(
                "approval-1",
                actor_id="EMP-MANAGER",
                decision=ApprovalDecisionType.APPROVE,
            )
        with pytest.raises(ApprovalValidationError):
            repository.require_approved("approval-1", proposal())


def test_approval_tables_compile_for_mysql() -> None:
    approval_ddl = str(
        CreateTable(ApprovalTask.__table__).compile(dialect=mysql.dialect())
    )
    decision_ddl = str(
        CreateTable(ApprovalDecisionRecord.__table__).compile(dialect=mysql.dialect())
    )

    assert "CREATE TABLE approvals" in approval_ddl
    assert "CREATE TABLE approval_decisions" in decision_ddl
