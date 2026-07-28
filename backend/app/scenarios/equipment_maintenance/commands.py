"""Deterministic maintenance intake, policy routing and proposal persistence."""

from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from app.actions.contracts import ActionProposal
from app.actions.repository import ActionProposalRepository
from app.agents.contracts import AgentWorkflowSnapshot
from app.approval.checkpoint import ApprovalCheckpointCoordinator
from app.approval.contracts import (
    ApprovalDecisionType,
    ApprovalStatus,
)
from app.approval.repository import ApprovalRepository
from app.policy.contracts import PolicyDecision, PolicyOutcome
from app.scenarios.equipment_maintenance.contracts import (
    MaintenanceRequestContext,
    MaintenanceRequestDraft,
    ProductionImpact,
)
from app.scenarios.equipment_maintenance.policy import MaintenancePolicyEngine
from app.scenarios.equipment_maintenance.execution import (
    MaintenanceRequestExecutionService,
)
from app.tickets.service import TicketProjectionService
from app.workflow.models import WorkflowEvent
from app.workflow.repository import WorkflowNotFoundError, WorkflowRepository
from app.workflow.state import WorkflowState


class MaintenanceRequestActorMismatchError(PermissionError):
    """Raised when an actor accesses another employee's maintenance request."""


class MaintenanceRequestCommandService:
    """Collect maintenance facts and persist the deterministic policy result."""

    SCENARIO_KEY = "equipment_maintenance"
    TICKET_TITLE = "工业设备报修与维护申请"

    _TARGET_BY_OUTCOME = {
        PolicyOutcome.NEEDS_INPUT: WorkflowState.WAITING_USER,
        PolicyOutcome.APPROVAL_REQUIRED: WorkflowState.WAITING_APPROVAL,
        PolicyOutcome.HUMAN_REVIEW: WorkflowState.WAITING_HUMAN,
        PolicyOutcome.DENIED: WorkflowState.COMPLETED,
        PolicyOutcome.NO_ACTION: WorkflowState.COMPLETED,
    }
    _EVENT_BY_OUTCOME = {
        PolicyOutcome.NEEDS_INPUT: "MAINTENANCE_INFORMATION_REQUIRED",
        PolicyOutcome.APPROVAL_REQUIRED: "MAINTENANCE_APPROVAL_REQUIRED",
        PolicyOutcome.HUMAN_REVIEW: "MAINTENANCE_HUMAN_REVIEW_REQUIRED",
        PolicyOutcome.DENIED: "MAINTENANCE_REQUEST_DENIED",
        PolicyOutcome.NO_ACTION: "MAINTENANCE_ALREADY_ACTIVE",
    }

    def __init__(
        self,
        session_factory: sessionmaker[Session],
        ticket_projection: TicketProjectionService,
        policy_engine: MaintenancePolicyEngine | None = None,
        approval_checkpoint: ApprovalCheckpointCoordinator | None = None,
        execution_service: MaintenanceRequestExecutionService | None = None,
        action_executor_id: str = "system-action-executor",
    ) -> None:
        self._session_factory = session_factory
        self._ticket_projection = ticket_projection
        self._policy_engine = policy_engine or MaintenancePolicyEngine()
        self._approval_checkpoint = approval_checkpoint
        self._execution_service = execution_service
        self._action_executor_id = action_executor_id

    def create(
        self,
        draft: MaintenanceRequestDraft,
        *,
        context: MaintenanceRequestContext,
        actor_id: str,
        run_id: str,
    ) -> AgentWorkflowSnapshot:
        owned = self._require_owned(draft, actor_id=actor_id)
        with self._session_factory.begin() as session:
            workflows = WorkflowRepository(session)
            try:
                existing = workflows.get(run_id)
            except WorkflowNotFoundError:
                workflow = workflows.create(self.SCENARIO_KEY, run_id=run_id)
                workflow = workflows.transition(
                    workflow.id,
                    expected_version=workflow.version,
                    target=WorkflowState.RUNNING,
                    event_type="MAINTENANCE_INFORMATION_COLLECTION_STARTED",
                    payload={"request_draft": owned.model_dump(mode="json")},
                )
                self._evaluate_and_route(
                    session,
                    workflows,
                    workflow.id,
                    owned,
                    context,
                )
            else:
                if existing.scenario_key != self.SCENARIO_KEY:
                    raise ValueError("workflow belongs to another scenario")
                self._require_owned(
                    self._current_draft(session, run_id),
                    actor_id=actor_id,
                )
        return self.get(run_id, actor_id=actor_id)

    def provide_information(
        self,
        *,
        workflow_run_id: str,
        expected_workflow_version: int,
        updates: MaintenanceRequestDraft,
        context: MaintenanceRequestContext,
        actor_id: str,
    ) -> AgentWorkflowSnapshot:
        with self._session_factory.begin() as session:
            workflows = WorkflowRepository(session)
            workflow = workflows.get(workflow_run_id)
            if workflow.scenario_key != self.SCENARIO_KEY:
                raise ValueError("workflow belongs to another scenario")
            current = self._require_owned(
                self._current_draft(session, workflow_run_id),
                actor_id=actor_id,
            )
            if workflow.workflow_state is not WorkflowState.WAITING_USER:
                raise ValueError("maintenance workflow is not waiting for information")
            update_values = updates.model_dump(exclude_unset=True, mode="json")
            if not update_values or any(value is None for value in update_values.values()):
                raise ValueError("at least one non-null missing field is required")
            allowed_fields = set(self._required_fields_from_latest_policy(session, workflow_run_id))
            if set(update_values) - allowed_fields:
                raise ValueError("only required maintenance fields can be supplemented")
            merged = current.model_dump(mode="json")
            merged.update(update_values)
            resolved = MaintenanceRequestDraft.model_validate(merged)
            workflow = workflows.transition(
                workflow_run_id,
                expected_version=expected_workflow_version,
                target=WorkflowState.RUNNING,
                event_type="MAINTENANCE_INFORMATION_RECEIVED",
                payload={
                    "provided_fields": sorted(update_values),
                    "request_draft": resolved.model_dump(mode="json"),
                },
            )
            self._evaluate_and_route(
                session,
                workflows,
                workflow.id,
                resolved,
                context,
            )
        return self.get(workflow_run_id, actor_id=actor_id)

    def current_draft(
        self,
        workflow_run_id: str,
        *,
        actor_id: str,
    ) -> MaintenanceRequestDraft:
        with self._session_factory() as session:
            workflow = WorkflowRepository(session).get(workflow_run_id)
            if workflow.scenario_key != self.SCENARIO_KEY:
                raise ValueError("workflow belongs to another scenario")
            return self._require_owned(
                self._current_draft(session, workflow_run_id),
                actor_id=actor_id,
            )

    def current_policy_decision(
        self,
        workflow_run_id: str,
        *,
        actor_id: str,
    ) -> PolicyDecision:
        with self._session_factory() as session:
            draft = self._require_owned(
                self._current_draft(session, workflow_run_id),
                actor_id=actor_id,
            )
            del draft
            return self._latest_policy_decision(session, workflow_run_id)

    def get(
        self,
        workflow_run_id: str,
        *,
        actor_id: str,
    ) -> AgentWorkflowSnapshot:
        with self._session_factory() as session:
            workflow = WorkflowRepository(session).get(workflow_run_id)
            if workflow.scenario_key != self.SCENARIO_KEY:
                raise ValueError("workflow belongs to another scenario")
            self._require_owned(
                self._current_draft(session, workflow_run_id),
                actor_id=actor_id,
            )
        return self._snapshot(workflow_run_id, requester_id=actor_id)

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
        """Commit one assigned decision and resume the persisted approval pause."""
        if self._approval_checkpoint is None:
            raise RuntimeError("maintenance approval checkpoint is not configured")
        with self._session_factory() as session:
            workflow = WorkflowRepository(session).get(workflow_run_id)
            if workflow.scenario_key != self.SCENARIO_KEY:
                raise ValueError("workflow belongs to another scenario")
            requester_id = self._current_draft(
                session,
                workflow_run_id,
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
        return self._snapshot(
            workflow_run_id,
            requester_id=requester_id or "",
        )

    def resume_recorded_approval(
        self,
        *,
        workflow_run_id: str,
        approval_id: str,
    ) -> AgentWorkflowSnapshot:
        """Recover after the SQL decision committed before graph resume."""
        if self._approval_checkpoint is None:
            raise RuntimeError("maintenance approval checkpoint is not configured")
        with self._session_factory() as session:
            requester_id = self._current_draft(
                session,
                workflow_run_id,
            ).requester_id
        checkpoint = self._approval_checkpoint.resume_recorded_decision(
            workflow_run_id=workflow_run_id,
            approval_id=approval_id,
        )
        self._execute_approved_if_needed(checkpoint)
        return self._snapshot(
            workflow_run_id,
            requester_id=requester_id or "",
        )

    def _snapshot(
        self,
        workflow_run_id: str,
        *,
        requester_id: str,
    ) -> AgentWorkflowSnapshot:
        with self._session_factory() as session:
            workflow = WorkflowRepository(session).get(workflow_run_id)
            if workflow.scenario_key != self.SCENARIO_KEY:
                raise ValueError("workflow belongs to another scenario")
            route_payload = self._latest_route_payload(session, workflow_run_id)
            state = workflow.workflow_state
            version = workflow.version
            action_id = self._optional_str(route_payload.get("action_id"))
            raw_action_version = route_payload.get("action_version")
            action_version = (
                raw_action_version
                if isinstance(raw_action_version, int)
                else None
            )
            approval_id = self._optional_str(route_payload.get("approval_id"))
            approval_status = None
            if approval_id is not None:
                approval_status = ApprovalRepository(session).get(
                    approval_id
                ).approval_status.value
            missing_fields = tuple(
                str(field) for field in route_payload.get("missing_fields", ())
            )
            execution_outcome = self._latest_execution_outcome(
                session,
                workflow_run_id,
            )
            subject_reference = self._current_draft(
                session,
                workflow_run_id,
            ).equipment_code

        checkpoint_pending = False
        next_nodes: tuple[str, ...] = ()
        if (
            self._approval_checkpoint is not None
            and approval_id is not None
            and action_id is not None
            and action_version is not None
        ):
            if state is WorkflowState.WAITING_APPROVAL:
                checkpoint = self._approval_checkpoint.arm(
                    workflow_run_id=workflow_run_id,
                    workflow_version=version,
                    approval_id=approval_id,
                    action_id=action_id,
                    action_version=action_version,
                )
            else:
                checkpoint = self._approval_checkpoint.snapshot(workflow_run_id)
            checkpoint_pending = checkpoint.checkpoint_pending
            next_nodes = checkpoint.next_nodes

        link = self._ticket_projection.sync(
            workflow_run_id,
            requester_id=requester_id,
            title=self.TICKET_TITLE,
            subject_reference=subject_reference,
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
            action_id=action_id,
            action_version=action_version,
            approval_id=approval_id,
            approval_status=approval_status,
            execution_outcome=execution_outcome,
            missing_fields=missing_fields,
        )

    def _execute_approved_if_needed(self, checkpoint) -> None:
        if (
            self._execution_service is None
            or checkpoint.approval_status is not ApprovalStatus.APPROVED
        ):
            return
        with self._session_factory() as session:
            workflow = WorkflowRepository(session).get(checkpoint.workflow_run_id)
            if workflow.workflow_state is not WorkflowState.RUNNING:
                return
            expected_version = workflow.version
        self._execution_service.execute(
            workflow_run_id=checkpoint.workflow_run_id,
            expected_workflow_version=expected_version,
            action_id=checkpoint.action_id,
            action_version=checkpoint.action_version,
            approval_id=checkpoint.approval_id,
            actor_id=self._action_executor_id,
            idempotency_key=(
                f"maintenance:{checkpoint.action_id}:v{checkpoint.action_version}"
            ),
        )

    def _evaluate_and_route(
        self,
        session: Session,
        workflows: WorkflowRepository,
        workflow_run_id: str,
        draft: MaintenanceRequestDraft,
        context: MaintenanceRequestContext,
    ) -> None:
        workflow = workflows.get(workflow_run_id)
        decision = self._policy_engine.evaluate(draft, context)
        payload: dict[str, object] = {
            "request_draft": draft.model_dump(mode="json"),
            "policy_decision": decision.model_dump(mode="json"),
        }
        if decision.outcome is PolicyOutcome.NEEDS_INPUT:
            payload["missing_fields"] = list(
                self._required_fields(draft, decision)
            )
        elif decision.outcome is PolicyOutcome.APPROVAL_REQUIRED:
            proposal = self._build_action_proposal(draft, context)
            ActionProposalRepository(session).add(workflow_run_id, proposal)
            approval = ApprovalRepository(session).create(
                workflow_run_id,
                proposal,
                decision.approver_id or "",
            )
            payload.update(
                {
                    "action_id": proposal.action_id,
                    "action_version": proposal.version,
                    "action_digest": proposal.content_digest,
                    "approval_id": approval.id,
                }
            )
        workflows.transition(
            workflow_run_id,
            expected_version=workflow.version,
            target=self._TARGET_BY_OUTCOME[decision.outcome],
            event_type=self._EVENT_BY_OUTCOME[decision.outcome],
            payload=payload,
        )

    @staticmethod
    def _build_action_proposal(
        draft: MaintenanceRequestDraft,
        context: MaintenanceRequestContext,
    ) -> ActionProposal:
        equipment = context.equipment
        if equipment is None or draft.production_impact is None:
            raise ValueError("complete equipment facts are required for a proposal")
        priority = {
            ProductionImpact.STOPPED: "HIGH",
            ProductionImpact.SLOWDOWN: "MEDIUM",
            ProductionImpact.NONE: "LOW",
        }[draft.production_impact]
        return ActionProposal(
            action_id=str(uuid4()),
            action_type="create_maintenance_work_order",
            target_resource=(
                f"equipment/{equipment.equipment_code}/maintenance-work-orders"
            ),
            parameters={
                "requester_id": draft.requester_id,
                "equipment_code": equipment.equipment_code,
                "expected_equipment_version": equipment.version,
                "fault_description": draft.fault_description,
                "observed_at": draft.observed_at.isoformat(),
                "production_impact": draft.production_impact.value,
                "safety_observation": draft.safety_observation,
                "business_reason": draft.business_reason,
                "priority": priority,
            },
            version=1,
            content_summary=(
                f"为设备 {equipment.equipment_code} 创建{priority}优先级维修工单，"
                f"当前设备版本 {equipment.version}"
            ),
        )

    @staticmethod
    def _required_fields(
        draft: MaintenanceRequestDraft,
        decision: PolicyDecision,
    ) -> tuple[str, ...]:
        missing = list(draft.missing_fields())
        if "EQUIPMENT_NOT_RESOLVED" in decision.reason_codes:
            missing.append("equipment_code")
        if "VAGUE_FAULT_DESCRIPTION" in decision.reason_codes:
            missing.append("fault_description")
        return tuple(dict.fromkeys(missing))

    @classmethod
    def _required_fields_from_latest_policy(
        cls,
        session: Session,
        workflow_run_id: str,
    ) -> tuple[str, ...]:
        payload = cls._latest_route_payload(session, workflow_run_id)
        return tuple(str(field) for field in payload.get("missing_fields", ()))

    @staticmethod
    def _current_draft(
        session: Session,
        workflow_run_id: str,
    ) -> MaintenanceRequestDraft:
        events = session.scalars(
            select(WorkflowEvent)
            .where(WorkflowEvent.run_id == workflow_run_id)
            .order_by(WorkflowEvent.sequence.desc())
        )
        for event in events:
            payload = event.payload or {}
            if "request_draft" in payload:
                return MaintenanceRequestDraft.model_validate(payload["request_draft"])
        raise ValueError("maintenance workflow has no persisted request draft")

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
            payload = event.payload or {}
            if "policy_decision" in payload:
                return dict(payload)
        raise ValueError("maintenance workflow has no policy decision")

    @classmethod
    def _latest_policy_decision(
        cls,
        session: Session,
        workflow_run_id: str,
    ) -> PolicyDecision:
        payload = cls._latest_route_payload(session, workflow_run_id)
        return PolicyDecision.model_validate(payload["policy_decision"])

    @staticmethod
    def _latest_execution_outcome(
        session: Session,
        workflow_run_id: str,
    ) -> str | None:
        events = session.scalars(
            select(WorkflowEvent)
            .where(WorkflowEvent.run_id == workflow_run_id)
            .order_by(WorkflowEvent.sequence.desc())
        )
        for event in events:
            if not event.event_type.startswith("MAINTENANCE_ACTION_"):
                continue
            gateway = (event.payload or {}).get("gateway_result")
            if isinstance(gateway, dict) and isinstance(gateway.get("outcome"), str):
                return gateway["outcome"]
            if event.event_type == "MAINTENANCE_ACTION_PRECONDITION_REQUIRES_HUMAN":
                return "HUMAN_REVIEW"
        return None

    @staticmethod
    def _require_owned(
        draft: MaintenanceRequestDraft,
        *,
        actor_id: str,
    ) -> MaintenanceRequestDraft:
        if draft.requester_id not in (None, actor_id):
            raise MaintenanceRequestActorMismatchError(actor_id)
        return draft.model_copy(update={"requester_id": actor_id})

    @staticmethod
    def _optional_str(value: object) -> str | None:
        return value if isinstance(value, str) and value else None
