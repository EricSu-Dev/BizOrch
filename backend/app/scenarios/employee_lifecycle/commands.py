"""Persistent employee-lifecycle intake, policy and plan routing."""

from datetime import date
from collections.abc import Callable
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from app.actions.contracts import ActionPlan, ActionPlanStatus
from app.actions.plans import ActionPlanRepository
from app.agents.contracts import AgentWorkflowSnapshot
from app.approval.checkpoint import ApprovalCheckpointCoordinator
from app.approval.contracts import ApprovalDecisionType, ApprovalStatus
from app.approval.repository import ApprovalRepository
from app.policy.contracts import PolicyDecision, PolicyOutcome
from app.scenarios.employee_lifecycle.contracts import (
    EmployeeLifecycleContext,
    EmployeeLifecycleRequestDraft,
    EmployeeLifecycleRequestType,
)
from app.scenarios.employee_lifecycle.plans import EmployeeLifecyclePlanBuilder
from app.scenarios.employee_lifecycle.policy import EmployeeLifecyclePolicyEngine
from app.scenarios.employee_lifecycle.execution import (
    EmployeeLifecyclePlanExecutionService,
)
from app.tickets.service import TicketProjectionService
from app.workflow.models import WorkflowEvent
from app.workflow.repository import WorkflowNotFoundError, WorkflowRepository
from app.workflow.state import WorkflowState


class EmployeeLifecycleActorMismatchError(PermissionError):
    """Raised when an initiator attempts to access another initiator's workflow."""


class EmployeeLifecycleIntakeService:
    """Persist immutable intake facts and resume only the same workflow."""

    SCENARIO_KEY = "employee_lifecycle"
    _TITLE_BY_TYPE = {
        EmployeeLifecycleRequestType.ONBOARDING: "员工入职协同申请",
        EmployeeLifecycleRequestType.TRANSFER: "员工调岗协同申请",
        EmployeeLifecycleRequestType.OFFBOARDING: "员工离职协同申请",
    }

    def __init__(
        self,
        session_factory: sessionmaker[Session],
        tickets: TicketProjectionService,
        *,
        policy_engine: EmployeeLifecyclePolicyEngine | None = None,
        plan_builder: EmployeeLifecyclePlanBuilder | None = None,
        today_provider: Callable[[], date] | None = None,
        approval_checkpoint: ApprovalCheckpointCoordinator | None = None,
        execution_service: EmployeeLifecyclePlanExecutionService | None = None,
        action_executor_id: str = "system-action-executor",
    ) -> None:
        self._session_factory = session_factory
        self._tickets = tickets
        self._policy_engine = policy_engine or EmployeeLifecyclePolicyEngine()
        self._plan_builder = plan_builder or EmployeeLifecyclePlanBuilder()
        self._today_provider = today_provider or date.today
        self._approval_checkpoint = approval_checkpoint
        self._execution_service = execution_service
        self._action_executor_id = action_executor_id

    def create(
        self,
        draft: EmployeeLifecycleRequestDraft,
        *,
        context: EmployeeLifecycleContext,
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
                    event_type="EMPLOYEE_LIFECYCLE_INFORMATION_COLLECTION_STARTED",
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
        updates: dict[str, object],
        normalization_updates: dict[str, object] | None = None,
        context: EmployeeLifecycleContext,
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
                raise ValueError("lifecycle workflow is not waiting for information")
            allowed = set(self._latest_missing_fields(session, workflow_run_id))
            if not updates or set(updates) - allowed:
                raise ValueError("only currently missing lifecycle fields may be supplied")
            canonical_updates = normalization_updates or {}
            if any(value is None for value in (*updates.values(), *canonical_updates.values())):
                raise ValueError("supplement fields must be non-null")
            merged = current.model_dump(mode="json")
            merged.update(canonical_updates)
            merged.update(updates)
            resolved = EmployeeLifecycleRequestDraft.model_validate(merged)
            workflow = workflows.transition(
                workflow_run_id,
                expected_version=expected_workflow_version,
                target=WorkflowState.RUNNING,
                event_type="EMPLOYEE_LIFECYCLE_INFORMATION_RECEIVED",
                payload={
                    "provided_fields": sorted(updates),
                    "normalized_fields": sorted(canonical_updates),
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
            draft = self._require_owned(
                self._current_draft(session, workflow_run_id),
                actor_id=actor_id,
            )
            missing_fields = self._latest_missing_fields(session, workflow_run_id)
            route_payload = self._latest_route_payload(session, workflow_run_id)
            state = workflow.workflow_state
            version = workflow.version
            approval_id = self._optional_str(route_payload.get("approval_id"))
            approval_status = (
                ApprovalRepository(session).get(approval_id).approval_status.value
                if approval_id
                else None
            )
        checkpoint_pending = False
        next_nodes: tuple[str, ...] = ()
        plan_id = self._optional_str(route_payload.get("action_plan_id"))
        plan_version = route_payload.get("action_plan_version")
        if (
            self._approval_checkpoint is not None
            and approval_id is not None
            and plan_id is not None
            and isinstance(plan_version, int)
        ):
            if state is WorkflowState.WAITING_APPROVAL:
                checkpoint = self._approval_checkpoint.arm_plan(
                    workflow_run_id=workflow_run_id,
                    workflow_version=version,
                    approval_id=approval_id,
                    action_plan_id=plan_id,
                    action_plan_version=plan_version,
                )
            else:
                checkpoint = self._approval_checkpoint.snapshot(workflow_run_id)
            checkpoint_pending = checkpoint.checkpoint_pending
            next_nodes = checkpoint.next_nodes
        link = self._tickets.sync(
            workflow_run_id,
            requester_id=actor_id,
            title=self._TITLE_BY_TYPE[draft.request_type],
            subject_reference=draft.subject_employee_id,
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
            action_plan_version=(
                plan_version
                if isinstance(plan_version, int)
                else None
            ),
            approval_id=approval_id,
            approval_status=approval_status,
            missing_fields=missing_fields,
        )

    def current_draft(
        self,
        workflow_run_id: str,
        *,
        actor_id: str,
    ) -> EmployeeLifecycleRequestDraft:
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
            self._require_owned(
                self._current_draft(session, workflow_run_id),
                actor_id=actor_id,
            )
            payload = self._latest_route_payload(session, workflow_run_id)
            return PolicyDecision.model_validate(payload["policy_decision"])

    def current_action_plan(
        self,
        workflow_run_id: str,
        *,
        actor_id: str,
    ) -> ActionPlan | None:
        """Return the exact persisted plan version, if policy created one."""
        with self._session_factory() as session:
            self._require_owned(
                self._current_draft(session, workflow_run_id),
                actor_id=actor_id,
            )
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
        """Commit one plan decision and resume the exact persisted checkpoint."""
        if self._approval_checkpoint is None:
            raise RuntimeError("lifecycle approval checkpoint is not configured")
        with self._session_factory() as session:
            initiator_id = self._current_draft(
                session,
                workflow_run_id,
            ).initiator_id
        checkpoint = self._approval_checkpoint.decide_and_resume(
            workflow_run_id=workflow_run_id,
            approval_id=approval_id,
            expected_workflow_version=expected_workflow_version,
            actor_id=actor_id,
            decision=decision,
            comment=comment,
        )
        self._execute_approved_if_needed(checkpoint)
        return self.get(workflow_run_id, actor_id=initiator_id)

    def resume_recorded_approval(
        self,
        *,
        workflow_run_id: str,
        approval_id: str,
    ) -> AgentWorkflowSnapshot:
        """Recover after SQL committed but graph checkpoint did not resume."""
        if self._approval_checkpoint is None:
            raise RuntimeError("lifecycle approval checkpoint is not configured")
        with self._session_factory() as session:
            initiator_id = self._current_draft(
                session,
                workflow_run_id,
            ).initiator_id
        checkpoint = self._approval_checkpoint.resume_recorded_decision(
            workflow_run_id=workflow_run_id,
            approval_id=approval_id,
        )
        self._execute_approved_if_needed(checkpoint)
        return self.get(workflow_run_id, actor_id=initiator_id)

    def _execute_approved_if_needed(self, checkpoint) -> None:
        if (
            self._execution_service is None
            or checkpoint.approval_status is not ApprovalStatus.APPROVED
            or checkpoint.action_plan_id is None
            or checkpoint.action_plan_version is None
        ):
            return
        with self._session_factory() as session:
            workflow = WorkflowRepository(session).get(checkpoint.workflow_run_id)
            if workflow.workflow_state not in {
                WorkflowState.RUNNING,
                WorkflowState.EXECUTING,
            }:
                return
            if self._execution_service.is_active(checkpoint.workflow_run_id):
                return
            plan = ActionPlanRepository(session).get(
                checkpoint.action_plan_id,
                checkpoint.action_plan_version,
            )
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
        draft: EmployeeLifecycleRequestDraft,
        context: EmployeeLifecycleContext,
    ) -> None:
        workflow = workflows.get(workflow_run_id)
        decision = self._policy_engine.evaluate(
            draft,
            context,
            today=self._today_provider(),
        )
        payload: dict[str, object] = {
            "request_draft": draft.model_dump(mode="json"),
            "policy_decision": decision.model_dump(mode="json"),
        }
        if decision.outcome is PolicyOutcome.NEEDS_INPUT:
            missing = self._required_fields(draft, decision)
            payload["missing_fields"] = list(missing)
            target = WorkflowState.WAITING_USER
            event_type = "EMPLOYEE_LIFECYCLE_INFORMATION_REQUIRED"
        elif decision.outcome is PolicyOutcome.APPROVAL_REQUIRED:
            plan = self._plan_builder.build(
                workflow_run_id=workflow_run_id,
                draft=draft,
                context=context,
            )
            ActionPlanRepository(session).add(
                workflow_run_id,
                plan,
                status=ActionPlanStatus.PENDING_APPROVAL,
            )
            approval = ApprovalRepository(session).create_for_plan(
                workflow_run_id,
                plan,
                decision.approver_id or "",
            )
            payload.update(
                {
                    "missing_fields": [],
                    "action_plan_id": plan.plan_id,
                    "action_plan_version": plan.version,
                    "action_plan_digest": plan.content_digest,
                    "approval_id": approval.id,
                }
            )
            target = WorkflowState.WAITING_APPROVAL
            event_type = "EMPLOYEE_LIFECYCLE_PLAN_APPROVAL_REQUIRED"
        elif decision.outcome is PolicyOutcome.HUMAN_REVIEW:
            payload["missing_fields"] = []
            target = WorkflowState.WAITING_HUMAN
            event_type = "EMPLOYEE_LIFECYCLE_HUMAN_REVIEW_REQUIRED"
        elif decision.outcome is PolicyOutcome.NO_ACTION:
            payload["missing_fields"] = []
            target = WorkflowState.COMPLETED
            event_type = "EMPLOYEE_LIFECYCLE_NO_ACTION"
        else:
            payload["missing_fields"] = []
            target = WorkflowState.COMPLETED
            event_type = "EMPLOYEE_LIFECYCLE_REQUEST_DENIED"
        workflows.transition(
            workflow_run_id,
            expected_version=workflow.version,
            target=target,
            event_type=event_type,
            payload=payload,
        )

    @staticmethod
    def _current_draft(
        session: Session,
        workflow_run_id: str,
    ) -> EmployeeLifecycleRequestDraft:
        events = session.scalars(
            select(WorkflowEvent)
            .where(WorkflowEvent.run_id == workflow_run_id)
            .order_by(WorkflowEvent.sequence.desc())
        )
        for event in events:
            payload = event.payload or {}
            if "request_draft" in payload:
                return EmployeeLifecycleRequestDraft.model_validate(
                    payload["request_draft"]
                )
        raise ValueError("lifecycle workflow has no persisted request draft")

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
            payload = event.payload or {}
            if "missing_fields" in payload:
                return tuple(str(field) for field in payload["missing_fields"])
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
            payload = event.payload or {}
            if "policy_decision" in payload:
                return dict(payload)
        raise ValueError("lifecycle workflow has no policy decision")

    @staticmethod
    def _required_fields(
        draft: EmployeeLifecycleRequestDraft,
        decision: PolicyDecision,
    ) -> tuple[str, ...]:
        fields = list(draft.missing_fields())
        by_reason = {
            "VAGUE_BUSINESS_REASON": "business_reason",
            "SUBJECT_EMPLOYEE_NOT_RESOLVED": "subject_employee_id",
            "TARGET_DEPARTMENT_NOT_RESOLVED": "target_department_code",
            "TARGET_JOB_NOT_RESOLVED": "target_job_code",
            "TARGET_JOB_DEPARTMENT_MISMATCH": "target_job_code",
            "TARGET_MANAGER_NOT_RESOLVED": "target_manager_id",
            "TARGET_MANAGER_OUTSIDE_DEPARTMENT": "target_manager_id",
            "WORK_LOCATION_NOT_RESOLVED": "work_location_code",
        }
        fields.extend(
            by_reason[reason]
            for reason in decision.reason_codes
            if reason in by_reason
        )
        return tuple(dict.fromkeys(fields))

    @staticmethod
    def _require_owned(
        draft: EmployeeLifecycleRequestDraft,
        *,
        actor_id: str,
    ) -> EmployeeLifecycleRequestDraft:
        if draft.initiator_id != actor_id:
            raise EmployeeLifecycleActorMismatchError(actor_id)
        return draft

    @staticmethod
    def _optional_str(value: object) -> str | None:
        return value if isinstance(value, str) and value else None
