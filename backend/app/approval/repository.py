"""Concurrency-safe creation, decision and validation of approval tasks."""

from datetime import UTC, datetime
from uuid import uuid4

from sqlalchemy import select, update
from sqlalchemy.orm import Session, selectinload

from app.actions.contracts import ActionPlan, ActionProposal
from app.approval.contracts import ApprovalDecisionType, ApprovalStatus
from app.approval.models import ApprovalDecisionRecord, ApprovalTask


class ApprovalNotFoundError(LookupError):
    """Raised when an approval task does not exist."""


class ApprovalActorMismatchError(PermissionError):
    """Raised when someone other than the assignee attempts a decision."""


class ApprovalConflictError(RuntimeError):
    """Raised when an approval already has a final decision."""


class ApprovalValidationError(PermissionError):
    """Raised when an action lacks a matching approved task."""


class ApprovalRepository:
    """Persist approvals bound to exactly one action or composite plan."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def create(
        self,
        workflow_run_id: str,
        proposal: ActionProposal,
        approver_id: str,
        *,
        approval_id: str | None = None,
    ) -> ApprovalTask:
        normalized_approver = approver_id.strip()
        if not normalized_approver:
            raise ValueError("approver_id must not be blank")
        task = ApprovalTask(
            id=approval_id or str(uuid4()),
            workflow_run_id=workflow_run_id,
            action_id=proposal.action_id,
            action_version=proposal.version,
            action_digest=proposal.content_digest,
            approver_id=normalized_approver,
            status=ApprovalStatus.PENDING.value,
        )
        self._session.add(task)
        self._session.flush()
        return task

    def create_for_plan(
        self,
        workflow_run_id: str,
        plan: ActionPlan,
        approver_id: str,
        *,
        approval_id: str | None = None,
    ) -> ApprovalTask:
        normalized_approver = approver_id.strip()
        if not normalized_approver:
            raise ValueError("approver_id must not be blank")
        task = ApprovalTask(
            id=approval_id or str(uuid4()),
            workflow_run_id=workflow_run_id,
            action_plan_id=plan.plan_id,
            action_plan_version=plan.version,
            action_plan_digest=plan.content_digest,
            approver_id=normalized_approver,
            status=ApprovalStatus.PENDING.value,
        )
        self._session.add(task)
        self._session.flush()
        return task

    def get(self, approval_id: str) -> ApprovalTask:
        task = self._session.get(ApprovalTask, approval_id)
        if task is None:
            raise ApprovalNotFoundError(approval_id)
        return task

    def get_assigned(self, approval_id: str, *, approver_id: str) -> ApprovalTask:
        """Return one task only when it belongs to the authenticated approver."""
        task = self._session.scalar(
            select(ApprovalTask)
            .options(selectinload(ApprovalTask.decision))
            .where(ApprovalTask.id == approval_id)
        )
        if task is None:
            raise ApprovalNotFoundError(approval_id)
        if task.approver_id != approver_id:
            raise ApprovalActorMismatchError(approver_id)
        if task.approval_status is ApprovalStatus.QUEUED:
            # A queued stage is intentionally undiscoverable until its turn.
            raise ApprovalNotFoundError(approval_id)
        return task

    def list_assigned(
        self,
        approver_id: str,
        *,
        status: ApprovalStatus | None = None,
    ) -> list[ApprovalTask]:
        """List an approver's tasks, oldest first for the pending queue."""
        statement = (
            select(ApprovalTask)
            .options(selectinload(ApprovalTask.decision))
            .where(
                ApprovalTask.approver_id == approver_id,
                ApprovalTask.status != ApprovalStatus.QUEUED.value,
            )
        )
        if status is not None:
            statement = statement.where(ApprovalTask.status == status.value)
        if status is ApprovalStatus.PENDING:
            statement = statement.order_by(ApprovalTask.created_at, ApprovalTask.id)
        else:
            statement = statement.order_by(
                ApprovalTask.created_at.desc(),
                ApprovalTask.id.desc(),
            )
        return list(self._session.scalars(statement))

    def decide(
        self,
        approval_id: str,
        *,
        actor_id: str,
        decision: ApprovalDecisionType,
        comment: str | None = None,
    ) -> ApprovalTask:
        task = self.get(approval_id)
        if task.approver_id != actor_id:
            raise ApprovalActorMismatchError(actor_id)

        target_status = (
            ApprovalStatus.APPROVED
            if decision is ApprovalDecisionType.APPROVE
            else ApprovalStatus.REJECTED
        )
        decided_at = datetime.now(UTC)
        result = self._session.execute(
            update(ApprovalTask)
            .where(
                ApprovalTask.id == approval_id,
                ApprovalTask.status == ApprovalStatus.PENDING.value,
            )
            .values(status=target_status.value, decided_at=decided_at)
            .execution_options(synchronize_session=False)
        )
        if result.rowcount != 1:
            raise ApprovalConflictError(approval_id)

        normalized_comment = comment.strip() if comment and comment.strip() else None
        self._session.add(
            ApprovalDecisionRecord(
                approval_id=approval_id,
                decision=decision.value,
                decided_by=actor_id,
                comment=normalized_comment,
            )
        )
        self._session.flush()
        self._session.expire(task)
        return self.get(approval_id)

    def require_approved(
        self,
        approval_id: str,
        proposal: ActionProposal,
    ) -> ApprovalTask:
        task = self.get(approval_id)
        if task.approval_status is not ApprovalStatus.APPROVED:
            raise ApprovalValidationError("approval is not approved")
        if task.action_id != proposal.action_id:
            raise ApprovalValidationError("approval belongs to another action")
        if task.action_version != proposal.version:
            raise ApprovalValidationError("approval action version is stale")
        if task.action_digest != proposal.content_digest:
            raise ApprovalValidationError("approved action content has changed")
        return task

    def require_plan_approved(
        self,
        approval_id: str,
        plan: ActionPlan,
    ) -> ApprovalTask:
        task = self.get(approval_id)
        if task.approval_status is not ApprovalStatus.APPROVED:
            raise ApprovalValidationError("approval is not approved")
        if task.action_plan_id != plan.plan_id:
            raise ApprovalValidationError("approval belongs to another action plan")
        if task.action_plan_version != plan.version:
            raise ApprovalValidationError("approval action plan version is stale")
        if task.action_plan_digest != plan.content_digest:
            raise ApprovalValidationError("approved action plan content has changed")
        return task

    def require_approved_plan_step(
        self,
        approval_id: str,
        plan: ActionPlan,
        proposal: ActionProposal,
    ) -> ApprovalTask:
        task = self.require_plan_approved(approval_id, plan)
        if not plan.contains_proposal(proposal):
            raise ApprovalValidationError(
                "action proposal is not part of the approved plan"
            )
        return task
