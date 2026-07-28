"""Domain-neutral LangGraph checkpoint around a human approval decision."""

from __future__ import annotations

from typing import TypedDict

from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command, interrupt
from pydantic import BaseModel, ConfigDict, model_validator
from sqlalchemy.orm import Session, sessionmaker

from app.actions.plans import ActionPlanRepository
from app.approval.contracts import ApprovalDecisionType, ApprovalStatus
from app.approval.repository import ApprovalConflictError, ApprovalRepository
from app.approval.service import ApprovalWorkflowService
from app.workflow.repository import WorkflowRepository
from app.workflow.state import WorkflowState


class ApprovalCheckpointState(TypedDict, total=False):
    workflow_run_id: str
    workflow_version: int
    approval_id: str
    action_id: str
    action_version: int
    action_plan_id: str
    action_plan_version: int
    approval_status: str


class ApprovalCheckpointInterrupt(BaseModel):
    model_config = ConfigDict(frozen=True)

    kind: str = "approval_required"
    workflow_run_id: str
    workflow_version: int
    approval_id: str
    action_id: str | None = None
    action_version: int | None = None
    action_plan_id: str | None = None
    action_plan_version: int | None = None

    @model_validator(mode="after")
    def require_exactly_one_subject(self) -> "ApprovalCheckpointInterrupt":
        action = self.action_id is not None and self.action_version is not None
        plan = (
            self.action_plan_id is not None
            and self.action_plan_version is not None
        )
        if action == plan:
            raise ValueError("checkpoint must bind exactly one approval subject")
        return self


class ApprovalCheckpointResume(BaseModel):
    model_config = ConfigDict(frozen=True)

    approval_status: ApprovalStatus
    workflow_version: int


class ApprovalCheckpointSnapshot(BaseModel):
    model_config = ConfigDict(frozen=True)

    workflow_run_id: str
    workflow_version: int
    approval_id: str
    action_id: str | None = None
    action_version: int | None = None
    action_plan_id: str | None = None
    action_plan_version: int | None = None
    approval_status: ApprovalStatus
    checkpoint_pending: bool
    next_nodes: tuple[str, ...] = ()

    @model_validator(mode="after")
    def require_exactly_one_subject(self) -> "ApprovalCheckpointSnapshot":
        action = self.action_id is not None and self.action_version is not None
        plan = (
            self.action_plan_id is not None
            and self.action_plan_version is not None
        )
        if action == plan:
            raise ValueError("checkpoint must bind exactly one approval subject")
        return self


class ApprovalCheckpointError(RuntimeError):
    """Raised when checkpoint position and authoritative business facts disagree."""


class ApprovalCheckpointCoordinator:
    """Persist and recover the human approval pause without domain write logic."""

    AWAIT_APPROVAL_NODE = "await_approval"

    def __init__(
        self,
        session_factory: sessionmaker[Session],
        checkpointer: BaseCheckpointSaver,
    ) -> None:
        self._session_factory = session_factory
        self._graph = self._build_graph(checkpointer)

    def arm(
        self,
        *,
        workflow_run_id: str,
        workflow_version: int,
        approval_id: str,
        action_id: str,
        action_version: int,
    ) -> ApprovalCheckpointSnapshot:
        """Create the recoverable pause, or return the matching existing pause."""
        return self._arm(
            workflow_run_id=workflow_run_id,
            workflow_version=workflow_version,
            approval_id=approval_id,
            action_id=action_id,
            action_version=action_version,
        )

    def arm_plan(
        self,
        *,
        workflow_run_id: str,
        workflow_version: int,
        approval_id: str,
        action_plan_id: str,
        action_plan_version: int,
    ) -> ApprovalCheckpointSnapshot:
        """Create a recoverable pause bound to one immutable plan version."""
        return self._arm(
            workflow_run_id=workflow_run_id,
            workflow_version=workflow_version,
            approval_id=approval_id,
            action_plan_id=action_plan_id,
            action_plan_version=action_plan_version,
        )

    def _arm(
        self,
        *,
        workflow_run_id: str,
        workflow_version: int,
        approval_id: str,
        action_id: str | None = None,
        action_version: int | None = None,
        action_plan_id: str | None = None,
        action_plan_version: int | None = None,
    ) -> ApprovalCheckpointSnapshot:
        subject = ApprovalCheckpointInterrupt(
            workflow_run_id=workflow_run_id,
            workflow_version=workflow_version,
            approval_id=approval_id,
            action_id=action_id,
            action_version=action_version,
            action_plan_id=action_plan_id,
            action_plan_version=action_plan_version,
        )
        existing = self._graph.get_state(self._config(workflow_run_id))
        if existing.values:
            snapshot = self.snapshot(workflow_run_id)
            if (
                snapshot.approval_id != approval_id
                or snapshot.action_id != subject.action_id
                or snapshot.action_version != subject.action_version
                or snapshot.action_plan_id != subject.action_plan_id
                or snapshot.action_plan_version != subject.action_plan_version
            ):
                raise ApprovalCheckpointError(
                    "checkpoint belongs to another approval subject"
                )
            return snapshot
        self._graph.invoke(
            {
                "workflow_run_id": workflow_run_id,
                "workflow_version": workflow_version,
                "approval_id": approval_id,
                "action_id": subject.action_id,
                "action_version": subject.action_version,
                "action_plan_id": subject.action_plan_id,
                "action_plan_version": subject.action_plan_version,
                "approval_status": ApprovalStatus.PENDING.value,
            },
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
    ) -> ApprovalCheckpointSnapshot:
        current = self.snapshot(workflow_run_id)
        if not current.checkpoint_pending:
            return self._require_matching_final_decision(
                current,
                actor_id=actor_id,
                decision=decision,
            )
        if current.next_nodes != (self.AWAIT_APPROVAL_NODE,):
            raise ApprovalCheckpointError("workflow is not waiting for approval")
        if current.approval_id != approval_id:
            raise ApprovalCheckpointError("approval does not match checkpoint")

        with self._session_factory.begin() as session:
            approvals = ApprovalRepository(session)
            task = approvals.get(approval_id)
            if task.approval_status is ApprovalStatus.PENDING:
                result = ApprovalWorkflowService(
                    approvals,
                    WorkflowRepository(session),
                    ActionPlanRepository(session),
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
            ApprovalCheckpointResume(
                approval_status=status,
                workflow_version=workflow_version,
            ),
        )
        return self.snapshot(workflow_run_id)

    def resume_recorded_decision(
        self,
        *,
        workflow_run_id: str,
        approval_id: str,
    ) -> ApprovalCheckpointSnapshot:
        """Close the crash window after the SQL decision committed before resume."""
        current = self.snapshot(workflow_run_id)
        if not current.checkpoint_pending:
            return current
        if current.approval_id != approval_id:
            raise ApprovalCheckpointError("approval does not match checkpoint")
        with self._session_factory() as session:
            task = ApprovalRepository(session).get(approval_id)
            if task.approval_status is ApprovalStatus.PENDING:
                raise ApprovalCheckpointError("approval still has no final decision")
            workflow = WorkflowRepository(session).get(workflow_run_id)
            expected_state = (
                WorkflowState.RUNNING
                if task.approval_status is ApprovalStatus.APPROVED
                else WorkflowState.COMPLETED
            )
            if workflow.workflow_state is not expected_state:
                raise ApprovalCheckpointError(
                    "approval and workflow state are inconsistent"
                )
            payload = ApprovalCheckpointResume(
                approval_status=task.approval_status,
                workflow_version=workflow.version,
            )
        self._resume_graph(workflow_run_id, payload)
        return self.snapshot(workflow_run_id)

    def snapshot(self, workflow_run_id: str) -> ApprovalCheckpointSnapshot:
        graph_snapshot = self._graph.get_state(self._config(workflow_run_id))
        values = graph_snapshot.values
        if values.get("workflow_run_id") != workflow_run_id:
            raise ApprovalCheckpointError(
                f"no approval checkpoint exists for workflow {workflow_run_id}"
            )
        approval_id = str(values["approval_id"])
        with self._session_factory() as session:
            task = ApprovalRepository(session).get(approval_id)
            workflow = WorkflowRepository(session).get(workflow_run_id)
        return ApprovalCheckpointSnapshot(
            workflow_run_id=workflow_run_id,
            workflow_version=workflow.version,
            approval_id=approval_id,
            action_id=(
                str(values["action_id"])
                if values.get("action_id") is not None
                else None
            ),
            action_version=(
                int(values["action_version"])
                if values.get("action_version") is not None
                else None
            ),
            action_plan_id=(
                str(values["action_plan_id"])
                if values.get("action_plan_id") is not None
                else None
            ),
            action_plan_version=(
                int(values["action_plan_version"])
                if values.get("action_plan_version") is not None
                else None
            ),
            approval_status=task.approval_status,
            checkpoint_pending=bool(graph_snapshot.next),
            next_nodes=tuple(graph_snapshot.next),
        )

    def _build_graph(self, checkpointer: BaseCheckpointSaver):
        builder = StateGraph(ApprovalCheckpointState)
        builder.add_node(self.AWAIT_APPROVAL_NODE, self._await_approval)
        builder.add_edge(START, self.AWAIT_APPROVAL_NODE)
        builder.add_edge(self.AWAIT_APPROVAL_NODE, END)
        return builder.compile(
            checkpointer=checkpointer,
            name="domain-neutral-approval-checkpoint",
        )

    @staticmethod
    def _await_approval(
        state: ApprovalCheckpointState,
    ) -> ApprovalCheckpointState:
        resumed = interrupt(
            ApprovalCheckpointInterrupt(
                workflow_run_id=state["workflow_run_id"],
                workflow_version=state["workflow_version"],
                approval_id=state["approval_id"],
                action_id=state.get("action_id"),
                action_version=state.get("action_version"),
                action_plan_id=state.get("action_plan_id"),
                action_plan_version=state.get("action_plan_version"),
            ).model_dump(mode="json")
        )
        payload = ApprovalCheckpointResume.model_validate(resumed)
        return {
            "approval_status": payload.approval_status.value,
            "workflow_version": payload.workflow_version,
        }

    def _resume_graph(
        self,
        workflow_run_id: str,
        payload: ApprovalCheckpointResume,
    ) -> None:
        self._graph.invoke(
            Command(resume=payload.model_dump(mode="json")),
            config=self._config(workflow_run_id),
        )

    def _require_matching_final_decision(
        self,
        current: ApprovalCheckpointSnapshot,
        *,
        actor_id: str,
        decision: ApprovalDecisionType,
    ) -> ApprovalCheckpointSnapshot:
        with self._session_factory() as session:
            self._validate_recorded_decision(
                session,
                workflow_run_id=current.workflow_run_id,
                approval_id=current.approval_id,
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
        workflow = WorkflowRepository(session).get(workflow_run_id)
        return task.approval_status, workflow.version

    @staticmethod
    def _config(workflow_run_id: str) -> dict[str, dict[str, str]]:
        return {"configurable": {"thread_id": workflow_run_id}}
