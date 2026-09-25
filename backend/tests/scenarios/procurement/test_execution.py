from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.approval.contracts import ApprovalDecisionType
from app.approval.sequence_checkpoint import (
    ApprovalSequenceCheckpointCoordinator,
)
from app.integrations.enterprise_ops import (
    EnterpriseOpsBudgetReservation,
    EnterpriseOpsCostCenter,
    EnterpriseOpsProcurementRequest,
    EnterpriseOpsProcurementRequestItem,
    EnterpriseOpsProcurementWriteResult,
    EnterpriseOpsRejectedError,
    EnterpriseOpsUnavailableError,
)
from app.persistence.base import Base
from app.scenarios.procurement.commands import ProcurementRequestCommandService
from app.scenarios.procurement.composition import (
    build_procurement_execution_service,
)
from app.scenarios.procurement.policy import ProcurementPolicyEngine
from app.tickets.service import TicketProjectionService
from app.workflow.checkpoint import SqliteCheckpointStore
from app.workflow.repository import WorkflowRepository
from app.workflow.state import WorkflowState
from tests.scenarios.procurement.test_context import FakeProcurementClient
from tests.scenarios.procurement.test_policy import (
    FakeApprovalEligibility,
    ready_facts,
)


class FakeProcurementWriteClient(FakeProcurementClient):
    def __init__(
        self,
        *,
        timeout_after_commit: bool = False,
        fail_before_commit: str | None = None,
    ) -> None:
        self.timeout_after_commit = timeout_after_commit
        self.fail_before_commit = fail_before_commit
        self.request: EnterpriseOpsProcurementRequest | None = None
        self.reservation: EnterpriseOpsBudgetReservation | None = None
        self.cost_center = super().query_cost_center("CC-SALES-EAST-001")

    def query_cost_center(self, code: str):
        return self.cost_center if code == "CC-SALES-EAST-001" else None

    def query_procurement_request(
        self, *, request_id=None, workflow_run_id=None
    ):
        if self.request is None:
            return None
        if request_id and request_id != self.request.request_id:
            return None
        if (
            workflow_run_id
            and workflow_run_id != self.request.external_workflow_run_id
        ):
            return None
        return self.request

    def query_budget_reservation(self, request_id: str):
        if self.reservation and self.reservation.request_id == request_id:
            return self.reservation
        return None

    def create_procurement_request_and_reserve_budget(
        self, payload, *, idempotency_key: str
    ):
        if self.fail_before_commit == "unknown":
            raise EnterpriseOpsUnavailableError("no trustworthy response")
        if self.fail_before_commit == "rejected":
            raise EnterpriseOpsRejectedError("enterprise rejected write")
        if self.request is not None:
            return EnterpriseOpsProcurementWriteResult(
                request_id=self.request.request_id,
                reservation_id=self.reservation.reservation_id,
                cost_center_code=self.request.cost_center_code,
                reserved_amount=self.reservation.amount,
                cost_center_version=self.cost_center.version,
                replayed=True,
            )
        now = datetime(2026, 7, 26, 12, 0, tzinfo=UTC)
        amount = Decimal(str(payload["estimated_total_amount"]))
        request_id = "enterprise-procurement-request-1"
        self.request = EnterpriseOpsProcurementRequest(
            request_id=request_id,
            external_workflow_run_id=str(payload["workflow_run_id"]),
            requester_id=str(payload["requester_id"]),
            cost_center_code=str(payload["cost_center_code"]),
            estimated_total_amount=amount,
            currency=str(payload["currency"]),
            desired_date=str(payload["desired_date"]),
            delivery_location_code=str(payload["delivery_location_code"]),
            business_reason_summary=str(payload["business_reason_summary"]),
            status="SUBMITTED",
            policy_code=str(payload["policy_code"]),
            policy_version=int(payload["policy_version"]),
            items=tuple(
                EnterpriseOpsProcurementRequestItem.model_validate(item)
                for item in payload["items"]
            ),
            created_at=now,
            updated_at=now,
        )
        self.reservation = EnterpriseOpsBudgetReservation(
            reservation_id="enterprise-budget-reservation-1",
            request_id=request_id,
            cost_center_code=str(payload["cost_center_code"]),
            amount=amount,
            currency=str(payload["currency"]),
            status="ACTIVE",
            created_at=now,
            updated_at=now,
        )
        assert self.cost_center is not None
        self.cost_center = EnterpriseOpsCostCenter(
            **{
                **self.cost_center.model_dump(),
                "reserved_amount": self.cost_center.reserved_amount + amount,
                "available_amount": self.cost_center.available_amount - amount,
                "version": self.cost_center.version + 1,
            }
        )
        if self.timeout_after_commit:
            raise EnterpriseOpsUnavailableError("response lost")
        return EnterpriseOpsProcurementWriteResult(
            request_id=request_id,
            reservation_id=self.reservation.reservation_id,
            cost_center_code=self.request.cost_center_code,
            reserved_amount=amount,
            cost_center_version=self.cost_center.version,
        )


class ProcessTerminated(BaseException):
    pass


class CrashingGateway:
    def execute(self, *args, **kwargs):
        raise ProcessTerminated("process terminated during procurement execution")


def build_commands(tmp_path, client: FakeProcurementWriteClient):
    engine = create_engine(f"sqlite+pysqlite:///{tmp_path / 'platform.db'}")
    Base.metadata.create_all(engine)
    sessions: sessionmaker[Session] = sessionmaker(
        bind=engine, expire_on_commit=False
    )
    store = SqliteCheckpointStore(tmp_path / "checkpoint.db")
    commands = ProcurementRequestCommandService(
        sessions,
        TicketProjectionService(sessions),
        policy_engine=ProcurementPolicyEngine(
            FakeApprovalEligibility(),
            today=lambda: datetime(2026, 7, 26, tzinfo=UTC).date(),
        ),
        approval_checkpoint=ApprovalSequenceCheckpointCoordinator(
            sessions, store.saver
        ),
        execution_service=build_procurement_execution_service(
            sessions, client
        ),
    )
    return commands, sessions, store


def approve_one_stage(commands: ProcurementRequestCommandService):
    draft, context = ready_facts("2600.00")
    waiting = commands.create(
        draft,
        context=context,
        actor_id="EMP-1001",
        run_id="procurement-execution",
    )
    return commands.decide(
        workflow_run_id=waiting.workflow_run_id,
        approval_id=waiting.approval_id or "",
        expected_workflow_version=waiting.workflow_version,
        actor_id="EMP-MANAGER",
        decision=ApprovalDecisionType.APPROVE,
    )


def test_final_approval_executes_atomic_write_and_completes_workflow(
    tmp_path,
) -> None:
    client = FakeProcurementWriteClient()
    commands, _, store = build_commands(tmp_path, client)
    try:
        completed = approve_one_stage(commands)
    finally:
        store.close()
    assert completed.workflow_state is WorkflowState.COMPLETED
    assert client.request is not None
    assert client.request.status == "SUBMITTED"
    assert client.reservation is not None
    assert client.reservation.status == "ACTIVE"
    assert client.cost_center is not None
    assert client.cost_center.reserved_amount == Decimal("7600.00")


def test_interrupted_procurement_execution_requires_human_reconciliation(
    tmp_path,
) -> None:
    client = FakeProcurementWriteClient()
    commands, sessions, store = build_commands(tmp_path, client)
    commands._execution_service._gateway = CrashingGateway()
    try:
        with pytest.raises(ProcessTerminated, match="process terminated"):
            approve_one_stage(commands)
        workflow_run_id = "procurement-execution"
        with sessions() as session:
            assert (
                WorkflowRepository(session)
                .get(workflow_run_id)
                .workflow_state
                is WorkflowState.EXECUTING
            )
        checkpoint = commands._approval_checkpoint.snapshot(workflow_run_id)
        result = commands.resume_recorded_approval(
            workflow_run_id=workflow_run_id,
            approval_sequence_id=checkpoint.approval_sequence_id,
        )
        assert result.workflow_state is WorkflowState.WAITING_HUMAN
        assert client.request is None
    finally:
        store.close()


def test_timeout_after_enterprise_commit_is_reconciled_without_second_write(
    tmp_path,
) -> None:
    client = FakeProcurementWriteClient(timeout_after_commit=True)
    commands, _, store = build_commands(tmp_path, client)
    try:
        completed = approve_one_stage(commands)
    finally:
        store.close()
    assert completed.workflow_state is WorkflowState.COMPLETED
    assert client.request is not None
    assert client.reservation is not None
    assert client.cost_center is not None
    assert client.cost_center.reserved_amount == Decimal("7600.00")


def test_unproven_or_rejected_write_stops_for_human_without_budget_change(
    tmp_path,
) -> None:
    for index, failure in enumerate(("unknown", "rejected"), start=1):
        case_dir = tmp_path / str(index)
        case_dir.mkdir()
        client = FakeProcurementWriteClient(fail_before_commit=failure)
        commands, _, store = build_commands(case_dir, client)
        try:
            result = approve_one_stage(commands)
        finally:
            store.close()
        assert result.workflow_state is WorkflowState.WAITING_HUMAN
        assert client.request is None
        assert client.reservation is None
        assert client.cost_center is not None
        assert client.cost_center.reserved_amount == Decimal("5000.00")
