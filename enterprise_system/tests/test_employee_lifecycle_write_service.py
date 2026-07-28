from datetime import date

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from enterprise_system.app.contracts import (
    ChangeEmployeeAccessCommand,
    CreateAssetAdjustmentTaskCommand,
    CreateAssetReturnTaskCommand,
    DisableCorporateAccountCommand,
    MarkEmployeeInactiveCommand,
    RevokeAllEmployeeAccessCommand,
    UpdateEmployeeAssignmentCommand,
    VerifyEmployeeOffboardingCommand,
    VerifyEmployeeTransferCommand,
)
from enterprise_system.app.employee_lifecycle_service import (
    EnterpriseEmployeeLifecycleReadService,
)
from enterprise_system.app.employee_lifecycle_write_service import (
    EnterpriseEmployeeLifecycleWriteService,
)
from enterprise_system.app.persistence import EnterpriseBase
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
        bind=engine,
        expire_on_commit=False,
    )
    with factory.begin() as session:
        seed_demo_data(session)
    return factory


def test_transfer_updates_assignment_access_asset_and_request(
    sessions: sessionmaker[Session],
) -> None:
    common = {
        "employee_id": "EMP-1001",
        "initiator_id": "EMP-HR-OPERATOR",
        "effective_date": date(2026, 7, 23),
        "business_reason": "执行已批准的销售岗位调整",
    }
    with sessions.begin() as session:
        EnterpriseEmployeeLifecycleWriteService(session).update_employee_assignment(
            UpdateEmployeeAssignmentCommand(
                **common,
                department_code="SALES-EAST",
                job_code="SALES-MANAGER",
                manager_id="EMP-MANAGER",
                work_location_code="SHANGHAI-HQ",
                expected_employee_version=1,
                expected_department_version=1,
                expected_job_version=1,
                expected_work_location_version=1,
            ),
            idempotency_key="transfer-step-1",
        )
    with sessions.begin() as session:
        EnterpriseEmployeeLifecycleWriteService(
            session
        ).revoke_obsolete_baseline_access(
            ChangeEmployeeAccessCommand(employee_id="EMP-1001", role_bindings=()),
            idempotency_key="transfer-step-2",
        )
    target_bindings = (
        {"application_code": "CRM", "role_code": "standard"},
        {"application_code": "ERP", "role_code": "read_only"},
    )
    with sessions.begin() as session:
        EnterpriseEmployeeLifecycleWriteService(
            session
        ).grant_target_baseline_access(
            ChangeEmployeeAccessCommand(
                employee_id="EMP-1001",
                package_code="SALES-STANDARD",
                package_version=1,
                role_bindings=target_bindings,
            ),
            idempotency_key="transfer-step-3",
        )
    with sessions.begin() as session:
        EnterpriseEmployeeLifecycleWriteService(
            session
        ).create_asset_adjustment_task(
            CreateAssetAdjustmentTaskCommand(
                employee_id="EMP-1001",
                asset_profile_code="OFFICE-MANAGER",
            ),
            idempotency_key="transfer-step-4",
        )
    with sessions.begin() as session:
        EnterpriseEmployeeLifecycleWriteService(
            session
        ).verify_employee_transfer_consistency(
            VerifyEmployeeTransferCommand(
                employee_id="EMP-1001",
                expected_department_code="SALES-EAST",
                expected_job_code="SALES-MANAGER",
                expected_manager_id="EMP-MANAGER",
                expected_work_location_code="SHANGHAI-HQ",
            ),
            idempotency_key="transfer-step-5",
        )

    with sessions() as session:
        profile = EnterpriseEmployeeLifecycleReadService(
            session
        ).query_employee_snapshot("EMP-1001")
    assert profile.employee.job_code == "SALES-MANAGER"
    assert profile.employee.version == 2
    assert {
        (item.application_code, item.role_code) for item in profile.active_access
    } == {("CRM", "standard"), ("ERP", "read_only")}
    assert any(task.task_type.value == "ADJUST" for task in profile.asset_tasks)
    assert profile.open_lifecycle_requests == ()


def test_offboarding_keeps_security_actions_and_finishes_inactive(
    sessions: sessionmaker[Session],
) -> None:
    with sessions.begin() as session:
        EnterpriseEmployeeLifecycleWriteService(session).disable_corporate_account(
            DisableCorporateAccountCommand(
                employee_id="EMP-2001",
                initiator_id="EMP-HR-OPERATOR",
                effective_date=date(2026, 7, 23),
                business_reason="执行已确认的员工离职手续",
                expected_account_version=1,
            ),
            idempotency_key="offboarding-step-1",
        )
    with sessions.begin() as session:
        EnterpriseEmployeeLifecycleWriteService(
            session
        ).revoke_all_employee_access(
            RevokeAllEmployeeAccessCommand(
                employee_id="EMP-2001",
                access_ids=(),
            ),
            idempotency_key="offboarding-step-2",
        )
    with sessions.begin() as session:
        EnterpriseEmployeeLifecycleWriteService(session).create_asset_return_task(
            CreateAssetReturnTaskCommand(
                employee_id="EMP-2001",
                existing_asset_task_ids=(
                    "40000000-0000-4000-8000-000000000001",
                ),
                asset_return_note="归还车间终端",
            ),
            idempotency_key="offboarding-step-3",
        )
    with sessions.begin() as session:
        EnterpriseEmployeeLifecycleWriteService(session).mark_employee_inactive(
            MarkEmployeeInactiveCommand(
                employee_id="EMP-2001",
                expected_employee_version=1,
                offboarding_reason="劳动合同到期",
            ),
            idempotency_key="offboarding-step-4",
        )
    with sessions.begin() as session:
        EnterpriseEmployeeLifecycleWriteService(
            session
        ).verify_employee_offboarding_consistency(
            VerifyEmployeeOffboardingCommand(employee_id="EMP-2001"),
            idempotency_key="offboarding-step-5",
        )

    with sessions() as session:
        profile = EnterpriseEmployeeLifecycleReadService(
            session
        ).query_employee_snapshot("EMP-2001")
    assert not profile.employee.active
    assert profile.employee.employment_status == "INACTIVE"
    assert profile.account is not None
    assert profile.account.status.value == "DISABLED"
    assert profile.active_access == ()
    assert any(task.task_type.value == "RETURN" for task in profile.asset_tasks)
    assert profile.open_lifecycle_requests == ()


def test_offboarding_cannot_mark_employee_inactive_before_account_disable(
    sessions: sessionmaker[Session],
) -> None:
    with pytest.raises(EnterpriseRuleViolationError):
        with sessions.begin() as session:
            EnterpriseEmployeeLifecycleWriteService(
                session
            ).mark_employee_inactive(
                MarkEmployeeInactiveCommand(
                    employee_id="EMP-2001",
                    expected_employee_version=1,
                ),
                idempotency_key="unsafe-offboarding-order",
            )

    with sessions() as session:
        profile = EnterpriseEmployeeLifecycleReadService(
            session
        ).query_employee_snapshot("EMP-2001")
    assert profile.employee.active
    assert profile.account is not None
    assert profile.account.status.value == "ACTIVE"


def test_offboarding_rejects_access_created_after_approval(
    sessions: sessionmaker[Session],
) -> None:
    from datetime import UTC, datetime
    from uuid import uuid4

    from enterprise_system.app.models import UserAccessRecord

    with sessions.begin() as session:
        session.add(
            UserAccessRecord(
                access_id=str(uuid4()),
                employee_id="EMP-2001",
                application_code="ERP",
                role_code="read_only",
                expires_at=datetime(2099, 12, 31, tzinfo=UTC),
                active=True,
            )
        )

    with pytest.raises(EnterpriseRuleViolationError):
        with sessions.begin() as session:
            EnterpriseEmployeeLifecycleWriteService(
                session
            ).revoke_all_employee_access(
                RevokeAllEmployeeAccessCommand(
                    employee_id="EMP-2001",
                    access_ids=(),
                ),
                idempotency_key="stale-offboarding-access",
            )
