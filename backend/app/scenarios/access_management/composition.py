"""Production composition for MCP-backed access queries and controlled writes."""

from pathlib import Path

from sqlalchemy.orm import Session, sessionmaker

from app.actions.execution import ActionExecutionRepository
from app.actions.gateway import ActionGateway
from app.actions.idempotency import PersistentIdempotencyStore
from app.actions.validation import (
    PersistentApprovalValidator,
    PersistentProposalVersionValidator,
)
from app.integrations.mcp_client import (
    EnterpriseOpsMcpClient,
    ReadOnlyEnterpriseOpsMcpClient,
    StreamableHttpMcpToolCaller,
)
from app.scenarios.access_management.execution import (
    AccessActionAuthorization,
    AccessExecutionPolicyGuard,
    AccessGrantVerifier,
    AccessGrantWriteTool,
    AccessLatestStateChecker,
    AccessRequestExecutionService,
)
from app.scenarios.access_management.workflow import AccessRequestWorkflow
from app.workflow.checkpoint import SqliteCheckpointStore


def build_mcp_enterprise_clients(
    mcp_endpoint: str,
) -> tuple[EnterpriseOpsMcpClient, ReadOnlyEnterpriseOpsMcpClient]:
    """Return separate full and read-only capabilities over one MCP transport."""
    full_client = EnterpriseOpsMcpClient(
        StreamableHttpMcpToolCaller(mcp_endpoint, timeout_seconds=10)
    )
    return full_client, ReadOnlyEnterpriseOpsMcpClient(full_client)


def build_access_execution_service(
    session_factory: sessionmaker[Session],
    *,
    mcp_endpoint: str,
    action_executor_id: str = "system-action-executor",
) -> AccessRequestExecutionService:
    """Wire Action Gateway writes exclusively through enterprise-ops-mcp."""
    full_client, _ = build_mcp_enterprise_clients(mcp_endpoint)
    gateway = ActionGateway(
        authorization=AccessActionAuthorization(frozenset({action_executor_id})),
        policy=AccessExecutionPolicyGuard(),
        approvals=PersistentApprovalValidator(session_factory),
        proposals=PersistentProposalVersionValidator(session_factory),
        latest_state=AccessLatestStateChecker(full_client),
        idempotency=PersistentIdempotencyStore(session_factory),
        tool=AccessGrantWriteTool(full_client),
        verifier=AccessGrantVerifier(full_client),
        executions=ActionExecutionRepository(session_factory),
    )
    return AccessRequestExecutionService(session_factory, gateway)


def build_access_workflow(
    session_factory: sessionmaker[Session],
    *,
    mcp_endpoint: str,
    checkpoint_path: str | Path,
    action_executor_id: str = "system-action-executor",
) -> tuple[AccessRequestWorkflow, SqliteCheckpointStore]:
    """Compose the recoverable graph and return its owned checkpoint resource."""
    checkpoint_store = SqliteCheckpointStore(checkpoint_path)
    execution_service = build_access_execution_service(
        session_factory,
        mcp_endpoint=mcp_endpoint,
        action_executor_id=action_executor_id,
    )
    workflow = AccessRequestWorkflow(
        session_factory,
        execution_service,
        checkpoint_store.saver,
        action_executor_id=action_executor_id,
    )
    return workflow, checkpoint_store
