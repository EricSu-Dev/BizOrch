"""Domain-neutral persistence and transitions for fixed serial approvals."""

from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import uuid4

from sqlalchemy import select, update
from sqlalchemy.orm import Session, selectinload

from app.actions.contracts import ActionPlan, ActionPlanStatus
from app.actions.plans import ActionPlanRepository, ActionPlanVersionError
from app.approval.contracts import (
    ApprovalDecisionType,
    ApprovalRoute,
    ApprovalRouteStage,
    ApprovalSequenceStageView,
    ApprovalSequenceStatus,
    ApprovalSequenceView,
    ApprovalRouteProgressView,
    ApprovalRouteStageProgressView,
    ApprovalStatus,
)
from app.approval.models import ApprovalSequence, ApprovalTask
from app.approval.repository import (
    ApprovalActorMismatchError,
    ApprovalConflictError,
    ApprovalRepository,
    ApprovalValidationError,
)


class ApprovalSequenceNotFoundError(LookupError):
    """Raised when a serial approval sequence does not exist."""


class ApprovalSequenceValidationError(ApprovalValidationError):
    """Raised when a route or sequence no longer matches its exact plan."""


@dataclass(frozen=True, slots=True)
class ApprovalSequenceDecision:
    """Transaction result after deciding one active sequence stage."""

    sequence_id: str
    decided_approval_id: str
    approval_status: ApprovalStatus
    sequence_status: ApprovalSequenceStatus
    next_approval_id: str | None
    sequence_complete: bool


class ApprovalSequenceRepository:
    """Create and advance fixed-order approval stages in one transaction."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def create_for_plan(
        self,
        workflow_run_id: str,
        plan: ActionPlan,
        route: ApprovalRoute,
        *,
        requester_id: str,
        sequence_id: str | None = None,
    ) -> ApprovalSequence:
        """Persist an exact plan route; only its first task becomes visible."""
        normalized_workflow = workflow_run_id.strip()
        if not normalized_workflow:
            raise ValueError("workflow_run_id must not be blank")
        route.require_separation_of_duties(requester_id)

        plan_record = ActionPlanRepository(self._session).require_current(plan)
        current_status = ActionPlanStatus(plan_record.status)
        if current_status not in {
            ActionPlanStatus.DRAFT,
            ActionPlanStatus.PENDING_APPROVAL,
        }:
            raise ActionPlanVersionError(
                f"action plan cannot enter approval from {current_status.value}"
            )

        digest = route.content_digest(plan)
        sequence = ApprovalSequence(
            sequence_id=sequence_id or str(uuid4()),
            workflow_run_id=normalized_workflow,
            action_plan_id=plan.plan_id,
            action_plan_version=plan.version,
            action_plan_digest=plan.content_digest,
            route_version=route.route_version,
            route_digest=digest,
            status=ApprovalSequenceStatus.PENDING.value,
            current_stage_order=1,
        )
        self._session.add(sequence)
        for stage in sorted(route.stages, key=lambda item: item.stage_order):
            self._session.add(
                ApprovalTask(
                    id=str(uuid4()),
                    workflow_run_id=normalized_workflow,
                    action_plan_id=plan.plan_id,
                    action_plan_version=plan.version,
                    action_plan_digest=plan.content_digest,
                    approval_sequence_id=sequence.sequence_id,
                    stage_order=stage.stage_order,
                    stage_code=stage.stage_code,
                    route_digest=digest,
                    approver_id=stage.approver_id,
                    status=(
                        ApprovalStatus.PENDING.value
                        if stage.stage_order == 1
                        else ApprovalStatus.QUEUED.value
                    ),
                )
            )
        plan_record.status = ActionPlanStatus.PENDING_APPROVAL.value
        self._session.flush()
        return self.get(sequence.sequence_id)

    def get(self, sequence_id: str) -> ApprovalSequence:
        sequence = self._session.scalar(
            select(ApprovalSequence)
            .options(
                selectinload(ApprovalSequence.stages).selectinload(
                    ApprovalTask.decision
                )
            )
            .where(ApprovalSequence.sequence_id == sequence_id)
        )
        if sequence is None:
            raise ApprovalSequenceNotFoundError(sequence_id)
        return sequence

    def get_for_workflow(self, workflow_run_id: str) -> ApprovalSequence:
        sequence = self._session.scalar(
            select(ApprovalSequence)
            .options(selectinload(ApprovalSequence.stages))
            .where(ApprovalSequence.workflow_run_id == workflow_run_id)
            .order_by(ApprovalSequence.created_at.desc())
        )
        if sequence is None:
            raise ApprovalSequenceNotFoundError(workflow_run_id)
        return sequence

    def get_by_approval(self, approval_id: str) -> ApprovalSequence:
        sequence_id = self._session.scalar(
            select(ApprovalTask.approval_sequence_id).where(
                ApprovalTask.id == approval_id
            )
        )
        if sequence_id is None:
            raise ApprovalSequenceNotFoundError(approval_id)
        return self.get(sequence_id)

    def decide(
        self,
        approval_id: str,
        *,
        actor_id: str,
        decision: ApprovalDecisionType,
        comment: str | None = None,
    ) -> ApprovalSequenceDecision:
        """Decide only the active stage and atomically advance or stop."""
        approvals = ApprovalRepository(self._session)
        task = approvals.get(approval_id)
        if task.approval_sequence_id is None or task.stage_order is None:
            raise ApprovalSequenceValidationError(
                "approval task does not belong to a sequence"
            )
        if task.approver_id != actor_id:
            raise ApprovalActorMismatchError(actor_id)
        if task.approval_status is not ApprovalStatus.PENDING:
            raise ApprovalConflictError(
                f"approval stage is not active: {task.approval_status.value}"
            )

        sequence = self.get(task.approval_sequence_id)
        if (
            sequence.sequence_status is not ApprovalSequenceStatus.PENDING
            or sequence.current_stage_order != task.stage_order
            or task.route_digest != sequence.route_digest
        ):
            raise ApprovalConflictError("approval sequence stage is not current")

        decided_task = approvals.decide(
            approval_id,
            actor_id=actor_id,
            decision=decision,
            comment=comment,
        )
        now = datetime.now(UTC)
        if decision is ApprovalDecisionType.REJECT:
            self._finish_rejected(sequence, task.stage_order, now)
            return ApprovalSequenceDecision(
                sequence_id=sequence.sequence_id,
                decided_approval_id=approval_id,
                approval_status=ApprovalStatus.REJECTED,
                sequence_status=ApprovalSequenceStatus.REJECTED,
                next_approval_id=None,
                sequence_complete=True,
            )

        next_task = next(
            (
                candidate
                for candidate in sequence.stages
                if candidate.stage_order == task.stage_order + 1
            ),
            None,
        )
        if next_task is None:
            self._finish_approved(sequence, task.stage_order, now)
            return ApprovalSequenceDecision(
                sequence_id=sequence.sequence_id,
                decided_approval_id=approval_id,
                approval_status=decided_task.approval_status,
                sequence_status=ApprovalSequenceStatus.APPROVED,
                next_approval_id=None,
                sequence_complete=True,
            )

        self._activate_next(sequence, task.stage_order, next_task)
        return ApprovalSequenceDecision(
            sequence_id=sequence.sequence_id,
            decided_approval_id=approval_id,
            approval_status=decided_task.approval_status,
            sequence_status=ApprovalSequenceStatus.PENDING,
            next_approval_id=next_task.id,
            sequence_complete=False,
        )

    def require_approved(
        self,
        sequence_id: str,
        plan: ActionPlan,
        route: ApprovalRoute,
    ) -> ApprovalSequence:
        """Require a fully approved sequence bound to exact plan and route."""
        sequence = self.get(sequence_id)
        expected_digest = route.content_digest(plan)
        if sequence.sequence_status is not ApprovalSequenceStatus.APPROVED:
            raise ApprovalSequenceValidationError(
                "approval sequence is not approved"
            )
        if (
            sequence.action_plan_id != plan.plan_id
            or sequence.action_plan_version != plan.version
            or sequence.action_plan_digest != plan.content_digest
        ):
            raise ApprovalSequenceValidationError(
                "approval sequence belongs to another action plan"
            )
        if (
            sequence.route_version != route.route_version
            or sequence.route_digest != expected_digest
        ):
            raise ApprovalSequenceValidationError(
                "approved route content has changed"
            )
        if not sequence.stages or any(
            task.approval_status is not ApprovalStatus.APPROVED
            or task.route_digest != expected_digest
            for task in sequence.stages
        ):
            raise ApprovalSequenceValidationError(
                "approval sequence stages are incomplete"
            )
        return sequence

    def require_integrity(
        self,
        sequence_id: str,
    ) -> tuple[ApprovalSequence, ActionPlan, ApprovalRoute]:
        """Reload and verify the current plan, stages and stable route digest."""
        sequence = self.get(sequence_id)
        plan = ActionPlanRepository(self._session).get(
            sequence.action_plan_id,
            sequence.action_plan_version,
        )
        ActionPlanRepository(self._session).require_current(plan)
        try:
            route = ApprovalRoute(
                route_version=sequence.route_version,
                stages=tuple(
                    ApprovalRouteStage(
                        stage_order=self._require_stage_order(task),
                        stage_code=self._require_stage_code(task),
                        approver_id=task.approver_id,
                    )
                    for task in sequence.stages
                ),
            )
        except ValueError as exc:
            raise ApprovalSequenceValidationError(
                "persisted approval route is invalid"
            ) from exc
        expected_digest = route.content_digest(plan)
        if (
            sequence.action_plan_digest != plan.content_digest
            or sequence.route_digest != expected_digest
            or any(
                task.action_plan_id != plan.plan_id
                or task.action_plan_version != plan.version
                or task.action_plan_digest != plan.content_digest
                or task.route_digest != expected_digest
                for task in sequence.stages
            )
        ):
            raise ApprovalSequenceValidationError(
                "approval sequence plan or route digest has changed"
            )
        self._require_status_invariants(sequence)
        return sequence, plan, route

    def to_view(self, sequence: ApprovalSequence) -> ApprovalSequenceView:
        """Convert authoritative persistence into a safe read projection."""
        return ApprovalSequenceView(
            sequence_id=sequence.sequence_id,
            workflow_run_id=sequence.workflow_run_id,
            action_plan_id=sequence.action_plan_id,
            action_plan_version=sequence.action_plan_version,
            action_plan_digest=sequence.action_plan_digest,
            route_version=sequence.route_version,
            route_digest=sequence.route_digest,
            status=sequence.sequence_status,
            current_stage_order=sequence.current_stage_order,
            stages=tuple(
                ApprovalSequenceStageView(
                    approval_id=task.id,
                    stage_order=self._require_stage_order(task),
                    stage_code=self._require_stage_code(task),
                    approver_id=task.approver_id,
                    status=task.approval_status,
                    created_at=task.created_at,
                    decided_at=task.decided_at,
                )
                for task in sequence.stages
            ),
            created_at=sequence.created_at,
            completed_at=sequence.completed_at,
        )

    def to_progress_view(
        self, sequence: ApprovalSequence
    ) -> ApprovalRouteProgressView:
        """Return a route projection that is safe to expose to a browser."""
        return ApprovalRouteProgressView(
            sequence_id=sequence.sequence_id,
            route_version=sequence.route_version,
            status=sequence.sequence_status,
            current_stage_order=sequence.current_stage_order,
            stages=tuple(
                ApprovalRouteStageProgressView(
                    approval_id=task.id,
                    stage_order=self._require_stage_order(task),
                    stage_code=self._require_stage_code(task),
                    approver_id=task.approver_id,
                    status=task.approval_status,
                    decided_at=task.decided_at,
                )
                for task in sequence.stages
            ),
            completed_at=sequence.completed_at,
        )

    def _activate_next(
        self,
        sequence: ApprovalSequence,
        current_order: int,
        next_task: ApprovalTask,
    ) -> None:
        sequence_result = self._session.execute(
            update(ApprovalSequence)
            .where(
                ApprovalSequence.sequence_id == sequence.sequence_id,
                ApprovalSequence.status == ApprovalSequenceStatus.PENDING.value,
                ApprovalSequence.current_stage_order == current_order,
            )
            .values(current_stage_order=current_order + 1)
            .execution_options(synchronize_session=False)
        )
        task_result = self._session.execute(
            update(ApprovalTask)
            .where(
                ApprovalTask.id == next_task.id,
                ApprovalTask.status == ApprovalStatus.QUEUED.value,
            )
            .values(status=ApprovalStatus.PENDING.value)
            .execution_options(synchronize_session=False)
        )
        if sequence_result.rowcount != 1 or task_result.rowcount != 1:
            raise ApprovalConflictError("approval sequence could not advance")
        self._session.flush()
        self._session.expire(sequence)
        self._session.expire(next_task)

    def _finish_approved(
        self,
        sequence: ApprovalSequence,
        current_order: int,
        completed_at: datetime,
    ) -> None:
        result = self._session.execute(
            update(ApprovalSequence)
            .where(
                ApprovalSequence.sequence_id == sequence.sequence_id,
                ApprovalSequence.status == ApprovalSequenceStatus.PENDING.value,
                ApprovalSequence.current_stage_order == current_order,
            )
            .values(
                status=ApprovalSequenceStatus.APPROVED.value,
                current_stage_order=None,
                completed_at=completed_at,
            )
            .execution_options(synchronize_session=False)
        )
        if result.rowcount != 1:
            raise ApprovalConflictError("approval sequence could not complete")
        plan_record = ActionPlanRepository(self._session).get_record(
            sequence.action_plan_id,
            sequence.action_plan_version,
        )
        if ActionPlanStatus(plan_record.status) is not ActionPlanStatus.PENDING_APPROVAL:
            raise ApprovalConflictError("action plan is not pending approval")
        plan_record.status = ActionPlanStatus.APPROVED.value
        self._session.flush()
        self._session.expire(sequence)

    def _finish_rejected(
        self,
        sequence: ApprovalSequence,
        current_order: int,
        completed_at: datetime,
    ) -> None:
        result = self._session.execute(
            update(ApprovalSequence)
            .where(
                ApprovalSequence.sequence_id == sequence.sequence_id,
                ApprovalSequence.status == ApprovalSequenceStatus.PENDING.value,
                ApprovalSequence.current_stage_order == current_order,
            )
            .values(
                status=ApprovalSequenceStatus.REJECTED.value,
                current_stage_order=None,
                completed_at=completed_at,
            )
            .execution_options(synchronize_session=False)
        )
        if result.rowcount != 1:
            raise ApprovalConflictError("approval sequence could not reject")
        self._session.execute(
            update(ApprovalTask)
            .where(
                ApprovalTask.approval_sequence_id == sequence.sequence_id,
                ApprovalTask.stage_order > current_order,
                ApprovalTask.status == ApprovalStatus.QUEUED.value,
            )
            .values(status=ApprovalStatus.CANCELLED.value)
            .execution_options(synchronize_session=False)
        )
        plan_record = ActionPlanRepository(self._session).get_record(
            sequence.action_plan_id,
            sequence.action_plan_version,
        )
        if ActionPlanStatus(plan_record.status) is not ActionPlanStatus.PENDING_APPROVAL:
            raise ApprovalConflictError("action plan is not pending approval")
        plan_record.status = ActionPlanStatus.CANCELLED.value
        self._session.flush()
        for task in sequence.stages:
            if (
                task.stage_order is not None
                and task.stage_order > current_order
            ):
                self._session.expire(task)
        self._session.expire(sequence)

    @staticmethod
    def _require_stage_order(task: ApprovalTask) -> int:
        if task.stage_order is None:
            raise ApprovalSequenceValidationError("sequence stage lacks order")
        return task.stage_order

    @staticmethod
    def _require_stage_code(task: ApprovalTask) -> str:
        if task.stage_code is None:
            raise ApprovalSequenceValidationError("sequence stage lacks code")
        return task.stage_code

    @staticmethod
    def _require_status_invariants(sequence: ApprovalSequence) -> None:
        statuses = [task.approval_status for task in sequence.stages]
        if sequence.sequence_status is ApprovalSequenceStatus.PENDING:
            if sequence.current_stage_order is None:
                raise ApprovalSequenceValidationError(
                    "pending sequence lacks current stage"
                )
            expected = [
                (
                    ApprovalStatus.APPROVED
                    if task.stage_order < sequence.current_stage_order
                    else ApprovalStatus.PENDING
                    if task.stage_order == sequence.current_stage_order
                    else ApprovalStatus.QUEUED
                )
                for task in sequence.stages
            ]
            if statuses != expected:
                raise ApprovalSequenceValidationError(
                    "pending approval sequence stage states are inconsistent"
                )
        elif sequence.sequence_status is ApprovalSequenceStatus.APPROVED:
            if sequence.current_stage_order is not None or any(
                status is not ApprovalStatus.APPROVED for status in statuses
            ):
                raise ApprovalSequenceValidationError(
                    "approved sequence stages are incomplete"
                )
        elif sequence.sequence_status is ApprovalSequenceStatus.REJECTED:
            rejected = [
                index
                for index, status in enumerate(statuses)
                if status is ApprovalStatus.REJECTED
            ]
            if (
                sequence.current_stage_order is not None
                or len(rejected) != 1
                or any(
                    status is not ApprovalStatus.APPROVED
                    for status in statuses[: rejected[0]]
                )
                or any(
                    status is not ApprovalStatus.CANCELLED
                    for status in statuses[rejected[0] + 1 :]
                )
            ):
                raise ApprovalSequenceValidationError(
                    "rejected sequence stages are inconsistent"
                )
