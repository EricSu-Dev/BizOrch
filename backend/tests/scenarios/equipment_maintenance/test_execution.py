from __future__ import annotations

from datetime import UTC, datetime

import pytest
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session, sessionmaker

from app.actions.idempotency import IdempotencyStatus
from app.actions.models import ActionExecutionRecord, IdempotencyRecord
from app.approval.checkpoint import ApprovalCheckpointCoordinator
from app.approval.contracts import ApprovalDecisionType
from app.integrations.enterprise_ops import (
    EnterpriseOpsEquipmentStatus,
    EnterpriseOpsEquipmentStatusView,
    EnterpriseOpsMaintenanceWorkOrder,
    EnterpriseOpsMaintenanceWorkOrderStatus,
    EnterpriseOpsMaintenanceWriteResult,
)
from app.persistence.base import Base
from app.scenarios.equipment_maintenance.commands import (
    MaintenanceRequestCommandService,
)
from app.scenarios.equipment_maintenance.composition import (
    build_maintenance_execution_service,
)
from app.tickets.service import TicketProjectionService
from app.workflow.checkpoint import SqliteCheckpointStore
from app.workflow.repository import WorkflowRepository
from app.workflow.state import WorkflowState
from tests.scenarios.equipment_maintenance.test_commands import complete_draft
from tests.scenarios.equipment_maintenance.test_policy import context


class FakeMaintenanceEnterpriseClient:
    def __init__(self) -> None:
        self.status = EnterpriseOpsEquipmentStatus.RUNNING
        self.version = 4
        self.write_calls = 0
        self.raise_on_write = False
        self.corrupt_verification = False
        self.orders: dict[str, EnterpriseOpsMaintenanceWorkOrder] = {}

    def query_equipment_status(
        self,
        equipment_code: str,
    ) -> EnterpriseOpsEquipmentStatusView:
        return EnterpriseOpsEquipmentStatusView(
            equipment_code=equipment_code,
            status=self.status,
            version=self.version,
            updated_at=datetime(2026, 7, 19, tzinfo=UTC),
        )

    def create_maintenance_work_order(self, payload, *, idempotency_key: str):
        self.write_calls += 1
        if self.raise_on_write:
            raise TimeoutError("remote result unknown")
        self.status = EnterpriseOpsEquipmentStatus.MAINTENANCE_PENDING
        self.version += 1
        order = EnterpriseOpsMaintenanceWorkOrder(
            work_order_id="MWO-001",
            equipment_code=str(payload["equipment_code"]),
            requester_id=str(payload["requester_id"]),
            fault_description=str(payload["fault_description"]),
            observed_at=datetime.fromisoformat(str(payload["observed_at"])),
            production_impact=str(payload["production_impact"]),
            safety_observation=str(payload["safety_observation"]),
            business_reason=str(payload["business_reason"]),
            priority=str(payload["priority"]),
            status=EnterpriseOpsMaintenanceWorkOrderStatus.OPEN,
            idempotency_key=idempotency_key,
            created_at=datetime(2026, 7, 19, tzinfo=UTC),
            updated_at=datetime(2026, 7, 19, tzinfo=UTC),
        )
        self.orders[order.work_order_id] = order
        return EnterpriseOpsMaintenanceWriteResult(
            work_order_id=order.work_order_id,
            equipment_code=order.equipment_code,
            work_order_status=order.status,
            equipment_status=self.status,
            equipment_version=self.version,
        )

    def query_maintenance_work_order(
        self,
        *,
        work_order_id: str | None = None,
        idempotency_key: str | None = None,
    ) -> EnterpriseOpsMaintenanceWorkOrder:
        order = (
            self.orders[work_order_id]
            if work_order_id
            else next(
                item
                for item in self.orders.values()
                if item.idempotency_key == idempotency_key
            )
        )
        if self.corrupt_verification:
            return order.model_copy(update={"priority": "HIGH"})
        return order


class CrashingGateway:
    def execute(self, *args, **kwargs):
        raise RuntimeError("process terminated during maintenance execution")


def build_commands(tmp_path, client: FakeMaintenanceEnterpriseClient):
    engine = create_engine(f"sqlite+pysqlite:///{tmp_path / 'business.db'}")
    Base.metadata.create_all(engine)
    sessions: sessionmaker[Session] = sessionmaker(
        bind=engine,
        expire_on_commit=False,
    )
    store = SqliteCheckpointStore(tmp_path / "checkpoints.db")
    commands = MaintenanceRequestCommandService(
        sessions,
        TicketProjectionService(sessions),
        approval_checkpoint=ApprovalCheckpointCoordinator(sessions, store.saver),
        execution_service=build_maintenance_execution_service(sessions, client),
    )
    return sessions, commands, store


def approve(commands: MaintenanceRequestCommandService):
    waiting = commands.create(
        complete_draft(),
        context=context(),
        actor_id="EMP-2001",
        run_id="maintenance-execution",
    )
    result = commands.decide(
        workflow_run_id=waiting.workflow_run_id,
        approval_id=waiting.approval_id or "",
        expected_workflow_version=waiting.workflow_version,
        actor_id="EMP-MAINT-MANAGER",
        decision=ApprovalDecisionType.APPROVE,
    )
    return waiting, result


def test_approved_maintenance_executes_once_and_verifies_readback(tmp_path) -> None:
    client = FakeMaintenanceEnterpriseClient()
    sessions, commands, store = build_commands(tmp_path, client)
    try:
        waiting, result = approve(commands)
        repeated = commands.decide(
            workflow_run_id=waiting.workflow_run_id,
            approval_id=waiting.approval_id or "",
            expected_workflow_version=result.workflow_version,
            actor_id="EMP-MAINT-MANAGER",
            decision=ApprovalDecisionType.APPROVE,
        )

        assert result.workflow_state is WorkflowState.COMPLETED
        assert result.execution_outcome == "SUCCEEDED"
        assert repeated == result
        assert client.write_calls == 1
        assert client.status is EnterpriseOpsEquipmentStatus.MAINTENANCE_PENDING
        assert client.version == 5
        with sessions() as session:
            assert session.scalar(select(func.count(ActionExecutionRecord.id))) == 1
            idem = session.scalar(select(IdempotencyRecord))
            assert idem is not None
            assert idem.status == IdempotencyStatus.SUCCEEDED.value
    finally:
        store.close()


def test_interrupted_maintenance_execution_requires_human_reconciliation(
    tmp_path,
) -> None:
    client = FakeMaintenanceEnterpriseClient()
    sessions, commands, store = build_commands(tmp_path, client)
    commands._execution_service._gateway = CrashingGateway()
    try:
        waiting = commands.create(
            complete_draft(),
            context=context(),
            actor_id="EMP-2001",
            run_id="maintenance-interrupted",
        )
        with pytest.raises(RuntimeError, match="process terminated"):
            commands.decide(
                workflow_run_id=waiting.workflow_run_id,
                approval_id=waiting.approval_id or "",
                expected_workflow_version=waiting.workflow_version,
                actor_id="EMP-MAINT-MANAGER",
                decision=ApprovalDecisionType.APPROVE,
            )
        with sessions() as session:
            assert (
                WorkflowRepository(session)
                .get(waiting.workflow_run_id)
                .workflow_state
                is WorkflowState.EXECUTING
            )

        result = commands.resume_recorded_approval(
            workflow_run_id=waiting.workflow_run_id,
            approval_id=waiting.approval_id or "",
        )
        assert result.workflow_state is WorkflowState.WAITING_HUMAN
        assert client.write_calls == 0
    finally:
        store.close()


def test_changed_equipment_version_after_approval_routes_to_human(tmp_path) -> None:
    client = FakeMaintenanceEnterpriseClient()
    _, commands, store = build_commands(tmp_path, client)
    try:
        waiting = commands.create(
            complete_draft(),
            context=context(),
            actor_id="EMP-2001",
            run_id="maintenance-changed-state",
        )
        client.version = 5
        result = commands.decide(
            workflow_run_id=waiting.workflow_run_id,
            approval_id=waiting.approval_id or "",
            expected_workflow_version=waiting.workflow_version,
            actor_id="EMP-MAINT-MANAGER",
            decision=ApprovalDecisionType.APPROVE,
        )

        assert result.workflow_state is WorkflowState.WAITING_HUMAN
        assert result.execution_outcome == "HUMAN_REVIEW"
        assert client.write_calls == 0
    finally:
        store.close()


def test_unknown_write_result_is_not_retried_and_routes_to_human(tmp_path) -> None:
    client = FakeMaintenanceEnterpriseClient()
    client.raise_on_write = True
    sessions, commands, store = build_commands(tmp_path, client)
    try:
        _, result = approve(commands)

        assert result.workflow_state is WorkflowState.WAITING_HUMAN
        assert result.execution_outcome == "RESULT_UNKNOWN"
        assert client.write_calls == 1
        with sessions() as session:
            idem = session.scalar(select(IdempotencyRecord))
            assert idem is not None
            assert idem.status == IdempotencyStatus.UNKNOWN.value
    finally:
        store.close()


def test_write_readback_mismatch_routes_to_human(tmp_path) -> None:
    client = FakeMaintenanceEnterpriseClient()
    client.corrupt_verification = True
    _, commands, store = build_commands(tmp_path, client)
    try:
        _, result = approve(commands)

        assert result.workflow_state is WorkflowState.WAITING_HUMAN
        assert result.execution_outcome == "VERIFICATION_FAILED"
        assert client.write_calls == 1
    finally:
        store.close()


def test_rejected_maintenance_never_invokes_write_tool(tmp_path) -> None:
    client = FakeMaintenanceEnterpriseClient()
    _, commands, store = build_commands(tmp_path, client)
    try:
        waiting = commands.create(
            complete_draft(),
            context=context(),
            actor_id="EMP-2001",
            run_id="maintenance-rejected",
        )
        result = commands.decide(
            workflow_run_id=waiting.workflow_run_id,
            approval_id=waiting.approval_id or "",
            expected_workflow_version=waiting.workflow_version,
            actor_id="EMP-MAINT-MANAGER",
            decision=ApprovalDecisionType.REJECT,
        )

        assert result.workflow_state is WorkflowState.COMPLETED
        assert result.execution_outcome is None
        assert client.write_calls == 0
    finally:
        store.close()
