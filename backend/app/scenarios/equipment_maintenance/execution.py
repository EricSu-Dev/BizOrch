"""Deterministic Action Gateway adapters for equipment maintenance writes."""

from datetime import datetime
from typing import Protocol

from pydantic import BaseModel, ConfigDict, JsonValue
from sqlalchemy.orm import Session, sessionmaker

from app.actions.contracts import (
    ActionGatewayOutcome,
    ActionGatewayResult,
    ActionProposal,
    ToolExecutionResult,
    ToolExecutionStatus,
    VerificationResult,
)
from app.actions.repository import ActionProposalRepository, ActionProposalVersionError
from app.approval.repository import ApprovalValidationError
from app.integrations.enterprise_ops import (
    EnterpriseOpsClientError,
    EnterpriseOpsEquipmentStatus,
    EnterpriseOpsEquipmentStatusView,
    EnterpriseOpsMaintenanceWorkOrder,
    EnterpriseOpsMaintenanceWriteResult,
)
from app.workflow.models import WorkflowRun
from app.workflow.repository import WorkflowRepository
from app.workflow.state import WorkflowState


class MaintenanceEnterpriseOpsPort(Protocol):
    def query_equipment_status(
        self,
        equipment_code: str,
    ) -> EnterpriseOpsEquipmentStatusView: ...

    def query_maintenance_work_order(
        self,
        *,
        work_order_id: str | None = None,
        idempotency_key: str | None = None,
    ) -> EnterpriseOpsMaintenanceWorkOrder: ...

    def create_maintenance_work_order(
        self,
        payload: dict[str, JsonValue],
        *,
        idempotency_key: str,
    ) -> EnterpriseOpsMaintenanceWriteResult: ...


class ActionGatewayPort(Protocol):
    def execute(
        self,
        proposal: ActionProposal,
        *,
        actor_id: str,
        approval_id: str,
        idempotency_key: str,
    ) -> ActionGatewayResult: ...


class MaintenanceActionDeniedError(PermissionError):
    """Raised when executor identity or proposal structure is not allowed."""


class MaintenanceBusinessStateError(RuntimeError):
    """Raised when authoritative equipment facts changed before execution."""


class MaintenanceActionAuthorization:
    def __init__(self, allowed_actor_ids: frozenset[str]) -> None:
        self._allowed_actor_ids = allowed_actor_ids

    def require_authorized(self, actor_id: str, proposal: ActionProposal) -> None:
        if actor_id not in self._allowed_actor_ids:
            raise MaintenanceActionDeniedError("actor is not an action executor")


class MaintenanceExecutionPolicyGuard:
    ACTION_TYPE = "create_maintenance_work_order"
    REQUIRED_TEXT = (
        "requester_id",
        "equipment_code",
        "fault_description",
        "observed_at",
        "production_impact",
        "safety_observation",
        "business_reason",
        "priority",
    )

    def require_allowed(self, proposal: ActionProposal) -> None:
        if proposal.action_type != self.ACTION_TYPE:
            raise MaintenanceActionDeniedError("unsupported maintenance action type")
        parameters = proposal.parameters
        if any(
            not isinstance(parameters.get(key), str)
            or not str(parameters[key]).strip()
            for key in self.REQUIRED_TEXT
        ):
            raise MaintenanceActionDeniedError("maintenance text fields are invalid")
        expected_version = parameters.get("expected_equipment_version")
        if (
            not isinstance(expected_version, int)
            or isinstance(expected_version, bool)
            or expected_version < 1
        ):
            raise MaintenanceActionDeniedError("equipment version is invalid")
        if parameters["production_impact"] not in {"NONE", "SLOWDOWN", "STOPPED"}:
            raise MaintenanceActionDeniedError("production impact is invalid")
        if parameters["priority"] not in {"LOW", "MEDIUM", "HIGH"}:
            raise MaintenanceActionDeniedError("maintenance priority is invalid")
        equipment_code = str(parameters["equipment_code"])
        expected_target = f"equipment/{equipment_code}/maintenance-work-orders"
        if proposal.target_resource != expected_target:
            raise MaintenanceActionDeniedError(
                "target resource does not match equipment parameters"
            )
        try:
            datetime.fromisoformat(str(parameters["observed_at"]).replace("Z", "+00:00"))
        except ValueError as exc:
            raise MaintenanceActionDeniedError("observed_at is invalid") from exc


class MaintenanceLatestStateChecker:
    """Re-read equipment status and optimistic version immediately before write."""

    def __init__(self, client: MaintenanceEnterpriseOpsPort) -> None:
        self._client = client

    def require_current_and_executable(self, proposal: ActionProposal) -> None:
        parameters = proposal.parameters
        equipment_code = str(parameters["equipment_code"])
        status = self._client.query_equipment_status(equipment_code)
        if status.equipment_code != equipment_code:
            raise MaintenanceBusinessStateError("equipment identity changed")
        if status.version != parameters["expected_equipment_version"]:
            raise MaintenanceBusinessStateError("equipment version changed after approval")
        if status.status not in {
            EnterpriseOpsEquipmentStatus.RUNNING,
            EnterpriseOpsEquipmentStatus.DEGRADED,
        }:
            raise MaintenanceBusinessStateError(
                "equipment state no longer allows maintenance creation"
            )


class MaintenanceWorkOrderWriteTool:
    name = "enterprise_ops.create_maintenance_work_order"

    def __init__(self, client: MaintenanceEnterpriseOpsPort) -> None:
        self._client = client

    def execute(
        self,
        proposal: ActionProposal,
        *,
        idempotency_key: str,
    ) -> ToolExecutionResult:
        result = self._client.create_maintenance_work_order(
            dict(proposal.parameters),
            idempotency_key=idempotency_key,
        )
        return ToolExecutionResult(
            status=ToolExecutionStatus.SUCCEEDED,
            external_reference=result.work_order_id,
            details={
                "equipment_code": result.equipment_code,
                "equipment_status": result.equipment_status.value,
                "equipment_version": result.equipment_version,
                "work_order_status": result.work_order_status.value,
                "idempotency_key": idempotency_key,
                "enterprise_replayed": result.replayed,
            },
        )


class MaintenanceWorkOrderVerifier:
    """Independently read order and equipment status after the write."""

    def __init__(self, client: MaintenanceEnterpriseOpsPort) -> None:
        self._client = client

    def verify(
        self,
        proposal: ActionProposal,
        tool_result: ToolExecutionResult,
    ) -> VerificationResult:
        if not tool_result.external_reference:
            return VerificationResult(
                confirmed=False,
                details={"reason": "missing_work_order_reference"},
            )
        order = self._client.query_maintenance_work_order(
            work_order_id=tool_result.external_reference
        )
        status = self._client.query_equipment_status(
            str(proposal.parameters["equipment_code"])
        )
        parameters = proposal.parameters
        expected_observed = datetime.fromisoformat(
            str(parameters["observed_at"]).replace("Z", "+00:00")
        )
        confirmed = all(
            (
                order.equipment_code == parameters["equipment_code"],
                order.requester_id == parameters["requester_id"],
                order.fault_description == parameters["fault_description"],
                order.observed_at == expected_observed,
                order.production_impact == parameters["production_impact"],
                order.safety_observation == parameters["safety_observation"],
                order.business_reason == parameters["business_reason"],
                order.priority == parameters["priority"],
                order.idempotency_key == tool_result.details.get("idempotency_key"),
                status.status is EnterpriseOpsEquipmentStatus.MAINTENANCE_PENDING,
                status.version == int(parameters["expected_equipment_version"]) + 1,
            )
        )
        return VerificationResult(
            confirmed=confirmed,
            details={
                "work_order_id": order.work_order_id,
                "work_order_status": order.status.value,
                "equipment_status": status.status.value,
                "equipment_version": status.version,
            },
        )


class MaintenanceExecutionResult(BaseModel):
    model_config = ConfigDict(frozen=True)

    workflow_run_id: str
    workflow_state: WorkflowState
    workflow_version: int
    gateway_result: ActionGatewayResult | None = None
    human_review_reason: str | None = None


class MaintenanceRequestExecutionService:
    """Move one approved maintenance flow through the controlled write boundary."""

    _TARGET_BY_OUTCOME = {
        ActionGatewayOutcome.SUCCEEDED: WorkflowState.COMPLETED,
        ActionGatewayOutcome.REPLAYED: WorkflowState.COMPLETED,
        ActionGatewayOutcome.TOOL_FAILED: WorkflowState.FAILED,
        ActionGatewayOutcome.RESULT_UNKNOWN: WorkflowState.WAITING_HUMAN,
        ActionGatewayOutcome.VERIFICATION_FAILED: WorkflowState.WAITING_HUMAN,
    }

    def __init__(
        self,
        session_factory: sessionmaker[Session],
        gateway: ActionGatewayPort,
    ) -> None:
        self._session_factory = session_factory
        self._gateway = gateway

    def execute(
        self,
        *,
        workflow_run_id: str,
        expected_workflow_version: int,
        action_id: str,
        action_version: int,
        approval_id: str,
        actor_id: str,
        idempotency_key: str,
    ) -> MaintenanceExecutionResult:
        with self._session_factory.begin() as session:
            proposal = ActionProposalRepository(session).get(action_id, action_version)
            workflow = WorkflowRepository(session).transition(
                workflow_run_id,
                expected_version=expected_workflow_version,
                target=WorkflowState.EXECUTING,
                event_type="MAINTENANCE_ACTION_EXECUTION_STARTED",
                payload={"action_id": action_id, "action_version": action_version},
            )
            executing_version = workflow.version
        try:
            gateway_result = self._gateway.execute(
                proposal,
                actor_id=actor_id,
                approval_id=approval_id,
                idempotency_key=idempotency_key,
            )
        except (
            MaintenanceActionDeniedError,
            MaintenanceBusinessStateError,
            ActionProposalVersionError,
            ApprovalValidationError,
            EnterpriseOpsClientError,
        ) as exc:
            workflow = self._finish(
                workflow_run_id,
                executing_version,
                WorkflowState.WAITING_HUMAN,
                "MAINTENANCE_ACTION_PRECONDITION_REQUIRES_HUMAN",
                {"action_id": action_id, "error_type": type(exc).__name__},
            )
            return MaintenanceExecutionResult(
                workflow_run_id=workflow.id,
                workflow_state=workflow.workflow_state,
                workflow_version=workflow.version,
                human_review_reason=type(exc).__name__,
            )

        target = self._TARGET_BY_OUTCOME[gateway_result.outcome]
        workflow = self._finish(
            workflow_run_id,
            executing_version,
            target,
            f"MAINTENANCE_ACTION_{gateway_result.outcome.value}",
            {"gateway_result": gateway_result.model_dump(mode="json")},
        )
        return MaintenanceExecutionResult(
            workflow_run_id=workflow.id,
            workflow_state=workflow.workflow_state,
            workflow_version=workflow.version,
            gateway_result=gateway_result,
        )

    def _finish(
        self,
        workflow_run_id: str,
        expected_version: int,
        target: WorkflowState,
        event_type: str,
        payload: dict[str, object],
    ) -> WorkflowRun:
        with self._session_factory.begin() as session:
            return WorkflowRepository(session).transition(
                workflow_run_id,
                expected_version=expected_version,
                target=target,
                event_type=event_type,
                payload=payload,
            )
