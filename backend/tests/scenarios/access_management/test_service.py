import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.persistence.base import Base
from app.policy.contracts import PolicyOutcome
from app.scenarios.access_management.contracts import (
    AccessRequestContext,
    AccessRequestDraft,
)
from app.scenarios.access_management.service import AccessRequestIntakeService
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


def draft(**overrides: object) -> AccessRequestDraft:
    values: dict[str, object] = {
        "employee_id": "EMP-1001",
        "application_code": "CRM",
        "role_code": "read_only",
        "duration_days": 30,
        "business_reason": "Participate in the East China customer project",
    }
    values.update(overrides)
    return AccessRequestDraft(**values)


def intake_service(session: Session) -> AccessRequestIntakeService:
    return AccessRequestIntakeService(
        WorkflowRepository(session),
        ActionProposalRepository(session),
        ApprovalRepository(session),
    )


@pytest.mark.parametrize(
    ("access_request", "context", "expected_state", "expected_outcome"),
    [
        (
            AccessRequestDraft(employee_id="EMP-1001"),
            AccessRequestContext(),
            WorkflowState.WAITING_USER,
            PolicyOutcome.NEEDS_INPUT,
        ),
        (
            draft(),
            AccessRequestContext(manager_id="EMP-MANAGER"),
            WorkflowState.WAITING_APPROVAL,
            PolicyOutcome.APPROVAL_REQUIRED,
        ),
        (
            draft(role_code="admin"),
            AccessRequestContext(),
            WorkflowState.WAITING_HUMAN,
            PolicyOutcome.HUMAN_REVIEW,
        ),
        (
            draft(duration_days=91),
            AccessRequestContext(),
            WorkflowState.COMPLETED,
            PolicyOutcome.DENIED,
        ),
        (
            draft(),
            AccessRequestContext(existing_role_codes=frozenset({"read_only"})),
            WorkflowState.COMPLETED,
            PolicyOutcome.NO_ACTION,
        ),
    ],
)
def test_intake_routes_policy_outcome_to_workflow_state(
    session_factory: sessionmaker[Session],
    access_request: AccessRequestDraft,
    context: AccessRequestContext,
    expected_state: WorkflowState,
    expected_outcome: PolicyOutcome,
) -> None:
    with session_factory.begin() as session:
        result = intake_service(session).start(
            access_request,
            context,
            run_id="run-1",
            action_id="action-1",
            approval_id="approval-1",
        )

        assert result.workflow_state is expected_state
        assert result.workflow_version == 2
        assert result.policy_decision.outcome is expected_outcome
        if expected_outcome is PolicyOutcome.APPROVAL_REQUIRED:
            assert result.action_proposal is not None
            assert result.action_proposal.action_id == "action-1"
            assert result.approval_id == "approval-1"
        else:
            assert result.action_proposal is None
            assert result.approval_id is None


def test_intake_writes_three_ordered_audit_events(
    session_factory: sessionmaker[Session],
) -> None:
    with session_factory.begin() as session:
        intake_service(session).start(
            draft(),
            AccessRequestContext(manager_id="EMP-MANAGER"),
            run_id="run-1",
            action_id="action-1",
            approval_id="approval-1",
        )

        events = session.scalars(
            select(WorkflowEvent)
            .where(WorkflowEvent.run_id == "run-1")
            .order_by(WorkflowEvent.sequence)
        ).all()

        assert [event.event_type for event in events] == [
            "WORKFLOW_CREATED",
            "REQUEST_PROCESSING_STARTED",
            "APPROVAL_REQUIRED",
        ]
        assert events[-1].payload["policy_decision"]["approver_id"] == "EMP-MANAGER"
        assert events[-1].payload["action_id"] == "action-1"
        assert events[-1].payload["approval_id"] == "approval-1"
        assert "reasoning" not in events[-1].payload


def test_approval_required_persists_proposal_and_pending_task_together(
    session_factory: sessionmaker[Session],
) -> None:
    with session_factory.begin() as session:
        result = intake_service(session).start(
            draft(),
            AccessRequestContext(manager_id="EMP-MANAGER"),
            run_id="run-1",
            action_id="action-1",
            approval_id="approval-1",
        )

        proposal_record = session.scalar(
            select(ActionProposalRecord).where(
                ActionProposalRecord.action_id == "action-1"
            )
        )
        approval = session.get(ApprovalTask, "approval-1")

        assert proposal_record is not None
        assert approval is not None
        assert approval.approval_status is ApprovalStatus.PENDING
        assert approval.action_digest == result.action_proposal.content_digest
        assert approval.approver_id == "EMP-MANAGER"


def test_waiting_user_can_resume_same_run_and_create_approval(
    session_factory: sessionmaker[Session],
) -> None:
    with session_factory.begin() as session:
        service = intake_service(session)
        waiting = service.start(
            AccessRequestDraft(employee_id="EMP-1001"),
            AccessRequestContext(),
            run_id="run-1",
        )
        assert waiting.workflow_state is WorkflowState.WAITING_USER

        resumed = service.resume_with_information(
            draft(),
            AccessRequestContext(manager_id="EMP-MANAGER"),
            run_id="run-1",
            expected_version=waiting.workflow_version,
            provided_fields=(
                "application_code",
                "role_code",
                "duration_days",
                "business_reason",
            ),
            action_id="action-1",
            approval_id="approval-1",
        )

        assert resumed.workflow_state is WorkflowState.WAITING_APPROVAL
        assert resumed.workflow_version == 4
        events = session.scalars(
            select(WorkflowEvent)
            .where(WorkflowEvent.run_id == "run-1")
            .order_by(WorkflowEvent.sequence)
        ).all()
        assert [event.event_type for event in events][-2:] == [
            "REQUEST_INFORMATION_RECEIVED",
            "APPROVAL_REQUIRED",
        ]
        assert events[-2].payload["request_draft"]["application_code"] == "CRM"
from app.actions.models import ActionProposalRecord
from app.actions.repository import ActionProposalRepository
from app.approval.contracts import ApprovalStatus
from app.approval.models import ApprovalTask
from app.approval.repository import ApprovalRepository
