"""Production composition for controlled maintenance writes through MCP."""

from sqlalchemy.orm import Session, sessionmaker

from app.actions.execution import ActionExecutionRepository
from app.actions.gateway import ActionGateway
from app.actions.idempotency import PersistentIdempotencyStore
from app.actions.validation import (
    PersistentApprovalValidator,
    PersistentProposalVersionValidator,
)
from app.integrations.mcp_client import EnterpriseOpsMcpClient
from app.scenarios.equipment_maintenance.execution import (
    MaintenanceActionAuthorization,
    MaintenanceExecutionPolicyGuard,
    MaintenanceLatestStateChecker,
    MaintenanceRequestExecutionService,
    MaintenanceWorkOrderVerifier,
    MaintenanceWorkOrderWriteTool,
)


def build_maintenance_execution_service(
    session_factory: sessionmaker[Session],
    client: EnterpriseOpsMcpClient,
    *,
    action_executor_id: str = "system-action-executor",
) -> MaintenanceRequestExecutionService:
    gateway = ActionGateway(
        authorization=MaintenanceActionAuthorization(
            frozenset({action_executor_id})
        ),
        policy=MaintenanceExecutionPolicyGuard(),
        approvals=PersistentApprovalValidator(session_factory),
        proposals=PersistentProposalVersionValidator(session_factory),
        latest_state=MaintenanceLatestStateChecker(client),
        idempotency=PersistentIdempotencyStore(session_factory),
        tool=MaintenanceWorkOrderWriteTool(client),
        verifier=MaintenanceWorkOrderVerifier(client),
        executions=ActionExecutionRepository(session_factory),
    )
    return MaintenanceRequestExecutionService(session_factory, gateway)
