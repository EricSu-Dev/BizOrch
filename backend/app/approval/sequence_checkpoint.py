"""LangGraph checkpoint coordination for fixed serial approval sequences."""

from __future__ import annotations

from typing import TypedDict

from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command, interrupt
from pydantic import BaseModel, ConfigDict
from sqlalchemy.orm import Session, sessionmaker

from app.approval.contracts import (
    ApprovalDecisionType,
    ApprovalSequenceStatus,
    ApprovalStatus,
)
from app.approval.repository import ApprovalConflictError, ApprovalRepository
from app.approval.sequences import ApprovalSequenceRepository
from app.workflow.repository import WorkflowRepository
from app.workflow.state import WorkflowState


class ApprovalSequenceCheckpointState(TypedDict, total=False):
    workflow_run_id: str
    workflow_version: int
    approval_sequence_id: str
    action_plan_id: str
    action_plan_version: int
    approval_status: str


class ApprovalSequenceCheckpointInterrupt(BaseModel):
    model_config = ConfigDict(frozen=True)

    kind: str = "approval_sequence_required"
    workflow_run_id: str
    workflow_version: int
    approval_sequence_id: str
    action_plan_id: str
    action_plan_version: int


class ApprovalSequenceCheckpointResume(BaseModel):
    model_config = ConfigDict(frozen=True)

    approval_status: ApprovalStatus
    workflow_version: int


class ApprovalSequenceCheckpointSnapshot(BaseModel):
    model_config = ConfigDict(frozen=True)

    workflow_run_id: str
    workflow_version: int
    approval_sequence_id: str
    sequence_status: ApprovalSequenceStatus
    approval_id: str
    approval_status: ApprovalStatus
    action_plan_id: str
    action_plan_version: int
    checkpoint_pending: bool
    next_nodes: tuple[str, ...] = ()


class ApprovalSequenceCheckpointError(RuntimeError):
    """Raised when sequence, workflow and checkpoint facts disagree."""


class ApprovalSequenceCheckpointCoordinator:
    """Keep one graph interrupted until the whole serial route is final."""

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
        approval_sequence_id: str,
        action_plan_id: str,
        action_plan_version: int,
    ) -> ApprovalSequenceCheckpointSnapshot:
        """Create one durable pause bound to an exact plan and route."""
        with self._session_factory() as session:
            sequence, plan, _ = ApprovalSequenceRepository(
                session
            ).require_integrity(approval_sequence_id)
            workflow = WorkflowRepository(session).get(workflow_run_id)
            if (
                sequence.workflow_run_id != workflow_run_id
                or plan.plan_id != action_plan_id
                or plan.version != action_plan_version
                or workflow.version != workflow_version
                or workflow.workflow_state is not WorkflowState.WAITING_APPROVAL
            ):
                raise ApprovalSequenceCheckpointError(
                    "sequence checkpoint subject does not match workflow"
                )
        existing = self._graph.get_state(self._config(workflow_run_id))
        if existing.values:
            snapshot = self.snapshot(workflow_run_id)
            if (
                snapshot.approval_sequence_id != approval_sequence_id
                or snapshot.action_plan_id != action_plan_id
                or snapshot.action_plan_version != action_plan_version
            ):
                raise ApprovalSequenceCheckpointError(
                    "checkpoint belongs to another approval sequence"
                )
            return snapshot
        self._graph.invoke(
            {
                "workflow_run_id": workflow_run_id,
                "workflow_version": workflow_version,
                "approval_sequence_id": approval_sequence_id,
                "action_plan_id": action_plan_id,
                "action_plan_version": action_plan_version,
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
    ) -> ApprovalSequenceCheckpointSnapshot:
        """Decide one stage; resume the graph only after a final route result."""
        current = self.snapshot(workflow_run_id)
        if current.approval_id != approval_id:
            if self._recorded_decision_matches(
                approval_id,
                workflow_run_id=workflow_run_id,
                actor_id=actor_id,
                decision=decision,
            ):
                return current
            raise ApprovalSequenceCheckpointError(
                "approval does not match the current sequence stage"
            )
        if not self._task_is_pending(approval_id):
            self._require_recorded_decision(
                approval_id,
                workflow_run_id=workflow_run_id,
                actor_id=actor_id,
                decision=decision,
            )
            if current.checkpoint_pending:
                return self.resume_recorded_decision(
                    workflow_run_id=workflow_run_id,
                    approval_sequence_id=current.approval_sequence_id,
                )
            return current
        if not current.checkpoint_pending:
            self._require_recorded_decision(
                approval_id,
                workflow_run_id=workflow_run_id,
                actor_id=actor_id,
                decision=decision,
            )
            return current
        if current.next_nodes != (self.AWAIT_APPROVAL_NODE,):
            raise ApprovalSequenceCheckpointError(
                "workflow is not waiting for approval sequence"
            )

        with self._session_factory.begin() as session:
            sequences = ApprovalSequenceRepository(session)
            sequence, _, _ = sequences.require_integrity(
                current.approval_sequence_id
            )
            if sequence.workflow_run_id != workflow_run_id:
                raise ApprovalSequenceCheckpointError(
                    "approval sequence belongs to another workflow"
                )
            workflow = WorkflowRepository(session).get(workflow_run_id)
            if (
                workflow.workflow_state is not WorkflowState.WAITING_APPROVAL
                or workflow.version != expected_workflow_version
            ):
                raise ApprovalSequenceCheckpointError(
                    "workflow version or state changed before approval"
                )
            result = sequences.decide(
                approval_id,
                actor_id=actor_id,
                decision=decision,
                comment=comment,
            )
            workflows = WorkflowRepository(session)
            if not result.sequence_complete:
                workflow = workflows.record_progress(
                    workflow_run_id,
                    expected_version=expected_workflow_version,
                    required_state=WorkflowState.WAITING_APPROVAL,
                    event_type="APPROVAL_SEQUENCE_STAGE_APPROVED",
                    payload={
                        "approval_sequence_id": result.sequence_id,
                        "approval_id": approval_id,
                        "decision": decision.value,
                        "decided_by": actor_id,
                        "next_approval_id": result.next_approval_id,
                    },
                )
            else:
                approved = (
                    result.sequence_status is ApprovalSequenceStatus.APPROVED
                )
                workflow = workflows.transition(
                    workflow_run_id,
                    expected_version=expected_workflow_version,
                    target=(
                        WorkflowState.RUNNING
                        if approved
                        else WorkflowState.COMPLETED
                    ),
                    event_type=(
                        "APPROVAL_SEQUENCE_APPROVED"
                        if approved
                        else "APPROVAL_SEQUENCE_REJECTED"
                    ),
                    payload={
                        "approval_sequence_id": result.sequence_id,
                        "approval_id": approval_id,
                        "decision": decision.value,
                        "decided_by": actor_id,
                    },
                )
            workflow_version = workflow.version
            approval_status = result.approval_status
            sequence_complete = result.sequence_complete

        if sequence_complete:
            self._resume_graph(
                workflow_run_id,
                ApprovalSequenceCheckpointResume(
                    approval_status=approval_status,
                    workflow_version=workflow_version,
                ),
            )
        return self.snapshot(workflow_run_id)

    def resume_recorded_decision(
        self,
        *,
        workflow_run_id: str,
        approval_sequence_id: str,
    ) -> ApprovalSequenceCheckpointSnapshot:
        """Repair a final SQL commit that happened before graph resumption."""
        current = self.snapshot(workflow_run_id)
        if current.approval_sequence_id != approval_sequence_id:
            raise ApprovalSequenceCheckpointError(
                "approval sequence does not match checkpoint"
            )
        if not current.checkpoint_pending:
            return current
        with self._session_factory() as session:
            sequence, _, _ = ApprovalSequenceRepository(
                session
            ).require_integrity(approval_sequence_id)
            workflow = WorkflowRepository(session).get(workflow_run_id)
            if sequence.sequence_status is ApprovalSequenceStatus.PENDING:
                return current
            expected_state = (
                WorkflowState.RUNNING
                if sequence.sequence_status is ApprovalSequenceStatus.APPROVED
                else WorkflowState.COMPLETED
            )
            if workflow.workflow_state is not expected_state:
                raise ApprovalSequenceCheckpointError(
                    "approval sequence and workflow state are inconsistent"
                )
            final_task = self._display_task(sequence)
            payload = ApprovalSequenceCheckpointResume(
                approval_status=final_task.approval_status,
                workflow_version=workflow.version,
            )
        self._resume_graph(workflow_run_id, payload)
        return self.snapshot(workflow_run_id)

    def snapshot(
        self,
        workflow_run_id: str,
    ) -> ApprovalSequenceCheckpointSnapshot:
        graph_snapshot = self._graph.get_state(self._config(workflow_run_id))
        values = graph_snapshot.values
        if values.get("workflow_run_id") != workflow_run_id:
            raise ApprovalSequenceCheckpointError(
                f"no sequence checkpoint exists for workflow {workflow_run_id}"
            )
        sequence_id = str(values["approval_sequence_id"])
        with self._session_factory() as session:
            sequence = ApprovalSequenceRepository(session).get(sequence_id)
            workflow = WorkflowRepository(session).get(workflow_run_id)
            task = self._display_task(sequence)
            return ApprovalSequenceCheckpointSnapshot(
                workflow_run_id=workflow_run_id,
                workflow_version=workflow.version,
                approval_sequence_id=sequence_id,
                sequence_status=sequence.sequence_status,
                approval_id=task.id,
                approval_status=task.approval_status,
                action_plan_id=str(values["action_plan_id"]),
                action_plan_version=int(values["action_plan_version"]),
                checkpoint_pending=bool(graph_snapshot.next),
                next_nodes=tuple(graph_snapshot.next),
            )

    def _build_graph(self, checkpointer: BaseCheckpointSaver):
        builder = StateGraph(ApprovalSequenceCheckpointState)
        builder.add_node(self.AWAIT_APPROVAL_NODE, self._await_approval)
        builder.add_edge(START, self.AWAIT_APPROVAL_NODE)
        builder.add_edge(self.AWAIT_APPROVAL_NODE, END)
        return builder.compile(
            checkpointer=checkpointer,
            name="domain-neutral-approval-sequence-checkpoint",
        )

    @staticmethod
    def _await_approval(
        state: ApprovalSequenceCheckpointState,
    ) -> ApprovalSequenceCheckpointState:
        resumed = interrupt(
            ApprovalSequenceCheckpointInterrupt(
                workflow_run_id=state["workflow_run_id"],
                workflow_version=state["workflow_version"],
                approval_sequence_id=state["approval_sequence_id"],
                action_plan_id=state["action_plan_id"],
                action_plan_version=state["action_plan_version"],
            ).model_dump(mode="json")
        )
        payload = ApprovalSequenceCheckpointResume.model_validate(resumed)
        return {
            "approval_status": payload.approval_status.value,
            "workflow_version": payload.workflow_version,
        }

    def _resume_graph(
        self,
        workflow_run_id: str,
        payload: ApprovalSequenceCheckpointResume,
    ) -> None:
        self._graph.invoke(
            Command(resume=payload.model_dump(mode="json")),
            config=self._config(workflow_run_id),
        )

    def _recorded_decision_matches(
        self,
        approval_id: str,
        *,
        workflow_run_id: str,
        actor_id: str,
        decision: ApprovalDecisionType,
    ) -> bool:
        try:
            self._require_recorded_decision(
                approval_id,
                workflow_run_id=workflow_run_id,
                actor_id=actor_id,
                decision=decision,
            )
        except ApprovalConflictError:
            return False
        return True

    def _task_is_pending(self, approval_id: str) -> bool:
        with self._session_factory() as session:
            return (
                ApprovalRepository(session).get(approval_id).approval_status
                is ApprovalStatus.PENDING
            )

    def _require_recorded_decision(
        self,
        approval_id: str,
        *,
        workflow_run_id: str,
        actor_id: str,
        decision: ApprovalDecisionType,
    ) -> None:
        with self._session_factory() as session:
            task = ApprovalRepository(session).get(approval_id)
            recorded = task.decision
            if (
                task.workflow_run_id != workflow_run_id
                or recorded is None
                or recorded.decided_by != actor_id
                or recorded.decision != decision.value
            ):
                raise ApprovalConflictError(approval_id)

    @staticmethod
    def _display_task(sequence):
        if sequence.sequence_status is ApprovalSequenceStatus.PENDING:
            task = next(
                (
                    item
                    for item in sequence.stages
                    if item.approval_status is ApprovalStatus.PENDING
                ),
                None,
            )
        elif sequence.sequence_status is ApprovalSequenceStatus.REJECTED:
            task = next(
                (
                    item
                    for item in sequence.stages
                    if item.approval_status is ApprovalStatus.REJECTED
                ),
                None,
            )
        else:
            task = max(
                sequence.stages,
                key=lambda item: item.stage_order or 0,
                default=None,
            )
        if task is None:
            raise ApprovalSequenceCheckpointError(
                "approval sequence has no displayable stage"
            )
        return task

    @staticmethod
    def _config(workflow_run_id: str) -> dict[str, dict[str, str]]:
        return {"configurable": {"thread_id": workflow_run_id}}
