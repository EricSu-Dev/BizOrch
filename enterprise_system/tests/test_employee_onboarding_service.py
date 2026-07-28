from datetime import date

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from enterprise_system.app.contracts import (
    ActivateEmployeeAndAccountCommand,
    AssignBaselineAccessPackageCommand,
    CreateAssetAssignmentTaskCommand,
    CreateDisabledCorporateAccountCommand,
    CreatePendingEmployeeCommand,
)
from enterprise_system.app.employee_lifecycle_service import (
    EnterpriseEmployeeLifecycleReadService,
)
from enterprise_system.app.employee_onboarding_service import (
    EnterpriseEmployeeOnboardingService,
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


def pending_command() -> CreatePendingEmployeeCommand:
    return CreatePendingEmployeeCommand(
        employee_id="EMP-NEW-001",
        display_name="新员工",
        department_code="SALES-EAST",
        job_code="SALES-SPECIALIST",
        manager_id="EMP-MANAGER",
        work_location_code="SHANGHAI-HQ",
        initiator_id="EMP-HR-OPERATOR",
        effective_date=date(2026, 7, 23),
        business_reason="销售团队已批准的人员补充计划",
        expected_department_version=1,
        expected_job_version=1,
        expected_work_location_version=1,
    )


def run_first_four_steps(
    sessions: sessionmaker[Session],
    *,
    employee_id: str = "EMP-NEW-001",
) -> None:
    with sessions.begin() as session:
        service = EnterpriseEmployeeOnboardingService(session)
        service.create_pending_employee(
            pending_command(), idempotency_key="onboarding-step-1"
        )
    with sessions.begin() as session:
        EnterpriseEmployeeOnboardingService(
            session
        ).create_disabled_corporate_account(
            CreateDisabledCorporateAccountCommand(employee_id=employee_id),
            idempotency_key="onboarding-step-2",
        )
    bindings = (
        {"application_code": "CRM", "role_code": "standard"},
        {"application_code": "ERP", "role_code": "read_only"},
    )
    with sessions.begin() as session:
        EnterpriseEmployeeOnboardingService(
            session
        ).assign_baseline_access_package(
            AssignBaselineAccessPackageCommand(
                employee_id=employee_id,
                package_code="SALES-STANDARD",
                package_version=1,
                role_bindings=bindings,
            ),
            idempotency_key="onboarding-step-3",
        )
    with sessions.begin() as session:
        EnterpriseEmployeeOnboardingService(session).create_asset_assignment_task(
            CreateAssetAssignmentTaskCommand(
                employee_id=employee_id,
                asset_profile_code="OFFICE-LAPTOP",
            ),
            idempotency_key="onboarding-step-4",
        )


def test_five_step_onboarding_activates_only_after_all_prerequisites(
    sessions: sessionmaker[Session],
) -> None:
    run_first_four_steps(sessions)

    with sessions.begin() as session:
        result = EnterpriseEmployeeOnboardingService(
            session
        ).activate_employee_and_account(
            ActivateEmployeeAndAccountCommand(
                employee_id="EMP-NEW-001",
                expected_employee_version=1,
                expected_account_version=1,
            ),
            idempotency_key="onboarding-step-5",
        )

    with sessions() as session:
        profile = EnterpriseEmployeeLifecycleReadService(
            session
        ).query_employee_snapshot("EMP-NEW-001")
    assert result.employee_version == 2
    assert result.account_version == 2
    assert profile.employee.active
    assert profile.employee.employment_status == "ACTIVE"
    assert profile.account is not None
    assert profile.account.status.value == "ACTIVE"
    assert {
        (item.application_code, item.role_code) for item in profile.active_access
    } == {("CRM", "standard"), ("ERP", "read_only")}
    assert profile.open_lifecycle_requests == ()


def test_each_onboarding_write_replays_same_idempotency_key(
    sessions: sessionmaker[Session],
) -> None:
    command = pending_command()
    with sessions.begin() as session:
        first = EnterpriseEmployeeOnboardingService(session).create_pending_employee(
            command,
            idempotency_key="same-onboarding-step",
        )
    with sessions.begin() as session:
        replay = EnterpriseEmployeeOnboardingService(session).create_pending_employee(
            command,
            idempotency_key="same-onboarding-step",
        )

    assert not first.replayed
    assert replay.replayed
    assert replay.resource_id == first.resource_id


def test_activation_rejects_partial_onboarding_and_keeps_employee_disabled(
    sessions: sessionmaker[Session],
) -> None:
    with sessions.begin() as session:
        EnterpriseEmployeeOnboardingService(session).create_pending_employee(
            pending_command(),
            idempotency_key="partial-step-1",
        )
    with sessions.begin() as session:
        EnterpriseEmployeeOnboardingService(
            session
        ).create_disabled_corporate_account(
            CreateDisabledCorporateAccountCommand(employee_id="EMP-NEW-001"),
            idempotency_key="partial-step-2",
        )

    with pytest.raises(EnterpriseRuleViolationError):
        with sessions.begin() as session:
            EnterpriseEmployeeOnboardingService(
                session
            ).activate_employee_and_account(
                ActivateEmployeeAndAccountCommand(
                    employee_id="EMP-NEW-001",
                    expected_employee_version=1,
                    expected_account_version=1,
                ),
                idempotency_key="partial-step-5",
            )

    with sessions() as session:
        profile = EnterpriseEmployeeLifecycleReadService(
            session
        ).query_employee_snapshot("EMP-NEW-001")
    assert not profile.employee.active
    assert profile.employee.employment_status == "PENDING_ONBOARDING"
    assert profile.account is not None
    assert profile.account.status.value == "DISABLED"
