"""Procurement intake, deterministic policy and serial approval routing."""

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from app.actions.contracts import ActionPlan, ActionPlanStatus
from app.actions.plans import ActionPlanRepository
from app.agents.contracts import AgentWorkflowSnapshot
from app.approval.contracts import ApprovalDecisionType, ApprovalStatus
from app.approval.sequence_checkpoint import (
    ApprovalSequenceCheckpointCoordinator,
)
from app.approval.sequences import ApprovalSequenceRepository
from app.scenarios.procurement.contracts import (
    ProcurementRequestContext,
    ProcurementRequestDraft,
)
from app.scenarios.procurement.policy import (
    ProcurementPolicyDecision,
    ProcurementPolicyEngine,
    ProcurementPolicyError,
    ProcurementPolicyErrorCode,
)
from app.scenarios.procurement.execution import ProcurementPlanExecutionService
from app.tickets.service import TicketProjectionService
from app.workflow.models import WorkflowEvent
from app.workflow.repository import WorkflowNotFoundError, WorkflowRepository
from app.workflow.state import WorkflowState


class ProcurementRequestCommandService:
    """Persist actor-owned procurement facts without generating approval or writes."""

    SCENARIO_KEY = "procurement"
    TICKET_TITLE = "采购与办公申请"

    def __init__(
        self,
        session_factory: sessionmaker[Session],
        ticket_projection: TicketProjectionService,
        *,
        policy_engine: ProcurementPolicyEngine | None = None,
        approval_checkpoint: ApprovalSequenceCheckpointCoordinator | None = None,
        execution_service: ProcurementPlanExecutionService | None = None,
        action_executor_id: str = "system-action-executor",
    ) -> None:
        self._session_factory = session_factory
        self._ticket_projection = ticket_projection
        self._policy_engine = policy_engine
        self._approval_checkpoint = approval_checkpoint
        self._execution_service = execution_service
        self._action_executor_id = action_executor_id

    def create(
        self,
        draft: ProcurementRequestDraft,
        *,
        context: ProcurementRequestContext | None = None,
        actor_id: str,
        run_id: str,
    ) -> AgentWorkflowSnapshot:
        self._require_owner(draft, actor_id)
        with self._session_factory.begin() as session:
            workflows = WorkflowRepository(session)
            try:
                workflows.get(run_id)
            except WorkflowNotFoundError:
                workflow = workflows.create(self.SCENARIO_KEY, run_id=run_id)
                workflow = workflows.transition(
                    run_id,
                    expected_version=workflow.version,
                    target=WorkflowState.RUNNING,
                    event_type="PROCUREMENT_INFORMATION_COLLECTION_STARTED",
                    payload={"request_draft": draft.model_dump(mode="json")},
                )
                self._evaluate_and_route(
                    session,
                    workflows,
                    workflow.id,
                    draft,
                    context,
                )
        return self.get(run_id, actor_id=actor_id)

    def provide_information(
        self,
        *,
        workflow_run_id: str,
        expected_workflow_version: int,
        draft: ProcurementRequestDraft,
        context: ProcurementRequestContext | None = None,
        actor_id: str,
    ) -> AgentWorkflowSnapshot:
        self._require_owner(draft, actor_id)
        with self._session_factory.begin() as session:
            workflows = WorkflowRepository(session)
            workflow = workflows.get(workflow_run_id)
            if workflow.scenario_key != self.SCENARIO_KEY:
                raise ValueError("workflow belongs to another scenario")
            if workflow.workflow_state is not WorkflowState.WAITING_USER:
                raise ValueError("procurement workflow is not waiting for information")
            workflow = workflows.transition(
                workflow_run_id,
                expected_version=expected_workflow_version,
                target=WorkflowState.RUNNING,
                event_type="PROCUREMENT_INFORMATION_RECEIVED",
                payload={"request_draft": draft.model_dump(mode="json")},
            )
            self._evaluate_and_route(
                session,
                workflows,
                workflow.id,
                draft,
                context,
            )
        return self.get(workflow_run_id, actor_id=actor_id)

    def current_draft(
        self, workflow_run_id: str, *, actor_id: str
    ) -> ProcurementRequestDraft:
        with self._session_factory() as session:
            draft = self._latest_draft(session, workflow_run_id)
            self._require_owner(draft, actor_id)
            return draft

    def get(
        self, workflow_run_id: str, *, actor_id: str
    ) -> AgentWorkflowSnapshot:
        with self._session_factory() as session:
            workflow = WorkflowRepository(session).get(workflow_run_id)
            if workflow.scenario_key != self.SCENARIO_KEY:
                raise ValueError("workflow belongs to another scenario")
            draft = self._latest_draft(session, workflow_run_id)
            self._require_owner(draft, actor_id)
            route_payload = self._latest_route_payload(
                session, workflow_run_id
            )
            missing = self._latest_missing_fields(
                session, workflow_run_id
            )
            state = workflow.workflow_state
            version = workflow.version
            sequence_id = self._optional_str(
                route_payload.get("approval_sequence_id")
            )
            plan_id = self._optional_str(route_payload.get("action_plan_id"))
            plan_version_raw = route_payload.get("action_plan_version")
            plan_version = (
                plan_version_raw if isinstance(plan_version_raw, int) else None
            )
            approval_id: str | None = None
            approval_status: str | None = None
            if sequence_id:
                sequence = ApprovalSequenceRepository(session).get(sequence_id)
                task = self._display_sequence_task(sequence)
                approval_id = task.id
                approval_status = task.approval_status.value
        checkpoint_pending = False
        next_nodes: tuple[str, ...] = ()
        if (
            self._approval_checkpoint is not None
            and sequence_id is not None
            and plan_id is not None
            and plan_version is not None
        ):
            if state is WorkflowState.WAITING_APPROVAL:
                checkpoint = self._approval_checkpoint.arm(
                    workflow_run_id=workflow_run_id,
                    workflow_version=version,
                    approval_sequence_id=sequence_id,
                    action_plan_id=plan_id,
                    action_plan_version=plan_version,
                )
            else:
                checkpoint = self._approval_checkpoint.snapshot(
                    workflow_run_id
                )
            checkpoint_pending = checkpoint.checkpoint_pending
            next_nodes = checkpoint.next_nodes
            approval_id = checkpoint.approval_id
            approval_status = checkpoint.approval_status.value
        link = self._ticket_projection.sync(
            workflow_run_id,
            requester_id=actor_id,
            title=self.TICKET_TITLE,
            subject_reference=draft.cost_center_code,
        )
        return AgentWorkflowSnapshot(
            scenario_key=self.SCENARIO_KEY,
            workflow_run_id=workflow_run_id,
            service_request_id=link.service_request_id,
            ticket_id=link.ticket_id,
            workflow_state=state,
            workflow_version=version,
            checkpoint_pending=checkpoint_pending,
            next_nodes=next_nodes,
            action_plan_id=plan_id,
            action_plan_version=plan_version,
            approval_sequence_id=sequence_id,
            approval_id=approval_id,
            approval_status=approval_status,
            missing_fields=missing,
        )

    def current_action_plan(
        self,
        workflow_run_id: str,
        *,
        actor_id: str,
    ) -> ActionPlan | None:
        with self._session_factory() as session:
            draft = self._latest_draft(session, workflow_run_id)
            self._require_owner(draft, actor_id)
            payload = self._latest_route_payload(session, workflow_run_id)
            plan_id = self._optional_str(payload.get("action_plan_id"))
            plan_version = payload.get("action_plan_version")
            if plan_id is None or not isinstance(plan_version, int):
                return None
            return ActionPlanRepository(session).get(plan_id, plan_version)

    def decide(
        self,
        *,
        workflow_run_id: str,
        approval_id: str,
        expected_workflow_version: int,
        actor_id: str,
        decision: ApprovalDecisionType,
        comment: str | None = None,
    ) -> AgentWorkflowSnapshot:
        """Decide one stage; only the final result resumes the workflow."""
        if self._approval_checkpoint is None:
            raise RuntimeError(
                "procurement approval checkpoint is not configured"
            )
        with self._session_factory() as session:
            requester_id = self._latest_draft(
                session, workflow_run_id
            ).requester_id
        checkpoint = self._approval_checkpoint.decide_and_resume(
            workflow_run_id=workflow_run_id,
            approval_id=approval_id,
            expected_workflow_version=expected_workflow_version,
            actor_id=actor_id,
            decision=decision,
            comment=comment,
        )
        self._execute_approved_if_needed(checkpoint)
        return self.get(workflow_run_id, actor_id=requester_id)

    def resume_recorded_approval(
        self,
        *,
        workflow_run_id: str,
        approval_sequence_id: str,
    ) -> AgentWorkflowSnapshot:
        """Repair a final SQL decision committed before graph resumption."""
        if self._approval_checkpoint is None:
            raise RuntimeError(
                "procurement approval checkpoint is not configured"
            )
        with self._session_factory() as session:
            requester_id = self._latest_draft(
                session, workflow_run_id
            ).requester_id
        checkpoint = self._approval_checkpoint.resume_recorded_decision(
            workflow_run_id=workflow_run_id,
            approval_sequence_id=approval_sequence_id,
        )
        self._execute_approved_if_needed(checkpoint)
        return self.get(workflow_run_id, actor_id=requester_id)

    def _execute_approved_if_needed(self, checkpoint) -> None:
        if (
            self._execution_service is None
            or checkpoint.approval_status is not ApprovalStatus.APPROVED
        ):
            return
        with self._session_factory() as session:
            workflow = WorkflowRepository(session).get(
                checkpoint.workflow_run_id
            )
            if workflow.workflow_state is not WorkflowState.RUNNING:
                return
            expected_version = workflow.version
        self._execution_service.execute(
            workflow_run_id=checkpoint.workflow_run_id,
            expected_workflow_version=expected_version,
            plan_id=checkpoint.action_plan_id,
            plan_version=checkpoint.action_plan_version,
            approval_id=checkpoint.approval_id,
            actor_id=self._action_executor_id,
        )

    def _evaluate_and_route(
        self,
        session: Session,
        workflows: WorkflowRepository,
        workflow_run_id: str,
        draft: ProcurementRequestDraft,
        context: ProcurementRequestContext | None,
    ) -> None:
        workflow = workflows.get(workflow_run_id)
        missing = draft.missing_fields()
        payload: dict[str, object] = {
            "request_draft": draft.model_dump(mode="json"),
            "missing_fields": list(missing),
        }
        if missing:
            target = WorkflowState.WAITING_USER
            event_type = "PROCUREMENT_INFORMATION_REQUIRED"
        elif self._policy_engine is None or context is None:
            target = WorkflowState.WAITING_HUMAN
            event_type = "PROCUREMENT_READY_FOR_DETERMINISTIC_POLICY"
            payload["policy_stage"] = "PENDING_DETERMINISTIC_POLICY"
        else:
            try:
                decision = self._policy_engine.evaluate(
                    workflow_run_id=workflow_run_id,
                    draft=draft,
                    context=context,
                )
            except ProcurementPolicyError as exc:
                fields = self._policy_fields(exc.code)
                payload.update(
                    {
                        "policy_error_code": exc.code.value,
                        "missing_fields": list(fields),
                    }
                )
                if fields:
                    target = WorkflowState.WAITING_USER
                    event_type = "PROCUREMENT_POLICY_INFORMATION_REQUIRED"
                else:
                    target = WorkflowState.WAITING_HUMAN
                    event_type = "PROCUREMENT_POLICY_HUMAN_REVIEW_REQUIRED"
            else:
                plan_record = ActionPlanRepository(session).add(
                    workflow_run_id,
                    decision.plan,
                    status=ActionPlanStatus.DRAFT,
                )
                sequence = ApprovalSequenceRepository(
                    session
                ).create_for_plan(
                    workflow_run_id,
                    decision.plan,
                    decision.route,
                    requester_id=draft.requester_id,
                )
                first_task = self._display_sequence_task(sequence)
                payload.update(
                    {
                        "policy_decision": self._policy_summary(decision),
                        "missing_fields": [],
                        "action_plan_id": plan_record.plan_id,
                        "action_plan_version": plan_record.version,
                        "action_plan_digest": plan_record.content_digest,
                        "approval_sequence_id": sequence.sequence_id,
                        "route_version": sequence.route_version,
                        "route_digest": sequence.route_digest,
                        "approval_id": first_task.id,
                    }
                )
                target = WorkflowState.WAITING_APPROVAL
                event_type = "PROCUREMENT_APPROVAL_SEQUENCE_REQUIRED"
        workflows.transition(
            workflow_run_id,
            expected_version=workflow.version,
            target=target,
            event_type=event_type,
            payload=payload,
        )

    @staticmethod
    def _policy_summary(
        decision: ProcurementPolicyDecision,
    ) -> dict[str, object]:
        return {
            "amount_band": decision.amount_band.value,
            "amount": format(decision.amount, ".2f"),
            "currency": decision.currency,
            "cost_center_code": decision.cost_center_code,
            "policy_code": decision.policy_code,
            "policy_version": decision.policy_version,
            "stage_count": len(decision.route.stages),
        }

    @staticmethod
    def _policy_fields(
        code: ProcurementPolicyErrorCode,
    ) -> tuple[str, ...]:
        by_code = {
            ProcurementPolicyErrorCode.MISSING_INFORMATION: (
                "items",
                "estimated_total_amount",
                "cost_center_code",
                "desired_date",
                "delivery_location_code",
                "business_reason",
            ),
            ProcurementPolicyErrorCode.ITEM_INVALID: ("items",),
            ProcurementPolicyErrorCode.ITEM_CATEGORY_NOT_ALLOWED: ("items",),
            ProcurementPolicyErrorCode.AMOUNT_INVALID: (
                "estimated_total_amount",
            ),
            ProcurementPolicyErrorCode.COST_CENTER_NOT_FOUND: (
                "cost_center_code",
            ),
            ProcurementPolicyErrorCode.COST_CENTER_INACTIVE: (
                "cost_center_code",
            ),
            ProcurementPolicyErrorCode.COST_CENTER_FORBIDDEN: (
                "cost_center_code",
            ),
            ProcurementPolicyErrorCode.CURRENCY_MISMATCH: (
                "cost_center_code",
            ),
            ProcurementPolicyErrorCode.DESIRED_DATE_INVALID: (
                "desired_date",
            ),
            ProcurementPolicyErrorCode.BUSINESS_REASON_INVALID: (
                "business_reason",
            ),
        }
        return by_code.get(code, ())

    @staticmethod
    def _display_sequence_task(sequence):
        pending = next(
            (
                task
                for task in sequence.stages
                if task.approval_status is ApprovalStatus.PENDING
            ),
            None,
        )
        if pending is not None:
            return pending
        rejected = next(
            (
                task
                for task in sequence.stages
                if task.approval_status is ApprovalStatus.REJECTED
            ),
            None,
        )
        if rejected is not None:
            return rejected
        if not sequence.stages:
            raise ValueError("approval sequence has no stages")
        return max(
            sequence.stages,
            key=lambda task: task.stage_order or 0,
        )

    @staticmethod
    def _latest_missing_fields(
        session: Session,
        workflow_run_id: str,
    ) -> tuple[str, ...]:
        events = session.scalars(
            select(WorkflowEvent)
            .where(WorkflowEvent.run_id == workflow_run_id)
            .order_by(WorkflowEvent.sequence.desc())
        )
        for event in events:
            raw = event.payload.get("missing_fields")
            if isinstance(raw, list):
                return tuple(str(field) for field in raw)
        return ()

    @staticmethod
    def _latest_route_payload(
        session: Session,
        workflow_run_id: str,
    ) -> dict[str, object]:
        events = session.scalars(
            select(WorkflowEvent)
            .where(WorkflowEvent.run_id == workflow_run_id)
            .order_by(WorkflowEvent.sequence.desc())
        )
        for event in events:
            if (
                "approval_sequence_id" in event.payload
                and "action_plan_id" in event.payload
            ):
                return dict(event.payload)
        return {}

    @staticmethod
    def _optional_str(value: object) -> str | None:
        return value if isinstance(value, str) and value else None

    @staticmethod
    def _latest_draft(
        session: Session, workflow_run_id: str
    ) -> ProcurementRequestDraft:
        events = session.scalars(
            select(WorkflowEvent)
            .where(WorkflowEvent.run_id == workflow_run_id)
            .order_by(WorkflowEvent.sequence.desc())
        )
        for event in events:
            raw = event.payload.get("request_draft")
            if isinstance(raw, dict):
                return ProcurementRequestDraft.model_validate(raw)
        raise ValueError("procurement workflow has no request draft")

    @staticmethod
    def _require_owner(draft: ProcurementRequestDraft, actor_id: str) -> None:
        if draft.requester_id != actor_id:
            raise PermissionError("procurement request belongs to another actor")
