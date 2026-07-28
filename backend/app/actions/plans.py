"""Persistence and integrity checks for immutable composite action plans."""

from datetime import UTC, datetime

from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from app.actions.contracts import (
    ActionPlan,
    ActionPlanStatus,
    ActionPlanStep,
    ActionPlanStepStatus,
    ActionStepReversibility,
)
from app.actions.models import ActionPlanRecord, ActionPlanStepRecord
from app.actions.repository import ActionProposalRepository


class ActionPlanNotFoundError(LookupError):
    """Raised when a requested plan version is absent."""


class ActionPlanVersionError(RuntimeError):
    """Raised when content no longer matches the current plan version."""


class ActionPlanRepository:
    """Store plan content and its action proposals in one caller transaction."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def add(
        self,
        workflow_run_id: str,
        plan: ActionPlan,
        *,
        status: ActionPlanStatus = ActionPlanStatus.DRAFT,
    ) -> ActionPlanRecord:
        proposals = ActionProposalRepository(self._session)
        for step in plan.steps:
            proposals.add(workflow_run_id, step.proposal)

        record = ActionPlanRecord(
            plan_id=plan.plan_id,
            workflow_run_id=workflow_run_id,
            scenario_key=plan.scenario_key,
            plan_type=plan.plan_type,
            subject_reference=plan.subject_reference,
            version=plan.version,
            content_summary=plan.content_summary,
            content_digest=plan.content_digest,
            status=status.value,
            steps=[
                ActionPlanStepRecord(
                    step_id=step.step_id,
                    step_order=step.step_order,
                    depends_on_step_ids=list(step.depends_on_step_ids),
                    action_id=step.proposal.action_id,
                    action_version=step.proposal.version,
                    action_digest=step.proposal.content_digest,
                    reversibility=step.reversibility.value,
                    compensation_action_type=step.compensation_action_type,
                    status=(
                        ActionPlanStepStatus.BLOCKED.value
                        if step.depends_on_step_ids
                        else ActionPlanStepStatus.PENDING.value
                    ),
                )
                for step in plan.steps
            ],
        )
        self._session.add(record)
        self._session.flush()
        return record

    def get_record(self, plan_id: str, version: int) -> ActionPlanRecord:
        record = self._session.scalar(
            select(ActionPlanRecord)
            .options(selectinload(ActionPlanRecord.steps))
            .where(
                ActionPlanRecord.plan_id == plan_id,
                ActionPlanRecord.version == version,
            )
        )
        if record is None:
            raise ActionPlanNotFoundError(f"{plan_id}:{version}")
        return record

    def get(self, plan_id: str, version: int) -> ActionPlan:
        record = self.get_record(plan_id, version)
        proposals = ActionProposalRepository(self._session)
        plan = ActionPlan(
            plan_id=record.plan_id,
            scenario_key=record.scenario_key,
            plan_type=record.plan_type,
            subject_reference=record.subject_reference,
            version=record.version,
            content_summary=record.content_summary,
            steps=tuple(
                ActionPlanStep(
                    step_id=step.step_id,
                    step_order=step.step_order,
                    depends_on_step_ids=tuple(step.depends_on_step_ids),
                    proposal=proposals.get(step.action_id, step.action_version),
                    reversibility=ActionStepReversibility(step.reversibility),
                    compensation_action_type=step.compensation_action_type,
                )
                for step in record.steps
            ),
        )
        if plan.content_digest != record.content_digest:
            raise ValueError("persisted action plan digest does not match content")
        for contract, persisted in zip(plan.steps, record.steps, strict=True):
            if contract.proposal.content_digest != persisted.action_digest:
                raise ValueError("persisted action plan step digest does not match")
        return plan

    def require_current(self, plan: ActionPlan) -> ActionPlanRecord:
        latest_version = self._session.scalar(
            select(func.max(ActionPlanRecord.version)).where(
                ActionPlanRecord.plan_id == plan.plan_id
            )
        )
        if latest_version is None:
            raise ActionPlanNotFoundError(plan.plan_id)
        if latest_version != plan.version:
            raise ActionPlanVersionError("action plan version is stale")
        persisted = self.get(plan.plan_id, plan.version)
        if persisted.content_digest != plan.content_digest:
            raise ActionPlanVersionError("action plan content is not authoritative")
        return self.get_record(plan.plan_id, plan.version)

    def begin_execution(
        self,
        plan_id: str,
        version: int,
    ) -> ActionPlanRecord:
        """Move an approved plan to execution without resetting step progress."""
        record = self.get_record(plan_id, version)
        status = ActionPlanStatus(record.status)
        if status is ActionPlanStatus.APPROVED:
            record.status = ActionPlanStatus.EXECUTING.value
        elif status is not ActionPlanStatus.EXECUTING:
            raise ActionPlanVersionError(
                f"action plan cannot execute from {status.value}"
            )
        self._session.flush()
        return record

    def start_step(
        self,
        plan_id: str,
        version: int,
        step_id: str,
    ) -> ActionPlanStepRecord:
        """Durably mark the next dependency-ready step before a side effect."""
        record = self.get_record(plan_id, version)
        step = next((item for item in record.steps if item.step_id == step_id), None)
        if step is None:
            raise ActionPlanNotFoundError(step_id)
        if ActionPlanStepStatus(step.status) is not ActionPlanStepStatus.PENDING:
            raise ActionPlanVersionError(
                f"action plan step cannot start from {step.status}"
            )
        by_id = {item.step_id: item for item in record.steps}
        successful = {
            ActionPlanStepStatus.SUCCEEDED.value,
            ActionPlanStepStatus.REPLAYED.value,
        }
        if any(by_id[item].status not in successful for item in step.depends_on_step_ids):
            raise ActionPlanVersionError("action plan step dependencies are incomplete")
        step.status = ActionPlanStepStatus.EXECUTING.value
        step.attempt_count += 1
        step.last_error_code = None
        step.started_at = datetime.now(UTC)
        self._session.flush()
        return step

    def finish_step(
        self,
        plan_id: str,
        version: int,
        step_id: str,
        *,
        status: ActionPlanStepStatus,
        error_code: str | None = None,
    ) -> ActionPlanStepRecord:
        """Persist a gateway outcome and unlock newly ready dependencies."""
        if status is ActionPlanStepStatus.EXECUTING:
            raise ValueError("finish status cannot be EXECUTING")
        record = self.get_record(plan_id, version)
        step = next((item for item in record.steps if item.step_id == step_id), None)
        if step is None:
            raise ActionPlanNotFoundError(step_id)
        if ActionPlanStepStatus(step.status) is not ActionPlanStepStatus.EXECUTING:
            raise ActionPlanVersionError(
                f"action plan step cannot finish from {step.status}"
            )
        step.status = status.value
        step.last_error_code = error_code
        step.completed_at = datetime.now(UTC)
        if status in {
            ActionPlanStepStatus.SUCCEEDED,
            ActionPlanStepStatus.REPLAYED,
        }:
            self._unlock_ready_steps(record)
        self._session.flush()
        return step

    def complete_execution(
        self,
        plan_id: str,
        version: int,
    ) -> ActionPlanRecord:
        record = self.get_record(plan_id, version)
        successful = {
            ActionPlanStepStatus.SUCCEEDED.value,
            ActionPlanStepStatus.REPLAYED.value,
        }
        if any(step.status not in successful for step in record.steps):
            raise ActionPlanVersionError("not all action plan steps succeeded")
        record.status = ActionPlanStatus.COMPLETED.value
        self._session.flush()
        return record

    def require_human(
        self,
        plan_id: str,
        version: int,
    ) -> ActionPlanRecord:
        record = self.get_record(plan_id, version)
        record.status = ActionPlanStatus.WAITING_HUMAN.value
        self._session.flush()
        return record

    @staticmethod
    def _unlock_ready_steps(record: ActionPlanRecord) -> None:
        by_id = {item.step_id: item for item in record.steps}
        successful = {
            ActionPlanStepStatus.SUCCEEDED.value,
            ActionPlanStepStatus.REPLAYED.value,
        }
        for candidate in record.steps:
            if candidate.status != ActionPlanStepStatus.BLOCKED.value:
                continue
            if all(
                by_id[dependency].status in successful
                for dependency in candidate.depends_on_step_ids
            ):
                candidate.status = ActionPlanStepStatus.PENDING.value
