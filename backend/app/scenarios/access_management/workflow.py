"""Recoverable LangGraph orchestration for the access-request scenario."""

from __future__ import annotations

from typing import TypedDict
from uuid import uuid4

from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command, interrupt
from pydantic import BaseModel, ConfigDict
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from app.actions.repository import ActionProposalRepository
from app.approval.contracts import (
    ApprovalDecisionType,
    ApprovalStatus,
)
from app.approval.repository import ApprovalConflictError, ApprovalRepository
from app.approval.service import ApprovalWorkflowService
from app.scenarios.access_management.contracts import (
    AccessRequestContext,
    AccessRequestDraft,
)
from app.scenarios.access_management.execution import AccessRequestExecutionService
from app.scenarios.access_management.service import (
    AccessRequestIntakeResult,
    AccessRequestIntakeService,
)
from app.workflow.models import WorkflowEvent
from app.workflow.repository import (
    WorkflowRepository,
    WorkflowVersionConflictError,
)
from app.workflow.state import WorkflowState


class AccessRequestGraphState(TypedDict, total=False):
    """JSON-serializable state persisted by the scenario graph."""

    workflow_run_id: str
    workflow_state: str
    workflow_version: int
    draft: dict[str, object]
    context: dict[str, object]
    action_id: str
    action_version: int
    approval_id: str
    approval_status: str
    policy_outcome: str
    execution_outcome: str
    missing_fields: list[str]


class ApprovalInterruptPayload(BaseModel):
    """Safe information exposed when the graph pauses for a human decision."""

    model_config = ConfigDict(frozen=True)

    kind: str = "approval_required"
    workflow_run_id: str
    workflow_version: int
    approval_id: str
    action_id: str
    action_version: int


class ApprovalResumePayload(BaseModel):
    """Validated data injected after the authoritative decision is committed."""

    model_config = ConfigDict(frozen=True)

    approval_status: ApprovalStatus
    workflow_version: int


class UserInputInterruptPayload(BaseModel):
    """Safe field requirements exposed while waiting for user information."""

    model_config = ConfigDict(frozen=True)

    kind: str = "user_input_required"
    workflow_run_id: str
    workflow_version: int
    missing_fields: tuple[str, ...]


class UserInputResumePayload(BaseModel):
    """Authoritative intake result injected after user information is committed."""

    model_config = ConfigDict(frozen=True)

    draft: dict[str, object]
    context: dict[str, object]
    provided_fields: tuple[str, ...]
    workflow_state: WorkflowState
    workflow_version: int
    policy_outcome: str
    missing_fields: tuple[str, ...]
    action_id: str | None = None
    action_version: int | None = None
    approval_id: str | None = None


class AccessWorkflowSnapshot(BaseModel):
    """Stable query result combining business state and checkpoint position."""

    model_config = ConfigDict(frozen=True)

    workflow_run_id: str
    service_request_id: str | None = None
    ticket_id: str | None = None
    workflow_state: WorkflowState
    workflow_version: int
    checkpoint_pending: bool
    next_nodes: tuple[str, ...]
    action_id: str | None = None
    action_version: int | None = None
    approval_id: str | None = None
    approval_status: ApprovalStatus | None = None
    execution_outcome: str | None = None
    missing_fields: tuple[str, ...] = ()


class AccessWorkflowResumeError(RuntimeError):
    """Raised when a caller tries to resume a graph at the wrong checkpoint."""


class AccessRequestWorkflow:
    """Coordinate intake, approval interruption and controlled execution."""

    AWAIT_APPROVAL_NODE = "await_approval"
    AWAIT_USER_NODE = "await_user_input"

    def __init__(
        self,
        session_factory: sessionmaker[Session],
        execution_service: AccessRequestExecutionService,
        checkpointer: BaseCheckpointSaver,
        *,
        action_executor_id: str = "system-action-executor",
    ) -> None:
        self._session_factory = session_factory
        self._execution_service = execution_service
        self._action_executor_id = action_executor_id
        self._graph = self._build_graph(checkpointer)

    def start(
        self,
        draft: AccessRequestDraft,
        context: AccessRequestContext,
        *,
        run_id: str | None = None,
        action_id: str | None = None,
        approval_id: str | None = None,
    ) -> AccessWorkflowSnapshot:
        """Create and run a graph until it finishes or reaches an interrupt."""
        resolved_run_id = run_id or str(uuid4())
        self._graph.invoke(
            {
                "workflow_run_id": resolved_run_id,
                "draft": draft.model_dump(mode="json"),
                "context": context.model_dump(mode="json"),
                "action_id": action_id or str(uuid4()),
                "action_version": 1,
                "approval_id": approval_id or str(uuid4()),
            },
            config=self._config(resolved_run_id),
        )
        return self.snapshot(resolved_run_id)

    def provide_information_and_resume(
        self,
        *,
        workflow_run_id: str,
        expected_workflow_version: int,
        updates: AccessRequestDraft,
        context: AccessRequestContext | None = None,
    ) -> AccessWorkflowSnapshot:
        """Commit missing fields and resume the exact waiting-user checkpoint."""
        current = self.snapshot(workflow_run_id)
        if self.AWAIT_USER_NODE not in current.next_nodes:
            raise AccessWorkflowResumeError(
                f"workflow {workflow_run_id} is not waiting for user input"
            )

        graph_snapshot = self._graph.get_state(self._config(workflow_run_id))
        current_draft = AccessRequestDraft.model_validate(
            graph_snapshot.values["draft"]
        )
        update_values = updates.model_dump(exclude_unset=True, mode="json")
        if not update_values or any(value is None for value in update_values.values()):
            raise ValueError("at least one non-null missing field must be provided")
        allowed_fields = set(current_draft.missing_fields())
        unexpected = set(update_values) - allowed_fields
        if unexpected:
            raise AccessWorkflowResumeError(
                "only currently missing fields can be supplemented: "
                + ", ".join(sorted(unexpected))
            )

        merged_values = current_draft.model_dump(mode="json")
        merged_values.update(update_values)
        merged_draft = AccessRequestDraft.model_validate(merged_values)
        trusted_context = context or AccessRequestContext.model_validate(
            graph_snapshot.values["context"]
        )
        provided_fields = tuple(update_values)

        with self._session_factory.begin() as session:
            workflows = WorkflowRepository(session)
            workflow_run = workflows.get(workflow_run_id)
            if (
                workflow_run.workflow_state is WorkflowState.WAITING_USER
                and workflow_run.version == expected_workflow_version
            ):
                result = AccessRequestIntakeService(
                    workflows,
                    ActionProposalRepository(session),
                    ApprovalRepository(session),
                ).resume_with_information(
                    merged_draft,
                    trusted_context,
                    run_id=workflow_run_id,
                    expected_version=expected_workflow_version,
                    provided_fields=provided_fields,
                    action_id=str(uuid4()),
                    approval_id=str(uuid4()),
                )
                payload = self._user_resume_payload(
                    result,
                    merged_draft,
                    trusted_context,
                    provided_fields,
                )
            else:
                payload = self._recover_committed_user_input(
                    session,
                    workflow_run_id=workflow_run_id,
                    expected_workflow_version=expected_workflow_version,
                    submitted_updates=update_values,
                    context=trusted_context,
                )

        self._graph.invoke(
            Command(resume=payload.model_dump(mode="json")),
            config=self._config(workflow_run_id),
        )
        return self.snapshot(workflow_run_id)

    def decide_and_resume(
        self,
        *,
        workflow_run_id: str,
        approval_id: str,
        expected_workflow_version: int,
        actor_id: str,
        decision: ApprovalDecisionType,
        comment: str | None = None,
    ) -> AccessWorkflowSnapshot:
        """Commit one decision and resume the exact interrupted graph."""
        current = self.snapshot(workflow_run_id)
        if not current.checkpoint_pending:
            return self._require_matching_final_decision(
                current,
                approval_id=approval_id,
                actor_id=actor_id,
                decision=decision,
            )
        if self.AWAIT_APPROVAL_NODE not in current.next_nodes:
            raise AccessWorkflowResumeError(
                f"workflow {workflow_run_id} is not waiting for approval"
            )
        if current.approval_id != approval_id:
            raise AccessWorkflowResumeError("approval does not match checkpoint")

        with self._session_factory.begin() as session:
            approvals = ApprovalRepository(session)
            task = approvals.get(approval_id)
            if task.approval_status is ApprovalStatus.PENDING:
                result = ApprovalWorkflowService(
                    approvals,
                    WorkflowRepository(session),
                ).decide(
                    workflow_run_id=workflow_run_id,
                    expected_workflow_version=expected_workflow_version,
                    approval_id=approval_id,
                    actor_id=actor_id,
                    decision=decision,
                    comment=comment,
                )
                status = result.approval_status
                workflow_version = result.workflow_version
            else:
                status, workflow_version = self._validate_recorded_decision(
                    session,
                    workflow_run_id=workflow_run_id,
                    approval_id=approval_id,
                    actor_id=actor_id,
                    decision=decision,
                )

        self._resume_graph(
            workflow_run_id,
            ApprovalResumePayload(
                approval_status=status,
                workflow_version=workflow_version,
            ),
        )
        return self.snapshot(workflow_run_id)

    def resume_recorded_approval(
        self,
        *,
        workflow_run_id: str,
        approval_id: str,
    ) -> AccessWorkflowSnapshot:
        """Recover the crash window after MySQL commit but before graph resume."""
        current = self.snapshot(workflow_run_id)
        if not current.checkpoint_pending:
            return current
        if self.AWAIT_APPROVAL_NODE not in current.next_nodes:
            raise AccessWorkflowResumeError(
                f"workflow {workflow_run_id} is not waiting for approval"
            )
        if current.approval_id != approval_id:
            raise AccessWorkflowResumeError("approval does not match checkpoint")

        with self._session_factory() as session:
            task = ApprovalRepository(session).get(approval_id)
            if task.approval_status is ApprovalStatus.PENDING:
                raise AccessWorkflowResumeError("approval still has no final decision")
            workflow_run = WorkflowRepository(session).get(workflow_run_id)
            expected_state = (
                WorkflowState.RUNNING
                if task.approval_status is ApprovalStatus.APPROVED
                else WorkflowState.COMPLETED
            )
            if workflow_run.workflow_state is not expected_state:
                raise AccessWorkflowResumeError(
                    "approval and authoritative workflow state are inconsistent"
                )
            payload = ApprovalResumePayload(
                approval_status=task.approval_status,
                workflow_version=workflow_run.version,
            )
        self._resume_graph(workflow_run_id, payload)
        return self.snapshot(workflow_run_id)

    def snapshot(self, workflow_run_id: str) -> AccessWorkflowSnapshot:
        """Read the current business state and recoverable graph position."""
        with self._session_factory() as session:
            workflow_run = WorkflowRepository(session).get(workflow_run_id)
        graph_snapshot = self._graph.get_state(self._config(workflow_run_id))
        values = graph_snapshot.values
        if values.get("workflow_run_id") != workflow_run_id:
            raise AccessWorkflowResumeError(
                f"no checkpoint exists for workflow {workflow_run_id}"
            )
        with self._session_factory() as session:
            approval_status: ApprovalStatus | None = None
            approval_id = self._optional_str(values.get("approval_id"))
            if approval_id:
                approval_status = ApprovalRepository(session).get(
                    approval_id
                ).approval_status
        next_nodes = tuple(graph_snapshot.next)
        action_version = values.get("action_version")
        return AccessWorkflowSnapshot(
            workflow_run_id=workflow_run_id,
            workflow_state=workflow_run.workflow_state,
            workflow_version=workflow_run.version,
            checkpoint_pending=bool(next_nodes),
            next_nodes=next_nodes,
            action_id=self._optional_str(values.get("action_id")),
            action_version=(
                action_version
                if isinstance(action_version, int) and action_version >= 1
                else None
            ),
            approval_id=approval_id,
            approval_status=approval_status,
            execution_outcome=self._optional_str(values.get("execution_outcome")),
            missing_fields=tuple(values.get("missing_fields", ())),
        )

    def request_draft(self, workflow_run_id: str) -> AccessRequestDraft:
        """Return the structured draft stored in the recoverable graph state."""
        with self._session_factory() as session:
            WorkflowRepository(session).get(workflow_run_id)
        graph_snapshot = self._graph.get_state(self._config(workflow_run_id))
        values = graph_snapshot.values
        if values.get("workflow_run_id") != workflow_run_id:
            raise AccessWorkflowResumeError(
                f"no checkpoint exists for workflow {workflow_run_id}"
            )
        return AccessRequestDraft.model_validate(values["draft"])

    def _build_graph(self, checkpointer: BaseCheckpointSaver):
        builder = StateGraph(AccessRequestGraphState)
        builder.add_node("intake", self._intake)
        builder.add_node(self.AWAIT_USER_NODE, self._await_user_input)
        builder.add_node(self.AWAIT_APPROVAL_NODE, self._await_approval)
        builder.add_node("execute", self._execute)
        builder.add_edge(START, "intake")
        builder.add_conditional_edges(
            "intake",
            self._route_after_intake,
            {
                self.AWAIT_USER_NODE: self.AWAIT_USER_NODE,
                self.AWAIT_APPROVAL_NODE: self.AWAIT_APPROVAL_NODE,
                "end": END,
            },
        )
        builder.add_conditional_edges(
            self.AWAIT_USER_NODE,
            self._route_after_intake,
            {
                self.AWAIT_USER_NODE: self.AWAIT_USER_NODE,
                self.AWAIT_APPROVAL_NODE: self.AWAIT_APPROVAL_NODE,
                "end": END,
            },
        )
        builder.add_conditional_edges(
            self.AWAIT_APPROVAL_NODE,
            self._route_after_approval,
            {"execute": "execute", "end": END},
        )
        builder.add_edge("execute", END)
        return builder.compile(
            checkpointer=checkpointer,
            name="access-request-workflow",
        )

    def _intake(self, state: AccessRequestGraphState) -> AccessRequestGraphState:
        draft = AccessRequestDraft.model_validate(state["draft"])
        context = AccessRequestContext.model_validate(state["context"])
        with self._session_factory.begin() as session:
            result = AccessRequestIntakeService(
                WorkflowRepository(session),
                ActionProposalRepository(session),
                ApprovalRepository(session),
            ).start(
                draft,
                context,
                run_id=state["workflow_run_id"],
                action_id=state["action_id"],
                approval_id=state["approval_id"],
            )
        update: AccessRequestGraphState = {
            "workflow_state": result.workflow_state.value,
            "workflow_version": result.workflow_version,
            "policy_outcome": result.policy_decision.outcome.value,
            "missing_fields": list(draft.missing_fields()),
        }
        if result.action_proposal is None:
            # LangGraph merges node updates into existing state. Explicit empty
            # values clear the preallocated identifiers on non-action branches.
            update["action_id"] = ""
            update["action_version"] = 0
            update["approval_id"] = ""
        else:
            update["action_id"] = result.action_proposal.action_id
            update["action_version"] = result.action_proposal.version
            update["approval_id"] = result.approval_id or ""
        return update

    @staticmethod
    def _route_after_intake(state: AccessRequestGraphState) -> str:
        if state["workflow_state"] == WorkflowState.WAITING_USER.value:
            return AccessRequestWorkflow.AWAIT_USER_NODE
        if state["workflow_state"] == WorkflowState.WAITING_APPROVAL.value:
            return AccessRequestWorkflow.AWAIT_APPROVAL_NODE
        return "end"

    @staticmethod
    def _await_user_input(
        state: AccessRequestGraphState,
    ) -> AccessRequestGraphState:
        resumed = interrupt(
            UserInputInterruptPayload(
                workflow_run_id=state["workflow_run_id"],
                workflow_version=state["workflow_version"],
                missing_fields=tuple(state["missing_fields"]),
            ).model_dump(mode="json")
        )
        payload = UserInputResumePayload.model_validate(resumed)
        return {
            "draft": payload.draft,
            "context": payload.context,
            "workflow_state": payload.workflow_state.value,
            "workflow_version": payload.workflow_version,
            "policy_outcome": payload.policy_outcome,
            "missing_fields": list(payload.missing_fields),
            "action_id": payload.action_id or "",
            "action_version": payload.action_version or 0,
            "approval_id": payload.approval_id or "",
        }

    @staticmethod
    def _await_approval(
        state: AccessRequestGraphState,
    ) -> AccessRequestGraphState:
        resumed = interrupt(
            ApprovalInterruptPayload(
                workflow_run_id=state["workflow_run_id"],
                workflow_version=state["workflow_version"],
                approval_id=state["approval_id"],
                action_id=state["action_id"],
                action_version=state["action_version"],
            ).model_dump(mode="json")
        )
        payload = ApprovalResumePayload.model_validate(resumed)
        return {
            "approval_status": payload.approval_status.value,
            "workflow_state": (
                WorkflowState.RUNNING.value
                if payload.approval_status is ApprovalStatus.APPROVED
                else WorkflowState.COMPLETED.value
            ),
            "workflow_version": payload.workflow_version,
        }

    @staticmethod
    def _route_after_approval(state: AccessRequestGraphState) -> str:
        if state["approval_status"] == ApprovalStatus.APPROVED.value:
            return "execute"
        return "end"

    def _execute(self, state: AccessRequestGraphState) -> AccessRequestGraphState:
        result = self._execution_service.execute(
            workflow_run_id=state["workflow_run_id"],
            expected_workflow_version=state["workflow_version"],
            action_id=state["action_id"],
            action_version=state["action_version"],
            approval_id=state["approval_id"],
            actor_id=self._action_executor_id,
            idempotency_key=(
                f"grant:{state['action_id']}:v{state['action_version']}"
            ),
        )
        update: AccessRequestGraphState = {
            "workflow_state": result.workflow_state.value,
            "workflow_version": result.workflow_version,
        }
        if result.gateway_result is not None:
            update["execution_outcome"] = result.gateway_result.outcome.value
        elif result.already_satisfied:
            update["execution_outcome"] = "ALREADY_SATISFIED"
        elif result.human_review_reason:
            update["execution_outcome"] = "HUMAN_REVIEW"
        return update

    @staticmethod
    def _user_resume_payload(
        result: AccessRequestIntakeResult,
        draft: AccessRequestDraft,
        context: AccessRequestContext,
        provided_fields: tuple[str, ...],
    ) -> UserInputResumePayload:
        proposal = result.action_proposal
        return UserInputResumePayload(
            draft=draft.model_dump(mode="json"),
            context=context.model_dump(mode="json"),
            provided_fields=provided_fields,
            workflow_state=result.workflow_state,
            workflow_version=result.workflow_version,
            policy_outcome=result.policy_decision.outcome.value,
            missing_fields=draft.missing_fields(),
            action_id=proposal.action_id if proposal else None,
            action_version=proposal.version if proposal else None,
            approval_id=result.approval_id,
        )

    @staticmethod
    def _recover_committed_user_input(
        session: Session,
        *,
        workflow_run_id: str,
        expected_workflow_version: int,
        submitted_updates: dict[str, object],
        context: AccessRequestContext,
    ) -> UserInputResumePayload:
        """Rebuild a resume payload after MySQL advanced but the graph did not."""
        workflow_run = WorkflowRepository(session).get(workflow_run_id)
        expected_committed_version = expected_workflow_version + 2
        if workflow_run.version != expected_committed_version:
            raise WorkflowVersionConflictError(
                f"workflow {workflow_run_id} expected version "
                f"{expected_workflow_version}, found {workflow_run.version}"
            )

        received_event = session.scalar(
            select(WorkflowEvent).where(
                WorkflowEvent.run_id == workflow_run_id,
                WorkflowEvent.sequence == expected_workflow_version + 1,
                WorkflowEvent.event_type == "REQUEST_INFORMATION_RECEIVED",
            )
        )
        routed_event = session.scalar(
            select(WorkflowEvent).where(
                WorkflowEvent.run_id == workflow_run_id,
                WorkflowEvent.sequence == expected_committed_version,
            )
        )
        if received_event is None or routed_event is None:
            raise AccessWorkflowResumeError(
                "committed user-input events cannot be recovered"
            )
        recorded_draft = AccessRequestDraft.model_validate(
            received_event.payload.get("request_draft")
        )
        recorded_values = recorded_draft.model_dump(mode="json")
        if any(
            recorded_values.get(key) != value
            for key, value in submitted_updates.items()
        ):
            raise AccessWorkflowResumeError(
                "submitted fields do not match the already committed input"
            )
        policy_data = routed_event.payload.get("policy_decision")
        if not isinstance(policy_data, dict) or not isinstance(
            policy_data.get("outcome"), str
        ):
            raise AccessWorkflowResumeError("committed policy result is missing")

        action_id = routed_event.payload.get("action_id")
        action_version = routed_event.payload.get("action_version")
        approval_id = routed_event.payload.get("approval_id")
        provided = received_event.payload.get("provided_fields", ())
        return UserInputResumePayload(
            draft=recorded_draft.model_dump(mode="json"),
            context=context.model_dump(mode="json"),
            provided_fields=tuple(str(field) for field in provided),
            workflow_state=workflow_run.workflow_state,
            workflow_version=workflow_run.version,
            policy_outcome=policy_data["outcome"],
            missing_fields=recorded_draft.missing_fields(),
            action_id=action_id if isinstance(action_id, str) else None,
            action_version=(
                action_version if isinstance(action_version, int) else None
            ),
            approval_id=approval_id if isinstance(approval_id, str) else None,
        )

    def _resume_graph(
        self,
        workflow_run_id: str,
        payload: ApprovalResumePayload,
    ) -> None:
        self._graph.invoke(
            Command(resume=payload.model_dump(mode="json")),
            config=self._config(workflow_run_id),
        )

    def _require_matching_final_decision(
        self,
        current: AccessWorkflowSnapshot,
        *,
        approval_id: str,
        actor_id: str,
        decision: ApprovalDecisionType,
    ) -> AccessWorkflowSnapshot:
        with self._session_factory() as session:
            self._validate_recorded_decision(
                session,
                workflow_run_id=current.workflow_run_id,
                approval_id=approval_id,
                actor_id=actor_id,
                decision=decision,
            )
        return current

    @staticmethod
    def _validate_recorded_decision(
        session: Session,
        *,
        workflow_run_id: str,
        approval_id: str,
        actor_id: str,
        decision: ApprovalDecisionType,
    ) -> tuple[ApprovalStatus, int]:
        task = ApprovalRepository(session).get(approval_id)
        recorded = task.decision
        if task.workflow_run_id != workflow_run_id or recorded is None:
            raise ApprovalConflictError(approval_id)
        if recorded.decided_by != actor_id or recorded.decision != decision.value:
            raise ApprovalConflictError(approval_id)
        workflow_run = WorkflowRepository(session).get(workflow_run_id)
        return task.approval_status, workflow_run.version

    @staticmethod
    def _config(workflow_run_id: str) -> dict[str, dict[str, str]]:
        return {"configurable": {"thread_id": workflow_run_id}}

    @staticmethod
    def _optional_str(value: object) -> str | None:
        return value if isinstance(value, str) and value else None
