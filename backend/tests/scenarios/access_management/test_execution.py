from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker

from app.actions.contracts import ActionGatewayOutcome
from app.actions.execution import ActionExecutionRepository
from app.actions.gateway import ActionGateway
from app.actions.idempotency import PersistentIdempotencyStore
from app.actions.models import ActionExecutionRecord, IdempotencyRecord
from app.actions.validation import (
    PersistentApprovalValidator,
    PersistentProposalVersionValidator,
)
from app.approval.contracts import ApprovalDecisionType
from app.approval.repository import ApprovalRepository
from app.approval.service import ApprovalWorkflowService
from app.integrations.enterprise_ops import (
    EnterpriseOpsApplication,
    EnterpriseOpsEmployee,
    EnterpriseOpsUserAccess,
    EnterpriseOpsVerification,
    EnterpriseOpsWriteResult,
    EnterpriseOpsWriteStatus,
)
from app.persistence.base import Base
from app.scenarios.access_management.contracts import (
    AccessRequestContext,
    AccessRequestDraft,
)
from app.scenarios.access_management.execution import (
    AccessActionAuthorization,
    AccessAlreadySatisfiedError,
    AccessExecutionPolicyGuard,
    AccessGrantVerifier,
    AccessGrantWriteTool,
    AccessLatestStateChecker,
    AccessRequestExecutionService,
)
from app.scenarios.access_management.service import AccessRequestIntakeService
from app.actions.repository import ActionProposalRepository
from app.workflow.models import WorkflowEvent
from app.workflow.repository import WorkflowRepository
from app.workflow.state import WorkflowState


class FakeEnterpriseClient:
    def __init__(
        self,
        *,
        already_active: bool = False,
        grant_error: Exception | None = None,
        verification_confirmed: bool = True,
    ) -> None:
        self.employee = EnterpriseOpsEmployee(
            employee_id="EMP-1001",
            display_name="Lin Employee",
            department_code="SALES-EAST",
            manager_id="EMP-MANAGER",
            active=True,
        )
        self.application = EnterpriseOpsApplication(
            application_code="CRM",
            display_name="CRM",
            active=True,
            allowed_role_codes=("read_only", "standard", "admin"),
        )
        self.accesses: list[EnterpriseOpsUserAccess] = []
        if already_active:
            self._add_access()
        self.grant_error = grant_error
        self.verification_confirmed = verification_confirmed
        self.write_calls = 0

    def _add_access(self) -> EnterpriseOpsUserAccess:
        access = EnterpriseOpsUserAccess(
            access_id="access-1",
            employee_id="EMP-1001",
            application_code="CRM",
            role_code="read_only",
            expires_at=datetime.now(UTC) + timedelta(days=30),
            active=True,
        )
        self.accesses[:] = [access]
        return access

    def query_employee(self, employee_id: str) -> EnterpriseOpsEmployee:
        return self.employee

    def query_application(self, application_code: str) -> EnterpriseOpsApplication:
        return self.application

    def query_user_access(
        self, employee_id: str
    ) -> tuple[EnterpriseOpsUserAccess, ...]:
        return tuple(self.accesses)

    def grant_application_access(
        self, payload, *, idempotency_key: str
    ) -> EnterpriseOpsWriteResult:
        self.write_calls += 1
        if self.grant_error is not None:
            raise self.grant_error
        access = self._add_access()
        return EnterpriseOpsWriteResult(
            status=EnterpriseOpsWriteStatus.GRANTED,
            resource_id=access.access_id,
        )

    def verify_application_access(self, payload) -> EnterpriseOpsVerification:
        access = self.accesses[0] if self.accesses else None
        return EnterpriseOpsVerification(
            confirmed=self.verification_confirmed and access is not None,
            access=access if self.verification_confirmed else None,
        )


@pytest.fixture
def session_factory(tmp_path) -> sessionmaker[Session]:
    engine = create_engine(f"sqlite+pysqlite:///{tmp_path / 'bizorch.db'}")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, expire_on_commit=False)


def prepare_approved_request(session_factory: sessionmaker[Session]) -> None:
    draft = AccessRequestDraft(
        employee_id="EMP-1001",
        application_code="CRM",
        role_code="read_only",
        duration_days=30,
        business_reason="Participate in a customer project",
    )
    with session_factory.begin() as session:
        result = AccessRequestIntakeService(
            WorkflowRepository(session),
            ActionProposalRepository(session),
            ApprovalRepository(session),
        ).start(
            draft,
            AccessRequestContext(manager_id="EMP-MANAGER"),
            run_id="run-1",
            action_id="action-1",
            approval_id="approval-1",
        )
        assert result.workflow_state is WorkflowState.WAITING_APPROVAL

    with session_factory.begin() as session:
        result = ApprovalWorkflowService(
            ApprovalRepository(session), WorkflowRepository(session)
        ).decide(
            workflow_run_id="run-1",
            expected_workflow_version=2,
            approval_id="approval-1",
            actor_id="EMP-MANAGER",
            decision=ApprovalDecisionType.APPROVE,
        )
        assert result.workflow_state is WorkflowState.RUNNING


def build_execution_service(
    session_factory: sessionmaker[Session],
    client: FakeEnterpriseClient,
) -> AccessRequestExecutionService:
    gateway = ActionGateway(
        authorization=AccessActionAuthorization(
            frozenset({"system-action-executor"})
        ),
        policy=AccessExecutionPolicyGuard(),
        approvals=PersistentApprovalValidator(session_factory),
        proposals=PersistentProposalVersionValidator(session_factory),
        latest_state=AccessLatestStateChecker(client),
        idempotency=PersistentIdempotencyStore(session_factory),
        tool=AccessGrantWriteTool(client),
        verifier=AccessGrantVerifier(client),
        executions=ActionExecutionRepository(session_factory),
    )
    return AccessRequestExecutionService(session_factory, gateway)


def execute(service: AccessRequestExecutionService):
    return service.execute(
        workflow_run_id="run-1",
        expected_workflow_version=3,
        action_id="action-1",
        action_version=1,
        approval_id="approval-1",
        actor_id="system-action-executor",
        idempotency_key="grant:action-1:v1",
    )


def test_approved_request_executes_and_verifies_full_vertical_slice(
    session_factory: sessionmaker[Session],
) -> None:
    prepare_approved_request(session_factory)
    client = FakeEnterpriseClient()

    result = execute(build_execution_service(session_factory, client))

    assert result.workflow_state is WorkflowState.COMPLETED
    assert result.workflow_version == 5
    assert result.gateway_result is not None
    assert result.gateway_result.outcome is ActionGatewayOutcome.SUCCEEDED
    assert client.write_calls == 1
    with session_factory() as session:
        assert session.get(IdempotencyRecord, "grant:action-1:v1") is not None
        execution = session.scalar(select(ActionExecutionRecord))
        assert execution is not None
        assert execution.status == "SUCCEEDED"
        events = session.scalars(
            select(WorkflowEvent)
            .where(WorkflowEvent.run_id == "run-1")
            .order_by(WorkflowEvent.sequence)
        ).all()
        assert [event.event_type for event in events][-2:] == [
            "ACTION_EXECUTION_STARTED",
            "ACTION_SUCCEEDED",
        ]


def test_access_that_became_active_while_waiting_skips_write(
    session_factory: sessionmaker[Session],
) -> None:
    prepare_approved_request(session_factory)
    client = FakeEnterpriseClient(already_active=True)

    result = execute(build_execution_service(session_factory, client))

    assert result.workflow_state is WorkflowState.COMPLETED
    assert result.already_satisfied
    assert client.write_calls == 0


def test_write_timeout_moves_workflow_to_human_review(
    session_factory: sessionmaker[Session],
) -> None:
    prepare_approved_request(session_factory)
    client = FakeEnterpriseClient(grant_error=TimeoutError("connection lost"))

    result = execute(build_execution_service(session_factory, client))

    assert result.workflow_state is WorkflowState.WAITING_HUMAN
    assert result.gateway_result is not None
    assert result.gateway_result.outcome is ActionGatewayOutcome.RESULT_UNKNOWN


def test_unconfirmed_write_moves_workflow_to_human_review(
    session_factory: sessionmaker[Session],
) -> None:
    prepare_approved_request(session_factory)
    client = FakeEnterpriseClient(verification_confirmed=False)

    result = execute(build_execution_service(session_factory, client))

    assert result.workflow_state is WorkflowState.WAITING_HUMAN
    assert result.gateway_result is not None
    assert result.gateway_result.outcome is ActionGatewayOutcome.VERIFICATION_FAILED

