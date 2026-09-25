from __future__ import annotations

import pytest
from concurrent.futures import ThreadPoolExecutor
from threading import Event
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker

from app.actions.contracts import ActionGatewayOutcome, ActionGatewayResult
from app.actions.repository import ActionProposalRepository
from app.approval.contracts import ApprovalDecisionType, ApprovalStatus
from app.approval.repository import ApprovalRepository
from app.approval.service import ApprovalWorkflowService
from app.persistence.base import Base
from app.scenarios.access_management.contracts import (
    AccessRequestContext,
    AccessRequestDraft,
)
from app.scenarios.access_management.execution import AccessRequestExecutionService
from app.scenarios.access_management.service import AccessRequestIntakeService
from app.scenarios.access_management.workflow import (
    AccessRequestWorkflow,
    AccessWorkflowResumeError,
)
from app.workflow.checkpoint import SqliteCheckpointStore
from app.workflow.models import WorkflowEvent
from app.workflow.repository import WorkflowRepository
from app.workflow.state import WorkflowState


class SuccessfulGateway:
    def __init__(self) -> None:
        self.calls = 0

    def execute(
        self,
        proposal,
        *,
        actor_id: str,
        approval_id: str,
        idempotency_key: str,
    ) -> ActionGatewayResult:
        self.calls += 1
        return ActionGatewayResult(
            outcome=ActionGatewayOutcome.SUCCEEDED,
            action_id=proposal.action_id,
            action_version=proposal.version,
            idempotency_key=idempotency_key,
        )


class CrashingGateway:
    def __init__(self) -> None:
        self.calls = 0

    def execute(self, *args, **kwargs) -> ActionGatewayResult:
        self.calls += 1
        raise RuntimeError("process terminated during external execution")


class BlockingGateway(SuccessfulGateway):
    def __init__(self) -> None:
        super().__init__()
        self.entered = Event()
        self.release = Event()

    def execute(self, proposal, *, actor_id, approval_id, idempotency_key):
        self.entered.set()
        if not self.release.wait(timeout=5):
            raise TimeoutError("test gateway was not released")
        return super().execute(
            proposal,
            actor_id=actor_id,
            approval_id=approval_id,
            idempotency_key=idempotency_key,
        )


def build_session_factory(tmp_path) -> sessionmaker[Session]:
    engine = create_engine(f"sqlite+pysqlite:///{tmp_path / 'business.db'}")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, expire_on_commit=False)


def request_draft() -> AccessRequestDraft:
    return AccessRequestDraft(
        employee_id="EMP-1001",
        application_code="CRM",
        role_code="read_only",
        duration_days=30,
        business_reason="Participate in a customer project",
    )


def context() -> AccessRequestContext:
    return AccessRequestContext(manager_id="EMP-MANAGER")


def build_workflow(session_factory, checkpoint_path, gateway):
    store = SqliteCheckpointStore(checkpoint_path)
    execution = AccessRequestExecutionService(session_factory, gateway)
    return AccessRequestWorkflow(
        session_factory,
        execution,
        store.saver,
    ), store


def start(workflow: AccessRequestWorkflow):
    return workflow.start(
        request_draft(),
        context(),
        run_id="run-1",
        action_id="action-1",
        approval_id="approval-1",
    )


def test_graph_interrupts_at_approval_and_persists_checkpoint(tmp_path) -> None:
    sessions = build_session_factory(tmp_path)
    gateway = SuccessfulGateway()
    workflow, store = build_workflow(
        sessions, tmp_path / "checkpoints.db", gateway
    )
    try:
        snapshot = start(workflow)

        assert snapshot.workflow_state is WorkflowState.WAITING_APPROVAL
        assert snapshot.workflow_version == 2
        assert snapshot.checkpoint_pending
        assert snapshot.next_nodes == ("await_approval",)
        assert snapshot.approval_status is ApprovalStatus.PENDING
        assert gateway.calls == 0
    finally:
        store.close()


def test_interrupted_execute_checkpoint_routes_to_human_without_retry(tmp_path) -> None:
    sessions = build_session_factory(tmp_path)
    crashing = CrashingGateway()
    workflow, store = build_workflow(
        sessions, tmp_path / "checkpoints.db", crashing
    )
    try:
        waiting = start(workflow)
        with pytest.raises(RuntimeError, match="process terminated"):
            workflow.decide_and_resume(
                workflow_run_id="run-1",
                approval_id=waiting.approval_id or "",
                expected_workflow_version=waiting.workflow_version,
                actor_id="EMP-MANAGER",
                decision=ApprovalDecisionType.APPROVE,
            )
        interrupted = workflow.snapshot("run-1")
        assert interrupted.workflow_state is WorkflowState.EXECUTING
        assert interrupted.next_nodes == ("execute",)
        assert crashing.calls == 1
    finally:
        store.close()

    recovered, reopened_store = build_workflow(
        sessions, tmp_path / "checkpoints.db", SuccessfulGateway()
    )
    try:
        result = recovered.resume_recorded_approval(
            workflow_run_id="run-1",
            approval_id=waiting.approval_id or "",
        )
        assert result.workflow_state is WorkflowState.WAITING_HUMAN
        assert not result.checkpoint_pending
        assert result.execution_outcome == "HUMAN_REVIEW"
        with sessions() as session:
            events = list(
                session.scalars(
                    select(WorkflowEvent).where(WorkflowEvent.run_id == "run-1")
                )
            )
        assert events[-1].event_type == "ACTION_EXECUTION_INTERRUPTED_REQUIRES_HUMAN"
    finally:
        reopened_store.close()


def test_duplicate_approval_does_not_recover_a_live_execution(tmp_path) -> None:
    sessions = build_session_factory(tmp_path)
    gateway = BlockingGateway()
    workflow, store = build_workflow(
        sessions, tmp_path / "checkpoints.db", gateway
    )
    try:
        waiting = start(workflow)
        with ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(
                workflow.decide_and_resume,
                workflow_run_id="run-1",
                approval_id="approval-1",
                expected_workflow_version=waiting.workflow_version,
                actor_id="EMP-MANAGER",
                decision=ApprovalDecisionType.APPROVE,
            )
            assert gateway.entered.wait(timeout=5)
            try:
                executing = workflow.snapshot("run-1")
                assert executing.workflow_state is WorkflowState.EXECUTING
                with pytest.raises(AccessWorkflowResumeError):
                    workflow.decide_and_resume(
                        workflow_run_id="run-1",
                        approval_id="wrong-approval",
                        expected_workflow_version=waiting.workflow_version,
                        actor_id="OTHER-APPROVER",
                        decision=ApprovalDecisionType.APPROVE,
                    )
                duplicate = workflow.decide_and_resume(
                    workflow_run_id="run-1",
                    approval_id="approval-1",
                    expected_workflow_version=waiting.workflow_version,
                    actor_id="EMP-MANAGER",
                    decision=ApprovalDecisionType.APPROVE,
                )
                assert duplicate.workflow_state is WorkflowState.EXECUTING
            finally:
                gateway.release.set()
            assert future.result(timeout=5).workflow_state is WorkflowState.COMPLETED
        assert gateway.calls == 1
        with sessions() as session:
            events = session.scalars(
                select(WorkflowEvent).where(WorkflowEvent.run_id == "run-1")
            ).all()
        assert all("INTERRUPTED" not in event.event_type for event in events)
    finally:
        gateway.release.set()
        store.close()


def test_missing_information_interrupts_without_phantom_approval(tmp_path) -> None:
    sessions = build_session_factory(tmp_path)
    gateway = SuccessfulGateway()
    workflow, store = build_workflow(
        sessions, tmp_path / "checkpoints.db", gateway
    )
    try:
        snapshot = workflow.start(
            AccessRequestDraft(employee_id="EMP-1001"),
            AccessRequestContext(),
            run_id="run-1",
        )

        assert snapshot.workflow_state is WorkflowState.WAITING_USER
        assert snapshot.checkpoint_pending
        assert snapshot.next_nodes == ("await_user_input",)
        assert snapshot.missing_fields == (
            "application_code",
            "role_code",
            "duration_days",
            "business_reason",
        )
        assert snapshot.action_id is None
        assert snapshot.action_version is None
        assert snapshot.approval_id is None
        assert snapshot.approval_status is None
    finally:
        store.close()


def test_user_can_complete_missing_fields_and_continue_to_approval(tmp_path) -> None:
    sessions = build_session_factory(tmp_path)
    gateway = SuccessfulGateway()
    workflow, store = build_workflow(
        sessions, tmp_path / "checkpoints.db", gateway
    )
    try:
        workflow.start(
            AccessRequestDraft(employee_id="EMP-1001"),
            AccessRequestContext(),
            run_id="run-1",
        )

        waiting = workflow.provide_information_and_resume(
            workflow_run_id="run-1",
            expected_workflow_version=2,
            updates=AccessRequestDraft(
                application_code="CRM",
                role_code="read_only",
                duration_days=30,
                business_reason="Participate in a customer project",
            ),
            context=context(),
        )

        assert waiting.workflow_state is WorkflowState.WAITING_APPROVAL
        assert waiting.workflow_version == 4
        assert waiting.next_nodes == ("await_approval",)
        assert waiting.approval_status is ApprovalStatus.PENDING
        assert waiting.missing_fields == ()

        completed = workflow.decide_and_resume(
            workflow_run_id="run-1",
            approval_id=waiting.approval_id or "",
            expected_workflow_version=4,
            actor_id="EMP-MANAGER",
            decision=ApprovalDecisionType.APPROVE,
        )
        assert completed.workflow_state is WorkflowState.COMPLETED
        assert completed.workflow_version == 7
        assert gateway.calls == 1
    finally:
        store.close()


def test_user_can_supply_missing_fields_in_multiple_rounds(tmp_path) -> None:
    sessions = build_session_factory(tmp_path)
    gateway = SuccessfulGateway()
    workflow, store = build_workflow(
        sessions, tmp_path / "checkpoints.db", gateway
    )
    try:
        workflow.start(
            AccessRequestDraft(employee_id="EMP-1001"),
            AccessRequestContext(),
            run_id="run-1",
        )

        still_waiting = workflow.provide_information_and_resume(
            workflow_run_id="run-1",
            expected_workflow_version=2,
            updates=AccessRequestDraft(application_code="CRM"),
        )
        assert still_waiting.workflow_state is WorkflowState.WAITING_USER
        assert still_waiting.workflow_version == 4
        assert still_waiting.next_nodes == ("await_user_input",)
        assert still_waiting.missing_fields == (
            "role_code",
            "duration_days",
            "business_reason",
        )

        approval = workflow.provide_information_and_resume(
            workflow_run_id="run-1",
            expected_workflow_version=4,
            updates=AccessRequestDraft(
                role_code="read_only",
                duration_days=30,
                business_reason="Participate in a customer project",
            ),
            context=context(),
        )
        assert approval.workflow_state is WorkflowState.WAITING_APPROVAL
        assert approval.workflow_version == 6
        assert approval.next_nodes == ("await_approval",)
    finally:
        store.close()


def test_supplement_cannot_silently_change_an_existing_field(tmp_path) -> None:
    sessions = build_session_factory(tmp_path)
    workflow, store = build_workflow(
        sessions, tmp_path / "checkpoints.db", SuccessfulGateway()
    )
    try:
        workflow.start(
            AccessRequestDraft(employee_id="EMP-1001"),
            AccessRequestContext(),
            run_id="run-1",
        )

        with pytest.raises(AccessWorkflowResumeError, match="only currently missing"):
            workflow.provide_information_and_resume(
                workflow_run_id="run-1",
                expected_workflow_version=2,
                updates=AccessRequestDraft(employee_id="EMP-9999"),
            )
    finally:
        store.close()


def test_user_input_recovers_after_business_commit_before_graph_resume(tmp_path) -> None:
    sessions = build_session_factory(tmp_path)
    workflow, store = build_workflow(
        sessions, tmp_path / "checkpoints.db", SuccessfulGateway()
    )
    updates = AccessRequestDraft(
        application_code="CRM",
        role_code="read_only",
        duration_days=30,
        business_reason="Participate in a customer project",
    )
    try:
        workflow.start(
            AccessRequestDraft(employee_id="EMP-1001"),
            AccessRequestContext(),
            run_id="run-1",
        )

        # Simulate MySQL committing immediately before the process exits.
        with sessions.begin() as session:
            AccessRequestIntakeService(
                WorkflowRepository(session),
                ActionProposalRepository(session),
                ApprovalRepository(session),
            ).resume_with_information(
                request_draft(),
                context(),
                run_id="run-1",
                expected_version=2,
                provided_fields=tuple(updates.model_fields_set),
                action_id="action-recovered",
                approval_id="approval-recovered",
            )

        snapshot = workflow.provide_information_and_resume(
            workflow_run_id="run-1",
            expected_workflow_version=2,
            updates=updates,
            context=context(),
        )

        assert snapshot.workflow_state is WorkflowState.WAITING_APPROVAL
        assert snapshot.workflow_version == 4
        assert snapshot.next_nodes == ("await_approval",)
        assert snapshot.action_id == "action-recovered"
        assert snapshot.approval_id == "approval-recovered"
    finally:
        store.close()


def test_approved_graph_resumes_executes_and_finishes(tmp_path) -> None:
    sessions = build_session_factory(tmp_path)
    gateway = SuccessfulGateway()
    workflow, store = build_workflow(
        sessions, tmp_path / "checkpoints.db", gateway
    )
    try:
        start(workflow)

        snapshot = workflow.decide_and_resume(
            workflow_run_id="run-1",
            approval_id="approval-1",
            expected_workflow_version=2,
            actor_id="EMP-MANAGER",
            decision=ApprovalDecisionType.APPROVE,
        )

        assert snapshot.workflow_state is WorkflowState.COMPLETED
        assert snapshot.workflow_version == 5
        assert not snapshot.checkpoint_pending
        assert snapshot.execution_outcome == ActionGatewayOutcome.SUCCEEDED.value
        assert gateway.calls == 1
        with sessions() as session:
            events = session.scalars(
                select(WorkflowEvent)
                .where(WorkflowEvent.run_id == "run-1")
                .order_by(WorkflowEvent.sequence)
            ).all()
            assert [event.event_type for event in events][-3:] == [
                "APPROVAL_APPROVED",
                "ACTION_EXECUTION_STARTED",
                "ACTION_SUCCEEDED",
            ]
    finally:
        store.close()


def test_rejected_graph_resumes_without_executing(tmp_path) -> None:
    sessions = build_session_factory(tmp_path)
    gateway = SuccessfulGateway()
    workflow, store = build_workflow(
        sessions, tmp_path / "checkpoints.db", gateway
    )
    try:
        start(workflow)

        snapshot = workflow.decide_and_resume(
            workflow_run_id="run-1",
            approval_id="approval-1",
            expected_workflow_version=2,
            actor_id="EMP-MANAGER",
            decision=ApprovalDecisionType.REJECT,
        )

        assert snapshot.workflow_state is WorkflowState.COMPLETED
        assert snapshot.workflow_version == 3
        assert snapshot.approval_status is ApprovalStatus.REJECTED
        assert not snapshot.checkpoint_pending
        assert gateway.calls == 0
    finally:
        store.close()


def test_new_process_resumes_recorded_approval_from_same_checkpoint(tmp_path) -> None:
    sessions = build_session_factory(tmp_path)
    checkpoint_path = tmp_path / "checkpoints.db"
    first_gateway = SuccessfulGateway()
    first, first_store = build_workflow(sessions, checkpoint_path, first_gateway)
    start(first)
    first_store.close()

    # Simulate the approval transaction committing before the process exits.
    with sessions.begin() as session:
        ApprovalWorkflowService(
            ApprovalRepository(session),
            WorkflowRepository(session),
        ).decide(
            workflow_run_id="run-1",
            expected_workflow_version=2,
            approval_id="approval-1",
            actor_id="EMP-MANAGER",
            decision=ApprovalDecisionType.APPROVE,
        )

    second_gateway = SuccessfulGateway()
    second, second_store = build_workflow(sessions, checkpoint_path, second_gateway)
    try:
        snapshot = second.resume_recorded_approval(
            workflow_run_id="run-1",
            approval_id="approval-1",
        )

        assert snapshot.workflow_state is WorkflowState.COMPLETED
        assert not snapshot.checkpoint_pending
        assert second_gateway.calls == 1
    finally:
        second_store.close()


def test_repeated_same_decision_does_not_execute_twice(tmp_path) -> None:
    sessions = build_session_factory(tmp_path)
    gateway = SuccessfulGateway()
    workflow, store = build_workflow(
        sessions, tmp_path / "checkpoints.db", gateway
    )
    try:
        start(workflow)
        first = workflow.decide_and_resume(
            workflow_run_id="run-1",
            approval_id="approval-1",
            expected_workflow_version=2,
            actor_id="EMP-MANAGER",
            decision=ApprovalDecisionType.APPROVE,
        )
        second = workflow.decide_and_resume(
            workflow_run_id="run-1",
            approval_id="approval-1",
            expected_workflow_version=first.workflow_version,
            actor_id="EMP-MANAGER",
            decision=ApprovalDecisionType.APPROVE,
        )

        assert second == first
        assert gateway.calls == 1
    finally:
        store.close()
