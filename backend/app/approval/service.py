"""Application service joining final approval decisions to workflow resumption."""

from pydantic import BaseModel, ConfigDict

from app.actions.contracts import ActionPlanStatus
from app.actions.plans import ActionPlanRepository
from app.approval.contracts import ApprovalDecisionType, ApprovalStatus
from app.approval.repository import ApprovalRepository
from app.workflow.repository import WorkflowRepository
from app.workflow.state import WorkflowState


class ApprovalWorkflowMismatchError(RuntimeError):
    """Raised when an approval is submitted against another workflow run."""


class ApprovalWorkflowResult(BaseModel):
    """Stable result returned after approval and workflow state change commit together."""

    model_config = ConfigDict(frozen=True)

    approval_id: str
    approval_status: ApprovalStatus
    workflow_run_id: str
    workflow_state: WorkflowState
    workflow_version: int


class ApprovalWorkflowService:
    """Persist one human decision and move its waiting workflow deterministically."""

    def __init__(
        self,
        approvals: ApprovalRepository,
        workflows: WorkflowRepository,
        plans: ActionPlanRepository | None = None,
    ) -> None:
        self._approvals = approvals
        self._workflows = workflows
        self._plans = plans

    def decide(
        self,
        *,
        workflow_run_id: str,
        expected_workflow_version: int,
        approval_id: str,
        actor_id: str,
        decision: ApprovalDecisionType,
        comment: str | None = None,
    ) -> ApprovalWorkflowResult:
        """Approve and resume, or reject and complete, without committing."""
        task = self._approvals.get(approval_id)
        if task.workflow_run_id != workflow_run_id:
            raise ApprovalWorkflowMismatchError(approval_id)

        task = self._approvals.decide(
            approval_id,
            actor_id=actor_id,
            decision=decision,
            comment=comment,
        )
        approved = task.approval_status is ApprovalStatus.APPROVED
        if task.action_plan_id is not None:
            if task.action_plan_version is None or self._plans is None:
                raise RuntimeError(
                    "plan approval requires an action plan repository"
                )
            plan = self._plans.get(
                task.action_plan_id,
                task.action_plan_version,
            )
            if (
                task.action_plan_digest != plan.content_digest
                or plan.scenario_key
                != self._workflows.get(workflow_run_id).scenario_key
            ):
                raise ApprovalWorkflowMismatchError(approval_id)
            record = self._plans.require_current(plan)
            if record.status != ActionPlanStatus.PENDING_APPROVAL.value:
                raise ApprovalWorkflowMismatchError(
                    "action plan is not pending approval"
                )
            record.status = (
                ActionPlanStatus.APPROVED.value
                if approved
                else ActionPlanStatus.CANCELLED.value
            )
        target = WorkflowState.RUNNING if approved else WorkflowState.COMPLETED
        event_type = "APPROVAL_APPROVED" if approved else "APPROVAL_REJECTED"
        workflow_run = self._workflows.transition(
            workflow_run_id,
            expected_version=expected_workflow_version,
            target=target,
            event_type=event_type,
            payload={
                "approval_id": approval_id,
                "decision": decision.value,
                "decided_by": actor_id,
            },
        )
        return ApprovalWorkflowResult(
            approval_id=approval_id,
            approval_status=task.approval_status,
            workflow_run_id=workflow_run.id,
            workflow_state=workflow_run.workflow_state,
            workflow_version=workflow_run.version,
        )
