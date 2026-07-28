from __future__ import annotations

from datetime import date

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.approval.checkpoint import ApprovalCheckpointCoordinator
from app.approval.contracts import ApprovalDecisionType
from app.integrations.enterprise_ops import (
    EnterpriseOpsAccessPackage,
    EnterpriseOpsEmployeeLifecycleProfile,
    EnterpriseOpsEmployeeLifecycleRequest,
    EnterpriseOpsEmployeeOnboardingWriteResult,
    EnterpriseOpsJobProfile,
    EnterpriseOpsOrganizationUnit,
    EnterpriseOpsWorkLocation,
)
from app.persistence.base import Base
from app.scenarios.employee_lifecycle.commands import EmployeeLifecycleIntakeService
from app.scenarios.employee_lifecycle.composition import (
    build_employee_lifecycle_execution_service,
)
from app.scenarios.employee_lifecycle.context import EmployeeLifecycleContextResolver
from app.scenarios.employee_lifecycle.contracts import (
    EmployeeLifecycleRequestDraft,
    EmployeeLifecycleRequestType,
)
from app.tickets.service import TicketProjectionService
from app.workflow.checkpoint import SqliteCheckpointStore
from app.workflow.state import WorkflowState
from enterprise_system.app.contracts import (
    ActivateEmployeeAndAccountCommand,
    AssignBaselineAccessPackageCommand,
    ChangeEmployeeAccessCommand,
    CreateAssetAdjustmentTaskCommand,
    CreateAssetAssignmentTaskCommand,
    CreateAssetReturnTaskCommand,
    CreateDisabledCorporateAccountCommand,
    CreatePendingEmployeeCommand,
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
from enterprise_system.app.employee_onboarding_service import (
    EnterpriseEmployeeOnboardingService,
)
from enterprise_system.app.persistence import EnterpriseBase
from enterprise_system.app.seed import seed_demo_data
from enterprise_system.app.service import EnterpriseResourceNotFoundError


class DirectEnterpriseLifecycleClient:
    """Test adapter preserving separate platform and enterprise transactions."""

    _COMMANDS = {
        "create_pending_employee": CreatePendingEmployeeCommand,
        "create_disabled_corporate_account": CreateDisabledCorporateAccountCommand,
        "assign_baseline_access_package": AssignBaselineAccessPackageCommand,
        "create_asset_assignment_task": CreateAssetAssignmentTaskCommand,
        "activate_employee_and_account": ActivateEmployeeAndAccountCommand,
        "update_employee_assignment": UpdateEmployeeAssignmentCommand,
        "revoke_obsolete_baseline_access": ChangeEmployeeAccessCommand,
        "grant_target_baseline_access": ChangeEmployeeAccessCommand,
        "create_asset_adjustment_task": CreateAssetAdjustmentTaskCommand,
        "verify_employee_transfer_consistency": VerifyEmployeeTransferCommand,
        "disable_corporate_account": DisableCorporateAccountCommand,
        "revoke_all_employee_access": RevokeAllEmployeeAccessCommand,
        "create_asset_return_task": CreateAssetReturnTaskCommand,
        "mark_employee_inactive": MarkEmployeeInactiveCommand,
        "verify_employee_offboarding_consistency": VerifyEmployeeOffboardingCommand,
    }
    _ONBOARDING = {
        "create_pending_employee",
        "create_disabled_corporate_account",
        "assign_baseline_access_package",
        "create_asset_assignment_task",
        "activate_employee_and_account",
    }

    def __init__(self, sessions: sessionmaker[Session]) -> None:
        self._sessions = sessions

    def query_employee_lifecycle_profile(
        self, employee_id: str
    ) -> EnterpriseOpsEmployeeLifecycleProfile | None:
        try:
            with self._sessions() as session:
                value = EnterpriseEmployeeLifecycleReadService(
                    session
                ).query_employee_snapshot(employee_id)
        except EnterpriseResourceNotFoundError:
            return None
        return EnterpriseOpsEmployeeLifecycleProfile.model_validate(
            value.model_dump(mode="json")
        )

    def query_organization_unit(
        self, department_code: str
    ) -> EnterpriseOpsOrganizationUnit | None:
        return self._read_optional(
            EnterpriseOpsOrganizationUnit,
            "query_organization_unit",
            department_code,
        )

    def query_job_profile(self, job_code: str) -> EnterpriseOpsJobProfile | None:
        return self._read_optional(
            EnterpriseOpsJobProfile, "query_job_profile", job_code
        )

    def query_work_location(
        self, location_code: str
    ) -> EnterpriseOpsWorkLocation | None:
        return self._read_optional(
            EnterpriseOpsWorkLocation, "query_work_location", location_code
        )

    def query_access_package(
        self, package_code: str
    ) -> EnterpriseOpsAccessPackage | None:
        return self._read_optional(
            EnterpriseOpsAccessPackage, "query_access_package", package_code
        )

    def query_open_employee_lifecycle_request(
        self, employee_id: str
    ) -> tuple[EnterpriseOpsEmployeeLifecycleRequest, ...]:
        with self._sessions() as session:
            values = EnterpriseEmployeeLifecycleReadService(
                session
            ).query_open_lifecycle_requests(employee_id)
        return tuple(
            EnterpriseOpsEmployeeLifecycleRequest.model_validate(
                value.model_dump(mode="json")
            )
            for value in values
        )

    def _read_optional(self, model_type, method_name: str, key: str):
        try:
            with self._sessions() as session:
                value = getattr(
                    EnterpriseEmployeeLifecycleReadService(session), method_name
                )(key)
        except EnterpriseResourceNotFoundError:
            return None
        return model_type.model_validate(value.model_dump(mode="json"))

    def __getattr__(self, action_type: str):
        command_type = self._COMMANDS.get(action_type)
        if command_type is None:
            raise AttributeError(action_type)

        def invoke(payload, *, idempotency_key):
            command = command_type.model_validate(payload)
            with self._sessions.begin() as session:
                service = (
                    EnterpriseEmployeeOnboardingService(session)
                    if action_type in self._ONBOARDING
                    else EnterpriseEmployeeLifecycleWriteService(session)
                )
                result = getattr(service, action_type)(
                    command,
                    idempotency_key=idempotency_key,
                )
            return EnterpriseOpsEmployeeOnboardingWriteResult.model_validate(
                result.model_dump(mode="json")
            )

        return invoke


def build_factories(tmp_path):
    platform_engine = create_engine(
        f"sqlite+pysqlite:///{tmp_path / 'platform-e2e.db'}"
    )
    Base.metadata.create_all(platform_engine)
    platform = sessionmaker(bind=platform_engine, expire_on_commit=False)

    enterprise_engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    EnterpriseBase.metadata.create_all(enterprise_engine)
    enterprise = sessionmaker(bind=enterprise_engine, expire_on_commit=False)
    with enterprise.begin() as session:
        seed_demo_data(session)
    return platform, enterprise


def execute_request(commands, resolver, draft, run_id):
    waiting = commands.create(
        draft,
        context=resolver.resolve(draft),
        actor_id="EMP-HR-OPERATOR",
        run_id=run_id,
    )
    assert waiting.workflow_state is WorkflowState.WAITING_APPROVAL
    result = commands.decide(
        workflow_run_id=waiting.workflow_run_id,
        approval_id=waiting.approval_id or "",
        expected_workflow_version=waiting.workflow_version,
        actor_id=draft.target_manager_id or "EMP-MAINT-MANAGER",
        decision=ApprovalDecisionType.APPROVE,
    )
    assert result.workflow_state is WorkflowState.COMPLETED


def test_three_lifecycle_paths_cross_platform_and_enterprise_transactions(
    tmp_path,
) -> None:
    platform, enterprise = build_factories(tmp_path)
    client = DirectEnterpriseLifecycleClient(enterprise)
    store = SqliteCheckpointStore(tmp_path / "e2e-checkpoint.db")
    coordinator = ApprovalCheckpointCoordinator(platform, store.saver)
    commands = EmployeeLifecycleIntakeService(
        platform,
        TicketProjectionService(platform),
        today_provider=lambda: date(2026, 7, 23),
        approval_checkpoint=coordinator,
        execution_service=build_employee_lifecycle_execution_service(
            platform,
            client,
        ),
    )
    resolver = EmployeeLifecycleContextResolver(client)
    try:
        onboarding = EmployeeLifecycleRequestDraft(
            request_type=EmployeeLifecycleRequestType.ONBOARDING,
            initiator_id="EMP-HR-OPERATOR",
            subject_employee_id="EMP-E2E-NEW",
            display_name="跨系统新员工",
            target_department_code="SALES-EAST",
            target_job_code="SALES-SPECIALIST",
            target_manager_id="EMP-MANAGER",
            work_location_code="SHANGHAI-HQ",
            effective_date=date(2026, 7, 23),
            business_reason="执行已批准的销售团队人员补充计划",
        )
        transfer = EmployeeLifecycleRequestDraft(
            request_type=EmployeeLifecycleRequestType.TRANSFER,
            initiator_id="EMP-HR-OPERATOR",
            subject_employee_id="EMP-1001",
            target_department_code="SALES-EAST",
            target_job_code="SALES-MANAGER",
            target_manager_id="EMP-MANAGER",
            work_location_code="SHANGHAI-HQ",
            effective_date=date(2026, 7, 23),
            business_reason="执行已批准的销售管理岗位调整",
        )
        offboarding = EmployeeLifecycleRequestDraft(
            request_type=EmployeeLifecycleRequestType.OFFBOARDING,
            initiator_id="EMP-HR-OPERATOR",
            subject_employee_id="EMP-2001",
            effective_date=date(2026, 7, 23),
            offboarding_reason="劳动合同到期",
            asset_return_note="归还车间终端",
            business_reason="执行已确认的员工离职手续",
        )

        execute_request(commands, resolver, onboarding, "e2e-onboarding")
        execute_request(commands, resolver, transfer, "e2e-transfer")
        execute_request(commands, resolver, offboarding, "e2e-offboarding")

        new_employee = client.query_employee_lifecycle_profile("EMP-E2E-NEW")
        transferred = client.query_employee_lifecycle_profile("EMP-1001")
        offboarded = client.query_employee_lifecycle_profile("EMP-2001")
        assert new_employee is not None and new_employee.employee.active
        assert transferred is not None
        assert transferred.employee.job_code == "SALES-MANAGER"
        assert offboarded is not None
        assert offboarded.employee.employment_status == "INACTIVE"
        assert offboarded.account is not None
        assert offboarded.account.status == "DISABLED"
    finally:
        store.close()
