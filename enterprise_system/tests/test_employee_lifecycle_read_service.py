from datetime import date

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from enterprise_system.app.contracts import (
    EmployeeLifecycleRequestStatus,
    EmployeeLifecycleRequestType,
)
from enterprise_system.app.employee_lifecycle_service import (
    EnterpriseEmployeeLifecycleReadService,
)
from enterprise_system.app.models import (
    EmployeeLifecycleRequestRecord,
    EmployeeRecord,
)
from enterprise_system.app.persistence import EnterpriseBase
from enterprise_system.app.seed import seed_demo_data
from enterprise_system.app.service import (
    EnterpriseAccessService,
    EnterpriseResourceNotFoundError,
    EnterpriseRuleViolationError,
)


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


def test_employee_snapshot_joins_authoritative_lifecycle_facts(
    session_factory: sessionmaker[Session],
) -> None:
    with session_factory() as session:
        snapshot = EnterpriseEmployeeLifecycleReadService(
            session
        ).query_employee_snapshot("EMP-2001")

    assert snapshot.employee.employee_id == "EMP-2001"
    assert snapshot.employee.job_code == "PLANT-OPERATOR"
    assert snapshot.department.department_code == "PLANT-WORKSHOP-1"
    assert snapshot.job.asset_profile_code == "SHOP-FLOOR-TERMINAL"
    assert snapshot.account is not None
    assert snapshot.account.username == "plant.operator"
    assert snapshot.account.model_dump().keys().isdisjoint(
        {"password", "token", "credential"}
    )
    assert len(snapshot.asset_tasks) == 1
    assert snapshot.asset_tasks[0].status.value == "COMPLETED"
    assert snapshot.open_lifecycle_requests == ()


def test_job_baseline_is_deterministic_and_contains_only_role_bindings(
    session_factory: sessionmaker[Session],
) -> None:
    with session_factory() as session:
        baseline = EnterpriseEmployeeLifecycleReadService(
            session
        ).query_job_access_baseline("EQUIPMENT-MAINTENANCE-ENGINEER")

    assert baseline.package_code == "MAINTENANCE-ENGINEER"
    assert baseline.role_bindings == (
        {"application_code": "ERP", "role_code": "standard"},
    )


def test_open_lifecycle_request_query_excludes_terminal_history(
    session_factory: sessionmaker[Session],
) -> None:
    with session_factory.begin() as session:
        session.add(
            EmployeeLifecycleRequestRecord(
                request_id="lifecycle-open-1",
                request_type=EmployeeLifecycleRequestType.TRANSFER.value,
                subject_employee_id="EMP-2001",
                initiator_id="EMP-HR-OPERATOR",
                status=EmployeeLifecycleRequestStatus.PENDING_APPROVAL.value,
                effective_date=date(2026, 7, 23),
                safe_summary="将员工调到设备工程部",
                idempotency_key="lifecycle-open-key-1",
            )
        )

    with session_factory() as session:
        service = EnterpriseEmployeeLifecycleReadService(session)
        open_requests = service.query_open_lifecycle_requests("EMP-2001")
        terminal_history = service.query_open_lifecycle_requests("EMP-1001")

    assert [request.request_id for request in open_requests] == [
        "lifecycle-open-1"
    ]
    assert terminal_history == ()

    with session_factory() as session:
        service = EnterpriseEmployeeLifecycleReadService(session)
        by_id = service.query_lifecycle_request(request_id="lifecycle-open-1")
        by_key = service.query_lifecycle_request(
            idempotency_key="lifecycle-open-key-1"
        )
    assert by_id == by_key


def test_lifecycle_request_lookup_requires_exactly_one_reference(
    session_factory: sessionmaker[Session],
) -> None:
    with session_factory() as session:
        service = EnterpriseEmployeeLifecycleReadService(session)
        with pytest.raises(ValueError, match="exactly one"):
            service.query_lifecycle_request()
        with pytest.raises(ValueError, match="exactly one"):
            service.query_lifecycle_request(
                request_id="request-1",
                idempotency_key="key-1",
            )


def test_missing_authoritative_department_or_job_is_explicit(
    session_factory: sessionmaker[Session],
) -> None:
    with session_factory() as session:
        service = EnterpriseEmployeeLifecycleReadService(session)
        with pytest.raises(EnterpriseResourceNotFoundError):
            service.query_organization_unit("UNKNOWN-DEPARTMENT")
        with pytest.raises(EnterpriseResourceNotFoundError):
            service.query_job_profile("UNKNOWN-JOB")
        with pytest.raises(EnterpriseResourceNotFoundError):
            service.query_work_location("UNKNOWN-LOCATION")


def test_work_location_is_authoritative_and_versioned(
    session_factory: sessionmaker[Session],
) -> None:
    with session_factory() as session:
        location = EnterpriseEmployeeLifecycleReadService(
            session
        ).query_work_location("SHANGHAI-HQ")

    assert location.display_name == "上海总部"
    assert location.active is True
    assert location.version == 1


def test_legacy_active_flag_cannot_conflict_with_employment_status(
    session_factory: sessionmaker[Session],
) -> None:
    with session_factory.begin() as session:
        employee = session.get(EmployeeRecord, "EMP-2001")
        assert employee is not None
        employee.active = False

    with session_factory() as session:
        with pytest.raises(
            EnterpriseRuleViolationError,
            match="active flag conflicts",
        ):
            EnterpriseAccessService(session).query_employee("EMP-2001")
