import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.actions.contracts import ActionProposal
from app.approval.contracts import ApprovalDecisionType, ApprovalStatus
from app.approval.models import ApprovalTask
from app.approval.repository import ApprovalRepository
from app.approval.service import (
    ApprovalWorkflowMismatchError,
    ApprovalWorkflowService,
)
from app.persistence.base import Base
from app.workflow.models import WorkflowEvent
from app.workflow.repository import WorkflowRepository
from app.workflow.state import WorkflowState


@pytest.fixture
def session_factory() -> sessionmaker[Session]:
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, expire_on_commit=False)


def proposal() -> ActionProposal:
    return ActionProposal(
        action_id="action-1",
        action_type="grant_access",
        target_resource="application/CRM/user/EMP-1001",
        parameters={"role_code": "read_only"},
        version=1,
        content_summary="Grant CRM read-only access",
    )


def prepare_waiting_approval(
    session: Session,
    *,
    run_id: str = "run-1",
    approval_id: str = "approval-1",
) -> None:
    workflows = WorkflowRepository(session)
    workflows.create("access_management", run_id=run_id)
    workflows.transition(
        run_id,
        expected_version=0,
        target=WorkflowState.RUNNING,
        event_type="PROCESSING_STARTED",
    )
    workflows.transition(
        run_id,
        expected_version=1,
        target=WorkflowState.WAITING_APPROVAL,
        event_type="APPROVAL_REQUIRED",
    )
    ApprovalRepository(session).create(
        run_id,
        proposal(),
        "EMP-MANAGER",
        approval_id=approval_id,
    )


@pytest.mark.parametrize(
    ("decision", "expected_approval", "expected_workflow", "expected_event"),
    [
        (
            ApprovalDecisionType.APPROVE,
            ApprovalStatus.APPROVED,
            WorkflowState.RUNNING,
            "APPROVAL_APPROVED",
        ),
        (
            ApprovalDecisionType.REJECT,
            ApprovalStatus.REJECTED,
            WorkflowState.COMPLETED,
            "APPROVAL_REJECTED",
        ),
    ],
)
def test_decision_resumes_or_completes_same_workflow(
    session_factory: sessionmaker[Session],
    decision: ApprovalDecisionType,
    expected_approval: ApprovalStatus,
    expected_workflow: WorkflowState,
    expected_event: str,
) -> None:
    with session_factory.begin() as session:
        prepare_waiting_approval(session)
        result = ApprovalWorkflowService(
            ApprovalRepository(session), WorkflowRepository(session)
        ).decide(
            workflow_run_id="run-1",
            expected_workflow_version=2,
            approval_id="approval-1",
            actor_id="EMP-MANAGER",
            decision=decision,
        )

        assert result.approval_status is expected_approval
        assert result.workflow_state is expected_workflow
        assert result.workflow_version == 3
        events = session.scalars(
            select(WorkflowEvent)
            .where(WorkflowEvent.run_id == "run-1")
            .order_by(WorkflowEvent.sequence)
        ).all()
        assert events[-1].event_type == expected_event
        assert events[-1].payload["approval_id"] == "approval-1"


def test_approval_cannot_change_another_workflow(
    session_factory: sessionmaker[Session],
) -> None:
    with session_factory.begin() as session:
        prepare_waiting_approval(session)

        with pytest.raises(ApprovalWorkflowMismatchError):
            ApprovalWorkflowService(
                ApprovalRepository(session), WorkflowRepository(session)
            ).decide(
                workflow_run_id="run-other",
                expected_workflow_version=2,
                approval_id="approval-1",
                actor_id="EMP-MANAGER",
                decision=ApprovalDecisionType.APPROVE,
            )

        task = session.get(ApprovalTask, "approval-1")
        assert task is not None
        assert task.approval_status is ApprovalStatus.PENDING

