"""Controlled V5 procurement execution through Action Gateway."""

from __future__ import annotations

from decimal import Decimal
from typing import Protocol

from pydantic import BaseModel, ConfigDict, JsonValue
from sqlalchemy.orm import Session, sessionmaker

from app.actions.activity import ExecutionActivityRegistry
from app.actions.contracts import (
    ActionGatewayOutcome,
    ActionGatewayResult,
    ActionPlanStepStatus,
    ActionProposal,
    ToolExecutionResult,
    ToolExecutionStatus,
    VerificationResult,
)
from app.actions.plans import ActionPlanRepository, ActionPlanVersionError
from app.approval.sequences import ApprovalSequenceRepository
from app.integrations.enterprise_ops import (
    EnterpriseOpsBudgetReservation,
    EnterpriseOpsCostCenter,
    EnterpriseOpsEmployeeLifecycleProfile,
    EnterpriseOpsProcurementPolicy,
    EnterpriseOpsProcurementRequest,
    EnterpriseOpsProcurementWriteResult,
    EnterpriseOpsRejectedError,
    EnterpriseOpsUnavailableError,
)
from app.workflow.repository import WorkflowRepository
from app.workflow.state import WorkflowState


_ACTION_TYPE = "CREATE_PROCUREMENT_REQUEST_AND_RESERVE_BUDGET"


class ProcurementWritePort(Protocol):
    def query_employee_lifecycle_profile(
        self, employee_id: str
    ) -> EnterpriseOpsEmployeeLifecycleProfile | None: ...

    def query_cost_center(
        self, cost_center_code: str
    ) -> EnterpriseOpsCostCenter | None: ...

    def query_current_procurement_policy(
        self,
    ) -> EnterpriseOpsProcurementPolicy | None: ...

    def query_open_procurement_requests(
        self, *, requester_id: str, cost_center_code: str
    ) -> tuple[EnterpriseOpsProcurementRequest, ...]: ...

    def query_procurement_request(
        self,
        *,
        request_id: str | None = None,
        workflow_run_id: str | None = None,
    ) -> EnterpriseOpsProcurementRequest | None: ...

    def query_budget_reservation(
        self, request_id: str
    ) -> EnterpriseOpsBudgetReservation | None: ...

    def create_procurement_request_and_reserve_budget(
        self, payload: dict[str, JsonValue], *, idempotency_key: str
    ) -> EnterpriseOpsProcurementWriteResult: ...


class ActionGatewayPort(Protocol):
    def execute(
        self,
        proposal: ActionProposal,
        *,
        actor_id: str,
        approval_id: str,
        idempotency_key: str,
    ) -> ActionGatewayResult: ...


class ProcurementExecutionError(RuntimeError):
    """Known deterministic condition requiring human review."""


class ProcurementActionAuthorization:
    def __init__(self, allowed_actor_ids: frozenset[str]) -> None:
        self._allowed_actor_ids = allowed_actor_ids

    def require_authorized(self, actor_id: str, proposal: ActionProposal) -> None:
        if actor_id not in self._allowed_actor_ids:
            raise PermissionError("actor cannot execute procurement actions")


class ProcurementExecutionPolicy:
    def require_allowed(self, proposal: ActionProposal) -> None:
        parameters = proposal.parameters
        cost_center = str(parameters.get("cost_center_code", ""))
        if (
            proposal.action_type != _ACTION_TYPE
            or not cost_center
            or proposal.target_resource != f"cost-center/{cost_center}"
            or str(parameters.get("currency", "")) != "CNY"
        ):
            raise PermissionError("proposal is not an allowed procurement write")


class ProcurementSequenceApprovalValidator:
    """Require every stage and exact proposal membership in the current plan."""

    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self._session_factory = session_factory

    def require_approved(self, approval_id: str, proposal: ActionProposal) -> object:
        with self._session_factory() as session:
            sequences = ApprovalSequenceRepository(session)
            sequence = sequences.get_by_approval(approval_id)
            sequence, plan, route = sequences.require_integrity(
                sequence.sequence_id
            )
            sequences.require_approved(sequence.sequence_id, plan, route)
            if not plan.contains_proposal(proposal):
                raise ProcurementExecutionError(
                    "approved sequence does not contain this proposal"
                )
            if approval_id not in {stage.id for stage in sequence.stages}:
                raise ProcurementExecutionError(
                    "approval does not belong to the approved sequence"
                )
            return sequence


class ProcurementLatestStateChecker:
    """Re-read employee, policy, budget and duplicates immediately before write."""

    def __init__(self, client: ProcurementWritePort) -> None:
        self._client = client

    def require_current_and_executable(self, proposal: ActionProposal) -> None:
        p = proposal.parameters
        requester_id = str(p["requester_id"])
        cost_center_code = str(p["cost_center_code"])
        profile = self._client.query_employee_lifecycle_profile(requester_id)
        cost_center = self._client.query_cost_center(cost_center_code)
        policy = self._client.query_current_procurement_policy()
        if (
            profile is None
            or not profile.employee.active
            or profile.employee.employment_status != "ACTIVE"
            or profile.account is None
            or profile.account.status != "ACTIVE"
            or profile.employee.manager_id
            != p["expected_business_approver_id"]
        ):
            raise ProcurementExecutionError("requester is no longer active")
        if (
            cost_center is None
            or not cost_center.active
            or cost_center.department_code != profile.employee.department_code
            or cost_center.currency != p["currency"]
            or cost_center.version != int(p["expected_cost_center_version"])
            or cost_center.reserved_amount
            != Decimal(str(p["expected_reserved_amount"]))
            or cost_center.budget_owner_id
            != p["expected_budget_owner_id"]
            or cost_center.available_amount
            < Decimal(str(p["estimated_total_amount"]))
        ):
            raise ProcurementExecutionError(
                "approved budget facts changed or budget is insufficient"
            )
        if (
            policy is None
            or not policy.active
            or policy.policy_code != p["policy_code"]
            or policy.version != int(p["policy_version"])
            or policy.currency != p["currency"]
            or policy.procurement_approver_id
            != p["expected_procurement_approver_id"]
        ):
            raise ProcurementExecutionError(
                "approved procurement policy is no longer current"
            )
        requested_names = {
            str(item["item_name"]).casefold() for item in p["items"]
        }
        if any(
            requested_names
            & {item.item_name.casefold() for item in request.items}
            for request in self._client.query_open_procurement_requests(
                requester_id=requester_id,
                cost_center_code=cost_center_code,
            )
        ):
            raise ProcurementExecutionError(
                "a similar open procurement request appeared after approval"
            )


class ProcurementWriteTool:
    """Invoke the one atomic enterprise capability and reconcile timeouts by read."""

    name = "enterprise_ops.create_procurement_request_and_reserve_budget"

    def __init__(self, client: ProcurementWritePort) -> None:
        self._client = client

    def execute(
        self, proposal: ActionProposal, *, idempotency_key: str
    ) -> ToolExecutionResult:
        payload = dict(proposal.parameters)
        try:
            result = self._client.create_procurement_request_and_reserve_budget(
                payload, idempotency_key=idempotency_key
            )
        except EnterpriseOpsRejectedError as exc:
            return ToolExecutionResult(
                status=ToolExecutionStatus.FAILED,
                details={"error_type": type(exc).__name__},
            )
        except EnterpriseOpsUnavailableError:
            return self._reconcile_unknown(proposal)
        return self._result(result)

    def _reconcile_unknown(
        self, proposal: ActionProposal
    ) -> ToolExecutionResult:
        try:
            request = self._client.query_procurement_request(
                workflow_run_id=str(proposal.parameters["workflow_run_id"])
            )
            reservation = (
                self._client.query_budget_reservation(request.request_id)
                if request is not None
                else None
            )
        except Exception:
            return ToolExecutionResult(
                status=ToolExecutionStatus.UNKNOWN,
                details={"reason": "write_and_reconciliation_unavailable"},
            )
        if request is None and reservation is None:
            return ToolExecutionResult(
                status=ToolExecutionStatus.UNKNOWN,
                details={"reason": "write_outcome_not_proven"},
            )
        if request is None or reservation is None:
            return ToolExecutionResult(
                status=ToolExecutionStatus.UNKNOWN,
                details={"reason": "partial_enterprise_state"},
            )
        if not ProcurementVerifier.matches(
            proposal, request, reservation
        ):
            return ToolExecutionResult(
                status=ToolExecutionStatus.UNKNOWN,
                details={"reason": "reconciled_state_mismatch"},
            )
        return ToolExecutionResult(
            status=ToolExecutionStatus.SUCCEEDED,
            external_reference=request.request_id,
            details={
                "reservation_id": reservation.reservation_id,
                "reconciled_after_unknown": True,
            },
        )

    @staticmethod
    def _result(result: EnterpriseOpsProcurementWriteResult) -> ToolExecutionResult:
        return ToolExecutionResult(
            status=ToolExecutionStatus.SUCCEEDED,
            external_reference=result.request_id,
            details={
                "reservation_id": result.reservation_id,
                "cost_center_version": result.cost_center_version,
                "enterprise_replayed": result.replayed,
            },
        )


class ProcurementVerifier:
    """Verify request, all lines, reservation and resulting budget snapshot."""

    def __init__(self, client: ProcurementWritePort) -> None:
        self._client = client

    def verify(
        self, proposal: ActionProposal, tool_result: ToolExecutionResult
    ) -> VerificationResult:
        request = self._client.query_procurement_request(
            workflow_run_id=str(proposal.parameters["workflow_run_id"])
        )
        reservation = (
            self._client.query_budget_reservation(request.request_id)
            if request is not None
            else None
        )
        cost_center = self._client.query_cost_center(
            str(proposal.parameters["cost_center_code"])
        )
        confirmed = (
            request is not None
            and reservation is not None
            and self.matches(proposal, request, reservation)
            and cost_center is not None
            and cost_center.version
            == int(proposal.parameters["expected_cost_center_version"]) + 1
            and cost_center.reserved_amount
            == Decimal(str(proposal.parameters["expected_reserved_amount"]))
            + Decimal(str(proposal.parameters["estimated_total_amount"]))
        )
        return VerificationResult(
            confirmed=confirmed,
            details={
                "request_id": request.request_id if request else None,
                "reservation_id": (
                    reservation.reservation_id if reservation else None
                ),
                "request_status": request.status if request else None,
                "reservation_status": (
                    reservation.status if reservation else None
                ),
                "cost_center_version": (
                    cost_center.version if cost_center else None
                ),
            },
        )

    @staticmethod
    def matches(
        proposal: ActionProposal,
        request: EnterpriseOpsProcurementRequest,
        reservation: EnterpriseOpsBudgetReservation,
    ) -> bool:
        p = proposal.parameters
        expected_items = tuple(
            (
                int(item["line_no"]),
                str(item["item_name"]),
                str(item["item_category"]),
                int(item["quantity"]),
                item.get("specification_note"),
            )
            for item in p["items"]
        )
        actual_items = tuple(
            (
                item.line_no,
                item.item_name,
                item.item_category,
                item.quantity,
                item.specification_note,
            )
            for item in request.items
        )
        amount = Decimal(str(p["estimated_total_amount"]))
        return bool(
            request.external_workflow_run_id == p["workflow_run_id"]
            and request.requester_id == p["requester_id"]
            and request.cost_center_code == p["cost_center_code"]
            and request.estimated_total_amount == amount
            and request.currency == p["currency"]
            and request.desired_date.isoformat() == p["desired_date"]
            and request.delivery_location_code == p["delivery_location_code"]
            and request.business_reason_summary == p["business_reason_summary"]
            and request.status == "SUBMITTED"
            and request.policy_code == p["policy_code"]
            and request.policy_version == int(p["policy_version"])
            and actual_items == expected_items
            and reservation.request_id == request.request_id
            and reservation.cost_center_code == p["cost_center_code"]
            and reservation.amount == amount
            and reservation.currency == p["currency"]
            and reservation.status == "ACTIVE"
        )


class ProcurementExecutionResult(BaseModel):
    model_config = ConfigDict(frozen=True)

    workflow_run_id: str
    workflow_state: WorkflowState
    workflow_version: int
    human_review_reason: str | None = None


class ProcurementPlanExecutionService:
    """Execute the approved one-step plan and persist every terminal outcome."""

    def __init__(
        self,
        session_factory: sessionmaker[Session],
        gateway: ActionGatewayPort,
    ) -> None:
        self._session_factory = session_factory
        self._gateway = gateway
        self._activity = ExecutionActivityRegistry()

    def is_active(self, workflow_run_id: str) -> bool:
        return self._activity.is_active(workflow_run_id)

    def execute(
        self,
        *,
        workflow_run_id: str,
        expected_workflow_version: int,
        plan_id: str,
        plan_version: int,
        approval_id: str,
        actor_id: str,
    ) -> ProcurementExecutionResult:
        with self._activity.claim(workflow_run_id):
            return self._execute_once(
                workflow_run_id=workflow_run_id,
                expected_workflow_version=expected_workflow_version,
                plan_id=plan_id,
                plan_version=plan_version,
                approval_id=approval_id,
                actor_id=actor_id,
            )

    def _execute_once(
        self,
        *,
        workflow_run_id: str,
        expected_workflow_version: int,
        plan_id: str,
        plan_version: int,
        approval_id: str,
        actor_id: str,
    ) -> ProcurementExecutionResult:
        with self._session_factory.begin() as session:
            workflows = WorkflowRepository(session)
            workflow = workflows.get(workflow_run_id)
            plans = ActionPlanRepository(session)
            plan = plans.get(plan_id, plan_version)
            record = plans.get_record(plan_id, plan_version)
            if (
                workflow.version != expected_workflow_version
                or workflow.workflow_state is not WorkflowState.RUNNING
                or record.workflow_run_id != workflow_run_id
                or plan.scenario_key != "procurement"
                or plan.plan_type != "OFFICE_PROCUREMENT"
                or len(plan.steps) != 1
            ):
                raise ActionPlanVersionError(
                    "workflow is not at the procurement execution checkpoint"
                )
            plans.begin_execution(plan_id, plan_version)
            step = record.steps[0]
            plans.start_step(plan_id, plan_version, step.step_id)
            workflow = workflows.transition(
                workflow_run_id,
                expected_version=workflow.version,
                target=WorkflowState.EXECUTING,
                event_type="PROCUREMENT_EXECUTION_STARTED",
                payload={"action_plan_id": plan_id},
            )
            executing_version = workflow.version
            proposal = plan.steps[0].proposal
            step_id = step.step_id

        try:
            result = self._gateway.execute(
                proposal,
                actor_id=actor_id,
                approval_id=approval_id,
                idempotency_key=f"procurement:{plan_id}:v{plan_version}:step:1",
            )
        except Exception as exc:
            return self._require_human(
                workflow_run_id,
                executing_version,
                plan_id,
                plan_version,
                step_id,
                type(exc).__name__,
                ActionPlanStepStatus.FAILED,
            )
        step_status = {
            ActionGatewayOutcome.SUCCEEDED: ActionPlanStepStatus.SUCCEEDED,
            ActionGatewayOutcome.REPLAYED: ActionPlanStepStatus.REPLAYED,
            ActionGatewayOutcome.TOOL_FAILED: ActionPlanStepStatus.FAILED,
            ActionGatewayOutcome.RESULT_UNKNOWN: ActionPlanStepStatus.RESULT_UNKNOWN,
            ActionGatewayOutcome.VERIFICATION_FAILED: (
                ActionPlanStepStatus.VERIFICATION_FAILED
            ),
        }[result.outcome]
        if step_status not in {
            ActionPlanStepStatus.SUCCEEDED,
            ActionPlanStepStatus.REPLAYED,
        }:
            return self._require_human(
                workflow_run_id,
                executing_version,
                plan_id,
                plan_version,
                step_id,
                result.outcome.value,
                step_status,
            )
        with self._session_factory.begin() as session:
            plans = ActionPlanRepository(session)
            plans.finish_step(
                plan_id, plan_version, step_id, status=step_status
            )
            plans.complete_execution(plan_id, plan_version)
            workflow = WorkflowRepository(session).transition(
                workflow_run_id,
                expected_version=executing_version,
                target=WorkflowState.COMPLETED,
                event_type="PROCUREMENT_EXECUTION_COMPLETED",
                payload={
                    "action_plan_id": plan_id,
                    "gateway_outcome": result.outcome.value,
                },
            )
        return ProcurementExecutionResult(
            workflow_run_id=workflow.id,
            workflow_state=workflow.workflow_state,
            workflow_version=workflow.version,
        )

    def _require_human(
        self,
        workflow_run_id: str,
        workflow_version: int,
        plan_id: str,
        plan_version: int,
        step_id: str,
        reason: str,
        step_status: ActionPlanStepStatus,
    ) -> ProcurementExecutionResult:
        with self._session_factory.begin() as session:
            plans = ActionPlanRepository(session)
            plans.finish_step(
                plan_id,
                plan_version,
                step_id,
                status=step_status,
                error_code=reason,
            )
            plans.require_human(plan_id, plan_version)
            workflow = WorkflowRepository(session).transition(
                workflow_run_id,
                expected_version=workflow_version,
                target=WorkflowState.WAITING_HUMAN,
                event_type="PROCUREMENT_EXECUTION_REQUIRES_HUMAN",
                payload={
                    "action_plan_id": plan_id,
                    "step_id": step_id,
                    "reason": reason,
                },
            )
        return ProcurementExecutionResult(
            workflow_run_id=workflow.id,
            workflow_state=workflow.workflow_state,
            workflow_version=workflow.version,
            human_review_reason=reason,
        )

    def recover_interrupted_execution(
        self,
        *,
        workflow_run_id: str,
        plan_id: str,
        plan_version: int,
    ) -> ProcurementExecutionResult:
        """Record an in-flight procurement write as result-unknown for review."""
        self._activity.require_inactive(workflow_run_id)
        with self._session_factory.begin() as session:
            workflows = WorkflowRepository(session)
            workflow = workflows.get(workflow_run_id)
            if workflow.workflow_state is WorkflowState.EXECUTING:
                record = ActionPlanRepository(session).get_record(plan_id, plan_version)
                executing_steps = [
                    step
                    for step in record.steps
                    if ActionPlanStepStatus(step.status)
                    is ActionPlanStepStatus.EXECUTING
                ]
                if len(executing_steps) != 1:
                    raise ActionPlanVersionError(
                        "interrupted procurement execution has no unique active step"
                    )
                step = executing_steps[0]
                return self._require_human(
                    workflow_run_id,
                    workflow.version,
                    plan_id,
                    plan_version,
                    step.step_id,
                    "INTERRUPTED_EXECUTION_REQUIRES_RECONCILIATION",
                    ActionPlanStepStatus.RESULT_UNKNOWN,
                )
            if workflow.workflow_state is not WorkflowState.WAITING_HUMAN:
                raise ActionPlanVersionError(
                    "workflow is not an interrupted procurement execution"
                )
        return ProcurementExecutionResult(
            workflow_run_id=workflow.id,
            workflow_state=workflow.workflow_state,
            workflow_version=workflow.version,
            human_review_reason="INTERRUPTED_EXECUTION_REQUIRES_RECONCILIATION",
        )
