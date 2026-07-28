"""Production composition for atomic procurement execution."""

from sqlalchemy.orm import Session, sessionmaker

from app.actions.execution import ActionExecutionRepository
from app.actions.gateway import ActionGateway
from app.actions.idempotency import PersistentIdempotencyStore
from app.actions.validation import PersistentProposalVersionValidator
from app.integrations.mcp_client import EnterpriseOpsMcpClient
from app.scenarios.procurement.execution import (
    ProcurementActionAuthorization,
    ProcurementExecutionPolicy,
    ProcurementLatestStateChecker,
    ProcurementPlanExecutionService,
    ProcurementSequenceApprovalValidator,
    ProcurementVerifier,
    ProcurementWriteTool,
)


def build_procurement_execution_service(
    session_factory: sessionmaker[Session],
    client: EnterpriseOpsMcpClient,
    *,
    action_executor_id: str = "system-action-executor",
) -> ProcurementPlanExecutionService:
    gateway = ActionGateway(
        authorization=ProcurementActionAuthorization(
            frozenset({action_executor_id})
        ),
        policy=ProcurementExecutionPolicy(),
        approvals=ProcurementSequenceApprovalValidator(session_factory),
        proposals=PersistentProposalVersionValidator(session_factory),
        latest_state=ProcurementLatestStateChecker(client),
        idempotency=PersistentIdempotencyStore(session_factory),
        tool=ProcurementWriteTool(client),
        verifier=ProcurementVerifier(client),
        executions=ActionExecutionRepository(session_factory),
    )
    return ProcurementPlanExecutionService(session_factory, gateway)
