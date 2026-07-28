from decimal import Decimal

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from enterprise_system.app.persistence import EnterpriseBase
from enterprise_system.app.procurement_service import (
    EnterpriseProcurementReadService,
)
from enterprise_system.app.seed import seed_demo_data
from enterprise_system.app.service import EnterpriseResourceNotFoundError


@pytest.fixture
def session_factory() -> sessionmaker[Session]:
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    EnterpriseBase.metadata.create_all(engine)
    factory: sessionmaker[Session] = sessionmaker(
        bind=engine,
        expire_on_commit=False,
    )
    with factory.begin() as session:
        seed_demo_data(session)
    return factory


def test_cost_center_snapshot_exposes_available_budget_without_write_access(
    session_factory: sessionmaker[Session],
) -> None:
    with session_factory() as session:
        cost_center = EnterpriseProcurementReadService(session).query_cost_center(
            "CC-SALES-EAST-001"
        )

    assert cost_center.budget_total == Decimal("100000.00")
    assert cost_center.spent_amount == Decimal("25000.00")
    assert cost_center.reserved_amount == Decimal("5000.00")
    assert cost_center.available_amount == Decimal("70000.00")
    assert cost_center.active is True
    assert set(cost_center.model_dump()).isdisjoint(
        {"credential", "token", "idempotency_key"}
    )


def test_current_procurement_policy_is_versioned_structured_authority(
    session_factory: sessionmaker[Session],
) -> None:
    with session_factory() as session:
        policy = EnterpriseProcurementReadService(
            session
        ).query_current_procurement_policy()

    assert policy.policy_code == "OFFICE-PROCUREMENT-2026"
    assert policy.version == 1
    assert policy.level_one_limit == Decimal("5000.00")
    assert policy.level_two_limit == Decimal("50000.00")
    assert policy.procurement_approver_id == "EMP-PROCUREMENT-OWNER"
    assert policy.allowed_item_categories == (
        "OFFICE_SUPPLIES",
        "OFFICE_EQUIPMENT",
        "OFFICE_FURNITURE",
    )


def test_open_procurement_requests_are_scoped_by_requester_and_cost_center(
    session_factory: sessionmaker[Session],
) -> None:
    with session_factory() as session:
        service = EnterpriseProcurementReadService(session)
        requests = service.query_open_procurement_requests(
            requester_id="EMP-1001",
            cost_center_code="CC-SALES-EAST-001",
        )
        no_match = service.query_open_procurement_requests(
            requester_id="EMP-1001",
            cost_center_code="CC-PLANT-001",
        )

    assert len(requests) == 1
    request = requests[0]
    assert request.external_workflow_run_id == (
        "61000000-0000-4000-8000-000000000001"
    )
    assert request.estimated_total_amount == Decimal("2600.00")
    assert [item.item_name for item in request.items] == [
        "27 英寸办公显示器",
        "无线键鼠套装",
    ]
    assert no_match == ()


def test_procurement_request_lookup_requires_one_reference_and_missing_is_clear(
    session_factory: sessionmaker[Session],
) -> None:
    with session_factory() as session:
        service = EnterpriseProcurementReadService(session)
        by_id = service.query_procurement_request(
            request_id="60000000-0000-4000-8000-000000000001"
        )
        by_workflow = service.query_procurement_request(
            workflow_run_id="61000000-0000-4000-8000-000000000001"
        )
        with pytest.raises(ValueError, match="exactly one"):
            service.query_procurement_request()
        with pytest.raises(EnterpriseResourceNotFoundError):
            service.query_budget_reservation(
                request_id="60000000-0000-4000-8000-000000000001"
            )

    assert by_id == by_workflow
