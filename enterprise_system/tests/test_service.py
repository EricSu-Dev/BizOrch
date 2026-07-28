from datetime import UTC, datetime

import pytest
from sqlalchemy import create_engine
from sqlalchemy.dialects import mysql
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool
from sqlalchemy.schema import CreateTable

from enterprise_system.app.contracts import (
    AccessRequestStatus,
    CreateAccessRequestCommand,
    CreateMaintenanceWorkOrderCommand,
    EquipmentCriticality,
    EquipmentStatus,
    GrantApplicationAccessCommand,
    MaintenanceWorkOrderStatus,
    VerifyApplicationAccessQuery,
    WriteResultStatus,
)
from enterprise_system.app.equipment_service import EnterpriseEquipmentService
from enterprise_system.app.models import (
    AccessPackageRecord,
    AccessRequestRecord,
    ApplicationRecord,
    AssetTaskRecord,
    CorporateAccountRecord,
    EmployeeRecord,
    EmployeeLifecycleRequestRecord,
    ExternalIdempotencyRecord,
    EquipmentRecord,
    JobProfileRecord,
    MaintenanceHistoryRecord,
    MaintenanceWorkOrderRecord,
    OrganizationUnitRecord,
    UserAccessRecord,
)
from enterprise_system.app.persistence import EnterpriseBase
from enterprise_system.app.seed import seed_demo_data
from enterprise_system.app.service import (
    EnterpriseAccessService,
    EnterpriseIdempotencyConflictError,
    EnterpriseRuleViolationError,
    EnterpriseResourceNotFoundError,
)


@pytest.fixture
def session_factory() -> sessionmaker[Session]:
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    EnterpriseBase.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    with factory.begin() as session:
        seed_demo_data(session)
    return factory


def access_request() -> CreateAccessRequestCommand:
    return CreateAccessRequestCommand(
        request_id="request-1",
        employee_id="EMP-1001",
        application_code="CRM",
        role_code="read_only",
        duration_days=30,
        business_reason="Participate in the East China customer project",
    )


def grant() -> GrantApplicationAccessCommand:
    return GrantApplicationAccessCommand(
        employee_id="EMP-1001",
        application_code="CRM",
        role_code="read_only",
        duration_days=30,
        access_request_id="request-1",
    )


def maintenance_command(**overrides: object) -> CreateMaintenanceWorkOrderCommand:
    values: dict[str, object] = {
        "requester_id": "EMP-2001",
        "equipment_code": "PRESS-001",
        "expected_equipment_version": 1,
        "fault_description": "飞轮侧持续异响并伴随明显振动",
        "observed_at": datetime(2026, 7, 19, 8, 0, tzinfo=UTC),
        "production_impact": "SLOWDOWN",
        "safety_observation": "未观察到直接危险",
        "business_reason": "停机检查异响来源",
        "priority": "MEDIUM",
    }
    values.update(overrides)
    return CreateMaintenanceWorkOrderCommand(**values)


def test_queries_employee_manager_and_application(
    session_factory: sessionmaker[Session],
) -> None:
    with session_factory() as session:
        service = EnterpriseAccessService(session)

        employee = service.query_employee("EMP-1001")
        manager = service.query_employee_manager("EMP-1001")
        application = service.query_application("CRM")

        assert employee.manager_id == "EMP-MANAGER"
        assert manager.employee_id == "EMP-MANAGER"
        assert "read_only" in application.allowed_role_codes


def test_queries_equipment_status_and_recent_maintenance_history(
    session_factory: sessionmaker[Session],
) -> None:
    with session_factory() as session:
        service = EnterpriseEquipmentService(session)

        equipment = service.query_equipment("press-001")
        status = service.query_equipment_status("PRESS-001")
        history = service.query_maintenance_history("PRESS-001", limit=1)

        assert equipment.criticality is EquipmentCriticality.HIGH
        assert equipment.responsible_manager_id == "EMP-MAINT-MANAGER"
        assert status.status is EquipmentStatus.RUNNING
        assert status.version == 1
        assert len(history) == 1
        assert history[0].equipment_code == "PRESS-001"
        assert "主轴振动" in history[0].fault_summary


def test_equipment_queries_reject_unknown_equipment_and_invalid_limit(
    session_factory: sessionmaker[Session],
) -> None:
    with session_factory() as session:
        service = EnterpriseEquipmentService(session)
        with pytest.raises(EnterpriseResourceNotFoundError):
            service.query_equipment("UNKNOWN-001")
        with pytest.raises(ValueError, match="between 1 and 50"):
            service.query_maintenance_history("PRESS-001", limit=0)


def test_create_maintenance_order_is_atomic_queryable_and_idempotent(
    session_factory: sessionmaker[Session],
) -> None:
    with session_factory.begin() as session:
        service = EnterpriseEquipmentService(session)
        created = service.create_maintenance_work_order(
            maintenance_command(),
            idempotency_key="maintenance-key-1",
        )
        replayed = service.create_maintenance_work_order(
            maintenance_command(),
            idempotency_key="maintenance-key-1",
        )
        order = service.query_maintenance_work_order(
            idempotency_key="maintenance-key-1"
        )
        equipment = service.query_equipment_status("PRESS-001")

        assert created.work_order_status is MaintenanceWorkOrderStatus.OPEN
        assert created.equipment_status is EquipmentStatus.MAINTENANCE_PENDING
        assert created.equipment_version == 2
        assert replayed.work_order_id == created.work_order_id
        assert replayed.replayed
        assert order.work_order_id == created.work_order_id
        assert order.requester_id == "EMP-2001"
        assert equipment.status is EquipmentStatus.MAINTENANCE_PENDING
        assert equipment.version == 2


def test_maintenance_order_rejects_changed_version_state_and_key_reuse(
    session_factory: sessionmaker[Session],
) -> None:
    with session_factory.begin() as session:
        service = EnterpriseEquipmentService(session)
        with pytest.raises(EnterpriseRuleViolationError, match="version changed"):
            service.create_maintenance_work_order(
                maintenance_command(expected_equipment_version=99),
                idempotency_key="wrong-version",
            )
        with pytest.raises(EnterpriseRuleViolationError, match="no longer allows"):
            service.create_maintenance_work_order(
                maintenance_command(
                    equipment_code="PUMP-001",
                    expected_equipment_version=2,
                ),
                idempotency_key="already-pending",
            )
        service.create_maintenance_work_order(
            maintenance_command(),
            idempotency_key="maintenance-conflict",
        )
        with pytest.raises(EnterpriseIdempotencyConflictError):
            service.create_maintenance_work_order(
                maintenance_command(priority="HIGH"),
                idempotency_key="maintenance-conflict",
            )


def test_create_request_is_idempotent_and_queryable(
    session_factory: sessionmaker[Session],
) -> None:
    with session_factory.begin() as session:
        service = EnterpriseAccessService(session)
        created = service.create_access_request(
            access_request(), idempotency_key="create-key-1"
        )
        replayed = service.create_access_request(
            access_request(), idempotency_key="create-key-1"
        )

        assert created.status is WriteResultStatus.CREATED
        assert replayed.status is WriteResultStatus.REPLAYED
        assert replayed.replayed
        assert service.query_access_request("request-1").status is AccessRequestStatus.CREATED


def test_grant_updates_request_and_read_after_write_verifies_access(
    session_factory: sessionmaker[Session],
) -> None:
    with session_factory.begin() as session:
        service = EnterpriseAccessService(session)
        service.create_access_request(access_request(), idempotency_key="create-key-1")
        result = service.grant_application_access(
            grant(), idempotency_key="grant-key-1"
        )
        verification = service.verify_application_access(
            VerifyApplicationAccessQuery(
                employee_id="EMP-1001",
                application_code="CRM",
                role_code="read_only",
            )
        )

        assert result.status is WriteResultStatus.GRANTED
        assert verification.confirmed
        assert verification.access is not None
        assert service.query_access_request("request-1").status is AccessRequestStatus.GRANTED


def test_duplicate_grant_replays_without_duplicate_access_row(
    session_factory: sessionmaker[Session],
) -> None:
    with session_factory.begin() as session:
        service = EnterpriseAccessService(session)
        service.create_access_request(access_request(), idempotency_key="create-key-1")
        first = service.grant_application_access(
            grant(), idempotency_key="grant-key-1"
        )
        replay = service.grant_application_access(
            grant(), idempotency_key="grant-key-1"
        )

        assert replay.status is WriteResultStatus.REPLAYED
        assert replay.resource_id == first.resource_id
        assert len(service.query_user_access("EMP-1001")) == 1


def test_idempotency_key_cannot_be_reused_for_changed_grant(
    session_factory: sessionmaker[Session],
) -> None:
    with session_factory.begin() as session:
        service = EnterpriseAccessService(session)
        service.create_access_request(access_request(), idempotency_key="create-key-1")
        service.grant_application_access(grant(), idempotency_key="grant-key-1")

        with pytest.raises(EnterpriseIdempotencyConflictError):
            service.grant_application_access(
                grant().model_copy(update={"duration_days": 60}),
                idempotency_key="grant-key-1",
            )


@pytest.mark.parametrize(
    "command",
    [
        GrantApplicationAccessCommand(
            employee_id="EMP-INACTIVE",
            application_code="CRM",
            role_code="read_only",
            duration_days=30,
        ),
        GrantApplicationAccessCommand(
            employee_id="EMP-1001",
            application_code="ERP",
            role_code="admin",
            duration_days=30,
        ),
    ],
)
def test_authoritative_enterprise_rules_reject_invalid_grants(
    session_factory: sessionmaker[Session],
    command: GrantApplicationAccessCommand,
) -> None:
    with session_factory.begin() as session:
        with pytest.raises(EnterpriseRuleViolationError):
            EnterpriseAccessService(session).grant_application_access(
                command, idempotency_key="grant-key-1"
            )


def test_grant_must_match_linked_access_request(
    session_factory: sessionmaker[Session],
) -> None:
    with session_factory.begin() as session:
        service = EnterpriseAccessService(session)
        service.create_access_request(access_request(), idempotency_key="create-key-1")

        with pytest.raises(EnterpriseRuleViolationError):
            service.grant_application_access(
                grant().model_copy(update={"role_code": "admin"}),
                idempotency_key="grant-key-1",
            )


def test_enterprise_tables_compile_for_mysql() -> None:
    models = (
        EmployeeRecord,
        ApplicationRecord,
        UserAccessRecord,
        AccessRequestRecord,
        ExternalIdempotencyRecord,
        EquipmentRecord,
        MaintenanceHistoryRecord,
        MaintenanceWorkOrderRecord,
        OrganizationUnitRecord,
        JobProfileRecord,
        CorporateAccountRecord,
        AccessPackageRecord,
        AssetTaskRecord,
        EmployeeLifecycleRequestRecord,
    )
    ddls = [
        str(CreateTable(model.__table__).compile(dialect=mysql.dialect()))
        for model in models
    ]

    assert all("CREATE TABLE enterprise_" in ddl for ddl in ddls)
    assert "JSON" in ddls[1]
    assert "JSON" in ddls[4]
    assert any(
        "enterprise_access_packages" in ddl and "JSON" in ddl for ddl in ddls
    )
