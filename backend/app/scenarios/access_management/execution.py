"""Access-specific ports and workflow service around the domain-neutral Action Gateway."""

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
    EnterpriseOpsApplication,
    EnterpriseOpsEmployee,
    EnterpriseOpsUserAccess,
    EnterpriseOpsVerification,
    EnterpriseOpsWriteResult,
    EnterpriseOpsClientError,
)
from app.workflow.models import WorkflowRun
from app.workflow.repository import WorkflowRepository
from app.workflow.state import WorkflowState


class EnterpriseOpsClientPort(Protocol):
    def query_employee(self, employee_id: str) -> EnterpriseOpsEmployee: ...

    def query_application(
        self, application_code: str
    ) -> EnterpriseOpsApplication: ...

    def query_user_access(
        self, employee_id: str
    ) -> tuple[EnterpriseOpsUserAccess, ...]: ...

    def grant_application_access(
        self,
        payload: dict[str, JsonValue],
        *,
        idempotency_key: str,
    ) -> EnterpriseOpsWriteResult: ...

    def verify_application_access(
        self, payload: dict[str, JsonValue]
    ) -> EnterpriseOpsVerification: ...


class ActionGatewayPort(Protocol):
    def execute(
        self,
        proposal: ActionProposal,
        *,
        actor_id: str,
        approval_id: str,
        idempotency_key: str,
    ) -> ActionGatewayResult: ...


class AccessActionDeniedError(PermissionError):
    """Raised when the executor identity or proposal violates access rules."""


class AccessBusinessStateError(RuntimeError):
    """Raised when authoritative enterprise state no longer permits execution."""


class AccessAlreadySatisfiedError(AccessBusinessStateError):
    """Raised when permission became active while the request was waiting."""


class AccessActionAuthorization:
    """Allow only configured deterministic service identities to execute writes."""

    def __init__(self, allowed_actor_ids: frozenset[str]) -> None:
        self._allowed_actor_ids = allowed_actor_ids

    def require_authorized(self, actor_id: str, proposal: ActionProposal) -> None:
        if actor_id not in self._allowed_actor_ids:
            raise AccessActionDeniedError("actor is not an action executor")


class AccessExecutionPolicyGuard:
    """Revalidate immutable permission limits independently from the Agent."""

    ACTION_TYPE = "grant_application_access"

    def require_allowed(self, proposal: ActionProposal) -> None:
        if proposal.action_type != self.ACTION_TYPE:
            raise AccessActionDeniedError("unsupported access action type")
        parameters = proposal.parameters
        employee_id = parameters.get("employee_id")
        application_code = parameters.get("application_code")
        role_code = parameters.get("role_code")
        duration_days = parameters.get("duration_days")
        if not all(
            isinstance(value, str) and value.strip()
            for value in (employee_id, application_code, role_code)
        ):
            raise AccessActionDeniedError("access action identity fields are invalid")
        if not isinstance(duration_days, int) or isinstance(duration_days, bool):
            raise AccessActionDeniedError("access duration must be an integer")
        if duration_days < 1 or duration_days > 90:
            raise AccessActionDeniedError("access duration violates policy")
        expected_target = (
            f"employee/{employee_id}/application/{application_code}"
        )
        if proposal.target_resource != expected_target:
            raise AccessActionDeniedError("target resource does not match parameters")


class AccessLatestStateChecker:
    """Re-read employee, application and current permission before writing."""

    def __init__(self, client: EnterpriseOpsClientPort) -> None:
        self._client = client

    def require_current_and_executable(self, proposal: ActionProposal) -> None:
        parameters = proposal.parameters
        employee_id = str(parameters["employee_id"])
        application_code = str(parameters["application_code"])
        role_code = str(parameters["role_code"])
        employee = self._client.query_employee(employee_id)
        application = self._client.query_application(application_code)
        if not employee.active:
            raise AccessBusinessStateError("employee is no longer active")
        if not application.active:
            raise AccessBusinessStateError("application is no longer active")
        if role_code not in application.allowed_role_codes:
            raise AccessBusinessStateError("role is no longer allowed")
        existing = self._client.query_user_access(employee_id)
        if any(
            access.active
            and access.application_code == application_code
            and access.role_code == role_code
            for access in existing
        ):
            raise AccessAlreadySatisfiedError("requested access is already active")


class AccessGrantWriteTool:
    """The only access adapter that invokes the enterprise grant write operation."""

    name = "enterprise_ops.grant_application_access"

    def __init__(self, client: EnterpriseOpsClientPort) -> None:
        self._client = client

    def execute(
        self,
        proposal: ActionProposal,
        *,
        idempotency_key: str,
    ) -> ToolExecutionResult:
        result = self._client.grant_application_access(
            self._tool_payload(proposal),
            idempotency_key=idempotency_key,
        )
        return ToolExecutionResult(
            status=ToolExecutionStatus.SUCCEEDED,
            external_reference=result.resource_id,
            details={"enterprise_status": result.status.value},
        )

    @staticmethod
    def _tool_payload(proposal: ActionProposal) -> dict[str, JsonValue]:
        parameters = proposal.parameters
        return {
            "employee_id": parameters["employee_id"],
            "application_code": parameters["application_code"],
            "role_code": parameters["role_code"],
            "duration_days": parameters["duration_days"],
        }


class AccessGrantVerifier:
    """Verify a grant through an independent read operation."""

    def __init__(self, client: EnterpriseOpsClientPort) -> None:
        self._client = client

    def verify(
        self,
        proposal: ActionProposal,
        tool_result: ToolExecutionResult,
    ) -> VerificationResult:
        parameters = proposal.parameters
        result = self._client.verify_application_access(
            {
                "employee_id": parameters["employee_id"],
                "application_code": parameters["application_code"],
                "role_code": parameters["role_code"],
            }
        )
        details: dict[str, JsonValue] = {"confirmed": result.confirmed}
        if result.access is not None:
            details["access_id"] = result.access.access_id
        return VerificationResult(confirmed=result.confirmed, details=details)


class AccessExecutionResult(BaseModel):
    model_config = ConfigDict(frozen=True)

    workflow_run_id: str
    workflow_state: WorkflowState
    workflow_version: int
    gateway_result: ActionGatewayResult | None = None
    already_satisfied: bool = False
    human_review_reason: str | None = None


class AccessRequestExecutionService:
    """Move an approved workflow through EXECUTING to its deterministic result."""

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
    ) -> AccessExecutionResult:
        # The EXECUTING state is committed before any external side effect.
        with self._session_factory.begin() as session:
            proposal = ActionProposalRepository(session).get(action_id, action_version)
            workflow_run = WorkflowRepository(session).transition(
                workflow_run_id,
                expected_version=expected_workflow_version,
                target=WorkflowState.EXECUTING,
                event_type="ACTION_EXECUTION_STARTED",
                payload={"action_id": action_id, "action_version": action_version},
            )
            executing_version = workflow_run.version
        try:
            gateway_result = self._gateway.execute(
                proposal,
                actor_id=actor_id,
                approval_id=approval_id,
                idempotency_key=idempotency_key,
            )
        except AccessAlreadySatisfiedError:
            workflow_run = self._finish(
                workflow_run_id,
                executing_version,
                WorkflowState.COMPLETED,
                "ACTION_ALREADY_SATISFIED",
                {"action_id": action_id, "action_version": action_version},
            )
            return AccessExecutionResult(
                workflow_run_id=workflow_run.id,
                workflow_state=workflow_run.workflow_state,
                workflow_version=workflow_run.version,
                already_satisfied=True,
            )
        except (
            AccessActionDeniedError,
            AccessBusinessStateError,
            ActionProposalVersionError,
            ApprovalValidationError,
            EnterpriseOpsClientError,
        ) as exc:
            reason = type(exc).__name__
            workflow_run = self._finish(
                workflow_run_id,
                executing_version,
                WorkflowState.WAITING_HUMAN,
                "ACTION_PRECONDITION_REQUIRES_HUMAN",
                {"action_id": action_id, "error_type": reason},
            )
            return AccessExecutionResult(
                workflow_run_id=workflow_run.id,
                workflow_state=workflow_run.workflow_state,
                workflow_version=workflow_run.version,
                human_review_reason=reason,
            )

        target = self._TARGET_BY_OUTCOME[gateway_result.outcome]
        workflow_run = self._finish(
            workflow_run_id,
            executing_version,
            target,
            f"ACTION_{gateway_result.outcome.value}",
            {"gateway_result": gateway_result.model_dump(mode="json")},
        )
        return AccessExecutionResult(
            workflow_run_id=workflow_run.id,
            workflow_state=workflow_run.workflow_state,
            workflow_version=workflow_run.version,
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
            workflow_run = WorkflowRepository(session).transition(
                workflow_run_id,
                expected_version=expected_version,
                target=target,
                event_type=event_type,
                payload=payload,
            )
            return workflow_run
