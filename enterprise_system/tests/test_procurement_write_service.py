from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from enterprise_system.app.contracts import (
    CreateProcurementRequestAndReserveBudgetCommand,
    CreateProcurementRequestItemCommand,
)
from enterprise_system.app.models import (
    BudgetReservationRecord,
    CostCenterRecord,
    ProcurementRequestRecord,
)
from enterprise_system.app.persistence import EnterpriseBase
from enterprise_system.app.procurement_write_service import (
    EnterpriseProcurementWriteService,
)
from enterprise_system.app.seed import seed_demo_data
from enterprise_system.app.service import EnterpriseRuleViolationError


@pytest.fixture
def sessions() -> sessionmaker[Session]:
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    EnterpriseBase.metadata.create_all(engine)
    factory: sessionmaker[Session] = sessionmaker(
        bind=engine, expire_on_commit=False
    )
    with factory.begin() as session:
        seed_demo_data(session)
    return factory


def command(
    workflow_run_id: str = "72000000-0000-4000-8000-000000000001",
    *,
    amount: str = "1200.00",
    expected_version: int = 1,
    expected_reserved: str = "5000.00",
    item_name: str = "会议室白板",
) -> CreateProcurementRequestAndReserveBudgetCommand:
    return CreateProcurementRequestAndReserveBudgetCommand(
        workflow_run_id=workflow_run_id,
        requester_id="EMP-1001",
        items=(
            CreateProcurementRequestItemCommand(
                line_no=1,
                item_name=item_name,
                item_category="OFFICE_SUPPLIES",
                quantity=2,
                specification_note="标准尺寸",
            ),
        ),
        estimated_total_amount=Decimal(amount),
        currency="CNY",
        cost_center_code="CC-SALES-EAST-001",
        desired_date=date(2026, 8, 20),
        delivery_location_code="SHANGHAI-HQ",
        business_reason_summary="销售团队项目复盘会议需要补充公共白板",
        policy_code="OFFICE-PROCUREMENT-2026",
        policy_version=1,
        expected_cost_center_version=expected_version,
        expected_reserved_amount=Decimal(expected_reserved),
        expected_business_approver_id="EMP-MANAGER",
        expected_budget_owner_id="EMP-BUDGET-OWNER",
        expected_procurement_approver_id="EMP-PROCUREMENT-OWNER",
    )


def test_request_lines_reservation_and_budget_update_commit_atomically(
    sessions: sessionmaker[Session],
) -> None:
    with sessions.begin() as session:
        result = EnterpriseProcurementWriteService(
            session
        ).create_request_and_reserve_budget(
            command(), idempotency_key="procurement-atomic-1"
        )

    with sessions() as session:
        request = session.get(ProcurementRequestRecord, result.request_id)
        reservation = session.get(
            BudgetReservationRecord, result.reservation_id
        )
        cost_center = session.get(CostCenterRecord, "CC-SALES-EAST-001")
    assert request is not None and request.status == "SUBMITTED"
    assert reservation is not None and reservation.status == "ACTIVE"
    assert reservation.amount == Decimal("1200.00")
    assert cost_center is not None
    assert cost_center.reserved_amount == Decimal("6200.00")
    assert cost_center.version == 2


def test_same_key_replays_without_reserving_budget_twice(
    sessions: sessionmaker[Session],
) -> None:
    with sessions.begin() as session:
        first = EnterpriseProcurementWriteService(
            session
        ).create_request_and_reserve_budget(
            command(), idempotency_key="procurement-replay"
        )
    with sessions.begin() as session:
        replay = EnterpriseProcurementWriteService(
            session
        ).create_request_and_reserve_budget(
            command(), idempotency_key="procurement-replay"
        )
    with sessions() as session:
        cost_center = session.get(CostCenterRecord, "CC-SALES-EAST-001")
        request_count = len(
            session.scalars(select(ProcurementRequestRecord)).all()
        )
    assert replay.replayed
    assert replay.request_id == first.request_id
    assert cost_center is not None
    assert cost_center.reserved_amount == Decimal("6200.00")
    assert request_count == 2  # one seed request and one new request


def test_stale_or_insufficient_budget_rolls_back_every_procurement_row(
    sessions: sessionmaker[Session],
) -> None:
    with pytest.raises(EnterpriseRuleViolationError):
        with sessions.begin() as session:
            EnterpriseProcurementWriteService(
                session
            ).create_request_and_reserve_budget(
                command(amount="70000.01"),
                idempotency_key="procurement-insufficient",
            )

    with sessions() as session:
        request = session.scalar(
            select(ProcurementRequestRecord).where(
                ProcurementRequestRecord.idempotency_key
                == "procurement-insufficient"
            )
        )
        reservation = session.scalar(
            select(BudgetReservationRecord).where(
                BudgetReservationRecord.idempotency_key
                == "procurement-insufficient"
            )
        )
        cost_center = session.get(CostCenterRecord, "CC-SALES-EAST-001")
    assert request is None
    assert reservation is None
    assert cost_center is not None
    assert cost_center.reserved_amount == Decimal("5000.00")
    assert cost_center.version == 1


def test_second_request_with_stale_budget_snapshot_cannot_overdraw(
    sessions: sessionmaker[Session],
) -> None:
    with sessions.begin() as session:
        EnterpriseProcurementWriteService(
            session
        ).create_request_and_reserve_budget(
            command(), idempotency_key="procurement-concurrent-a"
        )
    with pytest.raises(EnterpriseRuleViolationError, match="budget facts changed"):
        with sessions.begin() as session:
            EnterpriseProcurementWriteService(
                session
            ).create_request_and_reserve_budget(
                command(
                    "72000000-0000-4000-8000-000000000002",
                    amount="69000.00",
                    item_name="会议室投影幕布",
                ),
                idempotency_key="procurement-concurrent-b",
            )
