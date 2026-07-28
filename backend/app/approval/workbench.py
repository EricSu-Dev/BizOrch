"""Read-only approval workbench assembled from authoritative business facts."""

from sqlalchemy.orm import Session, sessionmaker

from app.actions.plans import ActionPlanRepository
from app.actions.repository import ActionProposalRepository
from app.approval.contracts import (
    ApprovalDecisionType,
    ApprovalDecisionView,
    ApprovalPlanStepView,
    ApprovalPlanView,
    ApprovalStatus,
    ApprovalSubjectType,
    ApprovalTaskView,
)
from app.approval.models import ApprovalTask
from app.approval.repository import ApprovalRepository
from app.approval.sequences import ApprovalSequenceRepository
from app.tickets.repository import TicketRepository
from app.workflow.repository import WorkflowRepository


class ApprovalWorkbenchService:
    """Build approver-scoped task views without changing approval state."""

    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self._session_factory = session_factory

    def list_for_approver(
        self,
        approver_id: str,
        *,
        status: ApprovalStatus | None = None,
    ) -> tuple[ApprovalTaskView, ...]:
        with self._session_factory() as session:
            tasks = ApprovalRepository(session).list_assigned(
                approver_id,
                status=status,
            )
            return tuple(self._view(session, task) for task in tasks)

    def get_for_approver(
        self,
        approval_id: str,
        *,
        approver_id: str,
    ) -> ApprovalTaskView:
        with self._session_factory() as session:
            task = ApprovalRepository(session).get_assigned(
                approval_id,
                approver_id=approver_id,
            )
            return self._view(session, task)

    @staticmethod
    def _view(session: Session, task: ApprovalTask) -> ApprovalTaskView:
        workflow = WorkflowRepository(session).get(task.workflow_run_id)
        ticket = TicketRepository(session).ticket_for_workflow(task.workflow_run_id)
        decision = task.decision
        common = {
            "approval_id": task.id,
            "workflow_run_id": task.workflow_run_id,
            "workflow_state": workflow.state,
            "workflow_version": workflow.version,
            "ticket_id": ticket.id if ticket else None,
            "requester_id": ticket.requester_id if ticket else None,
            "approval_sequence_id": task.approval_sequence_id,
            "stage_order": task.stage_order,
            "stage_code": task.stage_code,
            "approval_route": (
                ApprovalSequenceRepository(session).to_progress_view(
                    ApprovalSequenceRepository(session).get(
                        task.approval_sequence_id
                    )
                )
                if task.approval_sequence_id
                else None
            ),
            "status": task.approval_status,
            "created_at": task.created_at,
            "decided_at": task.decided_at,
            "decision": (
                ApprovalDecisionView(
                    decision=ApprovalDecisionType(decision.decision),
                    decided_by=decision.decided_by,
                    comment=decision.comment,
                    created_at=decision.created_at,
                )
                if decision
                else None
            ),
        }
        if task.action_plan_id is not None:
            if task.action_plan_version is None:
                raise ValueError("plan approval lacks a plan version")
            plan = ActionPlanRepository(session).get(
                task.action_plan_id,
                task.action_plan_version,
            )
            return ApprovalTaskView(
                **common,
                approval_subject_type=ApprovalSubjectType.PLAN,
                action_id=None,
                action_version=None,
                action_type=None,
                target_resource=None,
                parameters=None,
                content_summary=plan.content_summary,
                action_plan=ApprovalPlanView(
                    plan_id=plan.plan_id,
                    plan_version=plan.version,
                    scenario_key=plan.scenario_key,
                    plan_type=plan.plan_type,
                    subject_reference=plan.subject_reference,
                    content_summary=plan.content_summary,
                    steps=tuple(
                        ApprovalPlanStepView(
                            step_id=step.step_id,
                            step_order=step.step_order,
                            depends_on_step_ids=step.depends_on_step_ids,
                            action_id=step.proposal.action_id,
                            action_version=step.proposal.version,
                            action_type=step.proposal.action_type,
                            target_resource=step.proposal.target_resource,
                            parameters=step.proposal.parameters,
                            content_summary=step.proposal.content_summary,
                            reversibility=step.reversibility.value,
                            compensation_action_type=(
                                step.compensation_action_type
                            ),
                        )
                        for step in plan.steps
                    ),
                ),
            )

        if task.action_id is None or task.action_version is None:
            raise ValueError("action approval lacks an action version")
        proposal = ActionProposalRepository(session).get(
            task.action_id,
            task.action_version,
        )
        return ApprovalTaskView(
            **common,
            approval_subject_type=ApprovalSubjectType.ACTION,
            action_id=task.action_id,
            action_version=task.action_version,
            action_type=proposal.action_type,
            target_resource=proposal.target_resource,
            parameters=proposal.parameters,
            content_summary=proposal.content_summary,
            action_plan=None,
        )
