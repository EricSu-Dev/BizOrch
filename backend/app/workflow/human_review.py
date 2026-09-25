"""Operator decisions for workflows that deliberately stopped for human review."""

from datetime import datetime
from enum import Enum

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import distinct, func, select
from sqlalchemy.orm import Session, sessionmaker

from app.actions.models import ActionExecutionRecord, ActionPlanRecord, ActionProposalRecord
from app.tickets.models import Ticket
from app.tickets.service import TicketProjectionService
from app.workflow.models import WorkflowRun
from app.workflow.repository import WorkflowRepository
from app.workflow.state import WorkflowState


class HumanReviewOutcome(str, Enum):
    NOTE = "NOTE"
    CONFIRMED_SUCCESS = "CONFIRMED_SUCCESS"
    CONFIRMED_FAILURE = "CONFIRMED_FAILURE"
    CANCELLED = "CANCELLED"


class HumanReviewConflictError(RuntimeError):
    """The workflow is no longer waiting for a human decision."""


class HumanReviewValidationError(ValueError):
    """A manual conclusion lacks a safe public explanation or evidence."""


class HumanReviewItem(BaseModel):
    model_config = ConfigDict(frozen=True)

    workflow_run_id: str
    ticket_id: str
    scenario_key: str
    title: str
    requester_id: str
    can_confirm_success: bool
    version: int
    updated_at: datetime


class HumanReviewDecision(BaseModel):
    model_config = ConfigDict(extra="forbid")

    expected_workflow_version: int = Field(ge=0)
    outcome: HumanReviewOutcome
    public_summary: str = Field(min_length=10, max_length=1000)
    evidence_reference: str | None = Field(default=None, max_length=200)


class HumanReviewService:
    """Record a human conclusion without replaying or rewriting enterprise steps."""

    _TARGETS = {
        HumanReviewOutcome.CONFIRMED_SUCCESS: WorkflowState.COMPLETED,
        HumanReviewOutcome.CONFIRMED_FAILURE: WorkflowState.FAILED,
        HumanReviewOutcome.CANCELLED: WorkflowState.CANCELLED,
    }

    def __init__(
        self,
        session_factory: sessionmaker[Session],
        ticket_projection: TicketProjectionService,
    ) -> None:
        self._session_factory = session_factory
        self._ticket_projection = ticket_projection

    def list_pending(self) -> tuple[HumanReviewItem, ...]:
        with self._session_factory() as session:
            rows = session.execute(
                select(WorkflowRun, Ticket)
                .join(Ticket, Ticket.workflow_run_id == WorkflowRun.id)
                .where(WorkflowRun.state == WorkflowState.WAITING_HUMAN.value)
                .order_by(WorkflowRun.updated_at, WorkflowRun.id)
            ).all()
            return tuple(self._item(session, workflow, ticket) for workflow, ticket in rows)

    def decide(
        self,
        workflow_run_id: str,
        *,
        actor_id: str,
        decision: HumanReviewDecision,
    ) -> WorkflowState:
        if decision.outcome is not HumanReviewOutcome.NOTE and not (
            decision.evidence_reference or ""
        ).strip():
            raise HumanReviewValidationError(
                "closing human review requires an evidence reference"
            )
        summary = decision.public_summary.strip()
        if len(summary) < 10:
            raise HumanReviewValidationError(
                "public summary must contain at least 10 characters"
            )
        with self._session_factory.begin() as session:
            workflow = WorkflowRepository(session).get(workflow_run_id)
            if workflow.workflow_state is not WorkflowState.WAITING_HUMAN:
                raise HumanReviewConflictError(workflow_run_id)
            if decision.outcome is HumanReviewOutcome.CONFIRMED_SUCCESS:
                self._require_reconcilable_single_action(session, workflow_run_id)
            payload = {
                "reviewer_id": actor_id,
                "outcome": decision.outcome.value,
                "public_summary": summary,
                "evidence_reference": (decision.evidence_reference or "").strip(),
                "manual_conclusion": True,
            }
            repository = WorkflowRepository(session)
            if decision.outcome is HumanReviewOutcome.NOTE:
                updated = repository.record_progress(
                    workflow_run_id,
                    expected_version=decision.expected_workflow_version,
                    required_state=WorkflowState.WAITING_HUMAN,
                    event_type="HUMAN_REVIEW_NOTE_RECORDED",
                    payload=payload,
                )
            else:
                updated = repository.transition(
                    workflow_run_id,
                    expected_version=decision.expected_workflow_version,
                    target=self._TARGETS[decision.outcome],
                    event_type="HUMAN_REVIEW_CONCLUDED",
                    payload=payload,
                )
            state = updated.workflow_state
        self._ticket_projection.sync(workflow_run_id)
        return state

    @staticmethod
    def _require_reconcilable_single_action(session: Session, workflow_run_id: str) -> None:
        if session.scalar(
            select(ActionPlanRecord.id).where(ActionPlanRecord.workflow_run_id == workflow_run_id)
        ) is not None:
            raise HumanReviewValidationError(
                "a multi-step plan cannot be concluded successful without step reconciliation"
            )
        action_ids = session.scalars(
            select(distinct(ActionProposalRecord.action_id)).where(
                ActionProposalRecord.workflow_run_id == workflow_run_id
            )
        ).all()
        if len(action_ids) != 1:
            raise HumanReviewValidationError(
                "successful human review requires exactly one proposed action"
            )
        latest_version = session.scalar(
            select(func.max(ActionProposalRecord.version)).where(
                ActionProposalRecord.action_id == action_ids[0]
            )
        )
        latest_attempt = session.scalar(
            select(ActionExecutionRecord).where(
                ActionExecutionRecord.action_id == action_ids[0],
                ActionExecutionRecord.action_version == latest_version,
            ).order_by(ActionExecutionRecord.created_at.desc(), ActionExecutionRecord.id.desc()).limit(1)
        )
        if latest_attempt is None or latest_attempt.status not in (
            "EXECUTING", "RESULT_UNKNOWN", "VERIFICATION_FAILED"
        ):
            raise HumanReviewValidationError(
                "successful human review requires an uncertain execution attempt"
            )

    def _item(self, session: Session, workflow: WorkflowRun, ticket: Ticket) -> HumanReviewItem:
        try:
            self._require_reconcilable_single_action(session, workflow.id)
            can_confirm_success = True
        except HumanReviewValidationError:
            can_confirm_success = False
        return HumanReviewItem(
            workflow_run_id=workflow.id,
            ticket_id=ticket.id,
            scenario_key=workflow.scenario_key,
            title=ticket.title,
            requester_id=ticket.requester_id,
            can_confirm_success=can_confirm_success,
            version=workflow.version,
            updated_at=workflow.updated_at,
        )
