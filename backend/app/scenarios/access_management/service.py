"""Application service that starts the access-request workflow slice."""

from uuid import uuid4

from pydantic import BaseModel, ConfigDict

from app.actions.contracts import ActionProposal
from app.actions.repository import ActionProposalRepository
from app.approval.repository import ApprovalRepository
from app.policy.contracts import PolicyDecision, PolicyOutcome
from app.scenarios.access_management.contracts import (
    AccessRequestContext,
    AccessRequestDraft,
)
from app.scenarios.access_management.policy import AccessPolicyEngine
from app.workflow.repository import WorkflowRepository
from app.workflow.state import WorkflowState


class AccessRequestIntakeResult(BaseModel):
    """Stable output of the first deterministic workflow segment."""

    model_config = ConfigDict(frozen=True)

    workflow_run_id: str
    workflow_state: WorkflowState
    workflow_version: int
    policy_decision: PolicyDecision
    action_proposal: ActionProposal | None = None
    approval_id: str | None = None


class AccessRequestIntakeService:
    """Join scenario policy decisions to domain-neutral workflow states."""

    SCENARIO_KEY = "access_management"

    _TARGET_BY_OUTCOME = {
        PolicyOutcome.NEEDS_INPUT: WorkflowState.WAITING_USER,
        PolicyOutcome.APPROVAL_REQUIRED: WorkflowState.WAITING_APPROVAL,
        PolicyOutcome.HUMAN_REVIEW: WorkflowState.WAITING_HUMAN,
        PolicyOutcome.DENIED: WorkflowState.COMPLETED,
        PolicyOutcome.NO_ACTION: WorkflowState.COMPLETED,
    }

    _EVENT_BY_OUTCOME = {
        PolicyOutcome.NEEDS_INPUT: "REQUEST_INFORMATION_REQUIRED",
        PolicyOutcome.APPROVAL_REQUIRED: "APPROVAL_REQUIRED",
        PolicyOutcome.HUMAN_REVIEW: "HUMAN_REVIEW_REQUIRED",
        PolicyOutcome.DENIED: "REQUEST_DENIED_BY_POLICY",
        PolicyOutcome.NO_ACTION: "REQUEST_ALREADY_SATISFIED",
    }

    def __init__(
        self,
        repository: WorkflowRepository,
        proposals: ActionProposalRepository,
        approvals: ApprovalRepository,
        policy_engine: AccessPolicyEngine | None = None,
    ) -> None:
        self._repository = repository
        self._proposals = proposals
        self._approvals = approvals
        self._policy_engine = policy_engine or AccessPolicyEngine()

    def start(
        self,
        draft: AccessRequestDraft,
        context: AccessRequestContext,
        *,
        run_id: str | None = None,
        action_id: str | None = None,
        approval_id: str | None = None,
    ) -> AccessRequestIntakeResult:
        """Start and route a request without committing the caller's transaction."""
        workflow_run = self._repository.create(self.SCENARIO_KEY, run_id=run_id)
        workflow_run = self._repository.transition(
            workflow_run.id,
            expected_version=workflow_run.version,
            target=WorkflowState.RUNNING,
            event_type="REQUEST_PROCESSING_STARTED",
        )

        return self._evaluate_and_route(
            workflow_run.id,
            workflow_run.version,
            draft,
            context,
            action_id=action_id,
            approval_id=approval_id,
        )

    def resume_with_information(
        self,
        draft: AccessRequestDraft,
        context: AccessRequestContext,
        *,
        run_id: str,
        expected_version: int,
        provided_fields: tuple[str, ...],
        action_id: str | None = None,
        approval_id: str | None = None,
    ) -> AccessRequestIntakeResult:
        """Re-evaluate the same run after committing structured user input."""
        if not provided_fields:
            raise ValueError("provided_fields must not be empty")
        workflow_run = self._repository.transition(
            run_id,
            expected_version=expected_version,
            target=WorkflowState.RUNNING,
            event_type="REQUEST_INFORMATION_RECEIVED",
            payload={
                "provided_fields": list(provided_fields),
                "request_draft": draft.model_dump(mode="json"),
            },
        )
        return self._evaluate_and_route(
            workflow_run.id,
            workflow_run.version,
            draft,
            context,
            action_id=action_id,
            approval_id=approval_id,
        )

    def _evaluate_and_route(
        self,
        run_id: str,
        expected_version: int,
        draft: AccessRequestDraft,
        context: AccessRequestContext,
        *,
        action_id: str | None,
        approval_id: str | None,
    ) -> AccessRequestIntakeResult:
        """Evaluate policy and persist exactly one matching lifecycle result."""

        decision = self._policy_engine.evaluate(draft, context)
        proposal: ActionProposal | None = None
        created_approval_id: str | None = None
        event_payload: dict[str, object] = {
            "policy_decision": decision.model_dump(mode="json")
        }
        if decision.outcome is PolicyOutcome.APPROVAL_REQUIRED:
            proposal = self._build_action_proposal(draft, action_id=action_id)
            self._proposals.add(run_id, proposal)
            task = self._approvals.create(
                run_id,
                proposal,
                decision.approver_id or "",
                approval_id=approval_id,
            )
            created_approval_id = task.id
            event_payload.update(
                {
                    "action_id": proposal.action_id,
                    "action_version": proposal.version,
                    "action_digest": proposal.content_digest,
                    "approval_id": task.id,
                }
            )

        target = self._TARGET_BY_OUTCOME[decision.outcome]
        workflow_run = self._repository.transition(
            run_id,
            expected_version=expected_version,
            target=target,
            event_type=self._EVENT_BY_OUTCOME[decision.outcome],
            payload=event_payload,
        )

        return AccessRequestIntakeResult(
            workflow_run_id=workflow_run.id,
            workflow_state=workflow_run.workflow_state,
            workflow_version=workflow_run.version,
            policy_decision=decision,
            action_proposal=proposal,
            approval_id=created_approval_id,
        )

    @staticmethod
    def _build_action_proposal(
        draft: AccessRequestDraft,
        *,
        action_id: str | None,
    ) -> ActionProposal:
        missing = draft.missing_fields()
        if missing:
            raise ValueError(
                "cannot create action proposal with missing fields: "
                + ", ".join(missing)
            )
        return ActionProposal(
            action_id=action_id or str(uuid4()),
            action_type="grant_application_access",
            target_resource=(
                f"employee/{draft.employee_id}/application/{draft.application_code}"
            ),
            parameters={
                "employee_id": draft.employee_id,
                "application_code": draft.application_code,
                "role_code": draft.role_code,
                "duration_days": draft.duration_days,
                "business_reason": draft.business_reason,
            },
            version=1,
            content_summary=(
                f"Grant {draft.role_code} access to {draft.application_code} "
                f"for {draft.employee_id} for {draft.duration_days} days"
            ),
        )
