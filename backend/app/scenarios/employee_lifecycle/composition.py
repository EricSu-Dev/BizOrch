"""Production composition for controlled employee lifecycle writes."""

from sqlalchemy.orm import Session, sessionmaker

from app.actions.execution import ActionExecutionRepository
from app.actions.gateway import ActionGateway
from app.actions.idempotency import PersistentIdempotencyStore
from app.actions.validation import PersistentProposalVersionValidator
from app.integrations.mcp_client import EnterpriseOpsMcpClient
from app.scenarios.employee_lifecycle.execution import (
    EmployeeLifecycleActionAuthorization,
    EmployeeLifecyclePlanApprovalValidator,
    EmployeeLifecycleExecutionPolicy,
    EmployeeLifecycleLatestStateChecker,
    EmployeeLifecyclePlanExecutionService,
    EmployeeLifecycleVerifier,
    EmployeeLifecycleWriteTool,
)


def build_employee_lifecycle_execution_service(
    session_factory: sessionmaker[Session],
    client: EnterpriseOpsMcpClient,
    *,
    action_executor_id: str = "system-action-executor",
) -> EmployeeLifecyclePlanExecutionService:
    gateway = ActionGateway(
        authorization=EmployeeLifecycleActionAuthorization(
            frozenset({action_executor_id})
        ),
        policy=EmployeeLifecycleExecutionPolicy(),
        approvals=EmployeeLifecyclePlanApprovalValidator(session_factory),
        proposals=PersistentProposalVersionValidator(session_factory),
        latest_state=EmployeeLifecycleLatestStateChecker(client),
        idempotency=PersistentIdempotencyStore(session_factory),
        tool=EmployeeLifecycleWriteTool(client),
        verifier=EmployeeLifecycleVerifier(client),
        executions=ActionExecutionRepository(session_factory),
    )
    return EmployeeLifecyclePlanExecutionService(session_factory, gateway)
