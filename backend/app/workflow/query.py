"""Requester-scoped read service for authoritative workflow progress."""

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload, sessionmaker

from app.actions.models import ActionPlanRecord, ActionPlanStepRecord
from app.actions.repository import ActionProposalRepository
from app.approval.sequences import ApprovalSequenceNotFoundError, ApprovalSequenceRepository
from app.tickets.repository import (
    TicketActorMismatchError,
    TicketRepository,
)
from app.workflow.contracts import (
    WorkflowActionPlanProgressView,
    WorkflowActionPlanStepProgressView,
    WorkflowBusinessSummaryView,
    WorkflowProgressEventView,
    WorkflowProgressView,
)
from app.workflow.models import WorkflowRun
from app.workflow.repository import WorkflowNotFoundError
from app.workflow.state import TERMINAL_WORKFLOW_STATES, WorkflowState


class WorkflowProgressQueryService:
    """Expose safe workflow facts only after requester ownership is verified."""

    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self._session_factory = session_factory

    def get_for_requester(
        self,
        workflow_run_id: str,
        *,
        requester_id: str,
    ) -> WorkflowProgressView:
        """Return one authoritative progress view or an explicit access error."""
        with self._session_factory() as session:
            workflow_run = session.scalar(
                select(WorkflowRun)
                .options(selectinload(WorkflowRun.events))
                .where(WorkflowRun.id == workflow_run_id)
            )
            if workflow_run is None:
                raise WorkflowNotFoundError(workflow_run_id)

            ticket = TicketRepository(session).ticket_for_workflow(workflow_run_id)
            if ticket is None:
                raise WorkflowNotFoundError(workflow_run_id)
            if ticket.requester_id != requester_id:
                raise TicketActorMismatchError(requester_id)

            state = WorkflowState(workflow_run.state)
            plan_record = session.scalar(
                select(ActionPlanRecord)
                .options(selectinload(ActionPlanRecord.steps))
                .where(ActionPlanRecord.workflow_run_id == workflow_run_id)
                .order_by(ActionPlanRecord.version.desc())
            )
            return WorkflowProgressView(
                workflow_run_id=workflow_run.id,
                ticket_id=ticket.id,
                scenario_key=workflow_run.scenario_key,
                state=state,
                version=workflow_run.version,
                terminal=state in TERMINAL_WORKFLOW_STATES,
                created_at=workflow_run.created_at,
                updated_at=workflow_run.updated_at,
                events=tuple(
                    WorkflowProgressEventView(
                        sequence=event.sequence,
                        event_type=event.event_type,
                        from_state=(
                            WorkflowState(event.from_state)
                            if event.from_state is not None
                            else None
                        ),
                        to_state=WorkflowState(event.to_state),
                        created_at=event.created_at,
                    )
                    for event in workflow_run.events
                ),
                action_plan=(
                    self._plan_progress(session, plan_record)
                    if plan_record is not None
                    else None
                ),
                approval_route=self._approval_route(session, workflow_run.id),
                business_summary=(
                    self._business_summary(session, plan_record)
                    if plan_record is not None
                    else None
                ),
            )

    @staticmethod
    def _approval_route(session: Session, workflow_run_id: str):
        """Return an optional safe route without exposing digest material."""
        sequences = ApprovalSequenceRepository(session)
        try:
            return sequences.to_progress_view(
                sequences.get_for_workflow(workflow_run_id)
            )
        except ApprovalSequenceNotFoundError:
            return None

    @staticmethod
    def _business_summary(
        session: Session,
        record: ActionPlanRecord,
    ) -> WorkflowBusinessSummaryView | None:
        """Build a whitelist-only requester summary for known plan types."""
        if record.plan_type != "OFFICE_PROCUREMENT" or len(record.steps) != 1:
            return None
        proposal = ActionProposalRepository(session).get(
            record.steps[0].action_id,
            record.steps[0].action_version,
        )
        parameters = proposal.parameters
        raw_items = parameters.get("items")
        if not isinstance(raw_items, list):
            return None
        items: list[str] = []
        for item in raw_items:
            if not isinstance(item, dict):
                return None
            name = item.get("item_name")
            quantity = item.get("quantity")
            if not isinstance(name, str) or not isinstance(quantity, int):
                return None
            items.append(f"{name} × {quantity}")
        amount = parameters.get("estimated_total_amount")
        currency = parameters.get("currency")
        cost_center = parameters.get("cost_center_code")
        desired_date = parameters.get("desired_date")
        reason = parameters.get("business_reason_summary")
        required = (amount, currency, cost_center, desired_date, reason)
        if not items or any(not isinstance(value, str) for value in required):
            return None
        return WorkflowBusinessSummaryView(
            kind="procurement",
            fields={
                "item_summary": "、".join(items),
                "estimated_total_amount": str(amount),
                "currency": str(currency),
                "cost_center_code": str(cost_center),
                "desired_date": str(desired_date),
                "business_reason": str(reason),
            },
        )

    @staticmethod
    def _plan_progress(
        session: Session,
        record: ActionPlanRecord,
    ) -> WorkflowActionPlanProgressView:
        """Project only display-safe plan fields after requester ownership check."""
        proposals = ActionProposalRepository(session)
        return WorkflowActionPlanProgressView(
            plan_id=record.plan_id,
            plan_version=record.version,
            plan_type=record.plan_type,
            subject_reference=record.subject_reference,
            content_summary=record.content_summary,
            status=record.status,
            steps=tuple(
                WorkflowProgressQueryService._step_progress(step, proposals)
                for step in record.steps
            ),
        )

    @staticmethod
    def _step_progress(
        step: ActionPlanStepRecord,
        proposals: ActionProposalRepository,
    ) -> WorkflowActionPlanStepProgressView:
        proposal = proposals.get(step.action_id, step.action_version)
        return WorkflowActionPlanStepProgressView(
            step_id=step.step_id,
            step_order=step.step_order,
            depends_on_step_ids=tuple(step.depends_on_step_ids),
            action_type=proposal.action_type,
            content_summary=proposal.content_summary,
            reversibility=step.reversibility,
            status=step.status,
            attempt_count=step.attempt_count,
            last_error_code=step.last_error_code,
            started_at=step.started_at,
            completed_at=step.completed_at,
        )
