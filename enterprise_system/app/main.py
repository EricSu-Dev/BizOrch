"""Internal HTTP boundary for the independent simulated enterprise system."""

import os
import secrets
from collections.abc import Iterator

from fastapi import Depends, FastAPI, Header, HTTPException, Query, status
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session, sessionmaker

from enterprise_system.app.contracts import (
    AccessRequestView,
    AccessPackageView,
    AccessVerificationResult,
    ActivateEmployeeAndAccountCommand,
    ApplicationView,
    AssignBaselineAccessPackageCommand,
    ChangeEmployeeAccessCommand,
    AssetTaskView,
    BudgetReservationView,
    CostCenterView,
    CorporateAccountView,
    CreateProcurementRequestAndReserveBudgetCommand,
    CreateAssetAssignmentTaskCommand,
    CreateAssetAdjustmentTaskCommand,
    CreateAssetReturnTaskCommand,
    CreateDisabledCorporateAccountCommand,
    CreateMaintenanceWorkOrderCommand,
    CreateAccessRequestCommand,
    CreatePendingEmployeeCommand,
    EmployeeView,
    EmployeeLifecycleRequestView,
    EmployeeLifecycleSnapshot,
    EmployeeOnboardingWriteResult,
    DisableCorporateAccountCommand,
    EquipmentStatusView,
    EquipmentView,
    EnterpriseWriteResult,
    GrantApplicationAccessCommand,
    JobProfileView,
    MaintenanceHistoryView,
    MaintenanceWorkOrderView,
    MaintenanceWorkOrderWriteResult,
    MarkEmployeeInactiveCommand,
    OrganizationUnitView,
    ProcurementPolicyView,
    ProcurementRequestView,
    ProcurementWriteResult,
    RevokeAllEmployeeAccessCommand,
    UserAccessView,
    UpdateEmployeeAssignmentCommand,
    VerifyEmployeeOffboardingCommand,
    VerifyEmployeeTransferCommand,
    VerifyApplicationAccessQuery,
    WorkLocationView,
)
from enterprise_system.app.employee_lifecycle_service import (
    EnterpriseEmployeeLifecycleReadService,
)
from enterprise_system.app.employee_onboarding_service import (
    EnterpriseEmployeeOnboardingService,
)
from enterprise_system.app.employee_lifecycle_write_service import (
    EnterpriseEmployeeLifecycleWriteService,
)
from enterprise_system.app.equipment_service import EnterpriseEquipmentService
from enterprise_system.app.persistence import (
    build_enterprise_engine,
    build_enterprise_session_factory,
)
from enterprise_system.app.procurement_service import EnterpriseProcurementReadService
from enterprise_system.app.procurement_write_service import (
    EnterpriseProcurementWriteService,
)
from enterprise_system.app.schema import upgrade_enterprise_schema
from enterprise_system.app.seed import seed_demo_data
from enterprise_system.app.service import (
    EnterpriseAccessService,
    EnterpriseIdempotencyConflictError,
    EnterpriseResourceNotFoundError,
    EnterpriseRuleViolationError,
)


def create_app(
    session_factory: sessionmaker[Session],
    *,
    internal_token: str,
) -> FastAPI:
    """Create an injectable app; runtime secrets never enter source code."""
    app = FastAPI(
        title="BizOrch Simulated Enterprise System",
        version="0.1.0",
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )

    def get_session() -> Iterator[Session]:
        with session_factory.begin() as session:
            yield session

    def require_internal_token(
        provided: str | None = Header(default=None, alias="X-Enterprise-Internal-Token"),
    ) -> None:
        if not internal_token:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="internal authentication is not configured",
            )
        if provided is None or not secrets.compare_digest(provided, internal_token):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="invalid internal credential",
            )

    internal_dependencies = [Depends(require_internal_token)]

    @app.exception_handler(EnterpriseResourceNotFoundError)
    async def resource_not_found(_, exc: EnterpriseResourceNotFoundError):
        return JSONResponse(status_code=404, content={"detail": str(exc)})

    @app.exception_handler(EnterpriseRuleViolationError)
    async def rule_violation(_, exc: EnterpriseRuleViolationError):
        return JSONResponse(status_code=409, content={"detail": str(exc)})

    @app.exception_handler(EnterpriseIdempotencyConflictError)
    async def idempotency_conflict(_, exc: EnterpriseIdempotencyConflictError):
        return JSONResponse(status_code=409, content={"detail": str(exc)})

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok", "service": "simulated-enterprise-system"}

    @app.get(
        "/internal/v1/employees/{employee_id}",
        response_model=EmployeeView,
        dependencies=internal_dependencies,
    )
    def query_employee(
        employee_id: str,
        session: Session = Depends(get_session),
    ) -> EmployeeView:
        return EnterpriseAccessService(session).query_employee(employee_id)

    @app.get(
        "/internal/v1/employees/{employee_id}/manager",
        response_model=EmployeeView,
        dependencies=internal_dependencies,
    )
    def query_employee_manager(
        employee_id: str,
        session: Session = Depends(get_session),
    ) -> EmployeeView:
        return EnterpriseAccessService(session).query_employee_manager(employee_id)

    @app.get(
        "/internal/v1/organization-units/{department_code}",
        response_model=OrganizationUnitView,
        dependencies=internal_dependencies,
    )
    def query_organization_unit(
        department_code: str,
        session: Session = Depends(get_session),
    ) -> OrganizationUnitView:
        return EnterpriseEmployeeLifecycleReadService(
            session
        ).query_organization_unit(department_code)

    @app.get(
        "/internal/v1/work-locations/{location_code}",
        response_model=WorkLocationView,
        dependencies=internal_dependencies,
    )
    def query_work_location(
        location_code: str,
        session: Session = Depends(get_session),
    ) -> WorkLocationView:
        return EnterpriseEmployeeLifecycleReadService(
            session
        ).query_work_location(location_code)

    @app.get(
        "/internal/v1/job-profiles/{job_code}",
        response_model=JobProfileView,
        dependencies=internal_dependencies,
    )
    def query_job_profile(
        job_code: str,
        session: Session = Depends(get_session),
    ) -> JobProfileView:
        return EnterpriseEmployeeLifecycleReadService(session).query_job_profile(
            job_code
        )

    @app.get(
        "/internal/v1/job-profiles/{job_code}/access-baseline",
        response_model=AccessPackageView,
        dependencies=internal_dependencies,
    )
    def query_job_access_baseline(
        job_code: str,
        session: Session = Depends(get_session),
    ) -> AccessPackageView:
        return EnterpriseEmployeeLifecycleReadService(
            session
        ).query_job_access_baseline(job_code)

    @app.get(
        "/internal/v1/access-packages/{package_code}",
        response_model=AccessPackageView,
        dependencies=internal_dependencies,
    )
    def query_access_package(
        package_code: str,
        session: Session = Depends(get_session),
    ) -> AccessPackageView:
        return EnterpriseEmployeeLifecycleReadService(
            session
        ).query_access_package(package_code)

    @app.get(
        "/internal/v1/employees/{employee_id}/corporate-account",
        response_model=CorporateAccountView | None,
        dependencies=internal_dependencies,
    )
    def query_corporate_account(
        employee_id: str,
        session: Session = Depends(get_session),
    ) -> CorporateAccountView | None:
        return EnterpriseEmployeeLifecycleReadService(
            session
        ).query_corporate_account(employee_id)

    @app.get(
        "/internal/v1/employees/{employee_id}/asset-tasks",
        response_model=list[AssetTaskView],
        dependencies=internal_dependencies,
    )
    def query_asset_tasks(
        employee_id: str,
        session: Session = Depends(get_session),
    ) -> tuple[AssetTaskView, ...]:
        return EnterpriseEmployeeLifecycleReadService(session).query_asset_tasks(
            employee_id
        )

    @app.get(
        "/internal/v1/employees/{employee_id}/lifecycle-requests/open",
        response_model=list[EmployeeLifecycleRequestView],
        dependencies=internal_dependencies,
    )
    def query_open_lifecycle_requests(
        employee_id: str,
        session: Session = Depends(get_session),
    ) -> tuple[EmployeeLifecycleRequestView, ...]:
        return EnterpriseEmployeeLifecycleReadService(
            session
        ).query_open_lifecycle_requests(employee_id)

    @app.get(
        "/internal/v1/employees/{employee_id}/lifecycle-snapshot",
        response_model=EmployeeLifecycleSnapshot,
        dependencies=internal_dependencies,
    )
    def query_employee_lifecycle_snapshot(
        employee_id: str,
        session: Session = Depends(get_session),
    ) -> EmployeeLifecycleSnapshot:
        return EnterpriseEmployeeLifecycleReadService(
            session
        ).query_employee_snapshot(employee_id)

    @app.get(
        "/internal/v1/employee-lifecycle-requests/{request_id}",
        response_model=EmployeeLifecycleRequestView,
        dependencies=internal_dependencies,
    )
    def query_employee_lifecycle_request_by_id(
        request_id: str,
        session: Session = Depends(get_session),
    ) -> EmployeeLifecycleRequestView:
        return EnterpriseEmployeeLifecycleReadService(
            session
        ).query_lifecycle_request(request_id=request_id)

    @app.get(
        "/internal/v1/employee-lifecycle-requests",
        response_model=EmployeeLifecycleRequestView,
        dependencies=internal_dependencies,
    )
    def query_employee_lifecycle_request_by_idempotency(
        idempotency_key: str = Query(min_length=1, max_length=100),
        session: Session = Depends(get_session),
    ) -> EmployeeLifecycleRequestView:
        return EnterpriseEmployeeLifecycleReadService(
            session
        ).query_lifecycle_request(idempotency_key=idempotency_key)

    @app.get(
        "/internal/v1/cost-centers/{cost_center_code}",
        response_model=CostCenterView,
        dependencies=internal_dependencies,
    )
    def query_cost_center(
        cost_center_code: str,
        session: Session = Depends(get_session),
    ) -> CostCenterView:
        return EnterpriseProcurementReadService(session).query_cost_center(
            cost_center_code
        )

    @app.get(
        "/internal/v1/procurement-policies/current",
        response_model=ProcurementPolicyView,
        dependencies=internal_dependencies,
    )
    def query_current_procurement_policy(
        session: Session = Depends(get_session),
    ) -> ProcurementPolicyView:
        return EnterpriseProcurementReadService(
            session
        ).query_current_procurement_policy()

    @app.get(
        "/internal/v1/procurement-policies/{policy_code}",
        response_model=ProcurementPolicyView,
        dependencies=internal_dependencies,
    )
    def query_procurement_policy(
        policy_code: str,
        session: Session = Depends(get_session),
    ) -> ProcurementPolicyView:
        return EnterpriseProcurementReadService(session).query_procurement_policy(
            policy_code
        )

    @app.get(
        "/internal/v1/procurement-requests/open",
        response_model=list[ProcurementRequestView],
        dependencies=internal_dependencies,
    )
    def query_open_procurement_requests(
        requester_id: str = Query(min_length=1, max_length=100),
        cost_center_code: str = Query(min_length=1, max_length=100),
        session: Session = Depends(get_session),
    ) -> tuple[ProcurementRequestView, ...]:
        return EnterpriseProcurementReadService(
            session
        ).query_open_procurement_requests(
            requester_id=requester_id,
            cost_center_code=cost_center_code,
        )

    @app.get(
        "/internal/v1/procurement-requests/by-workflow/{workflow_run_id}",
        response_model=ProcurementRequestView,
        dependencies=internal_dependencies,
    )
    def query_procurement_request_by_workflow(
        workflow_run_id: str,
        session: Session = Depends(get_session),
    ) -> ProcurementRequestView:
        return EnterpriseProcurementReadService(session).query_procurement_request(
            workflow_run_id=workflow_run_id
        )

    @app.get(
        "/internal/v1/procurement-requests/{request_id}",
        response_model=ProcurementRequestView,
        dependencies=internal_dependencies,
    )
    def query_procurement_request(
        request_id: str,
        session: Session = Depends(get_session),
    ) -> ProcurementRequestView:
        return EnterpriseProcurementReadService(session).query_procurement_request(
            request_id=request_id
        )

    @app.get(
        "/internal/v1/budget-reservations/by-request/{request_id}",
        response_model=BudgetReservationView,
        dependencies=internal_dependencies,
    )
    def query_budget_reservation(
        request_id: str,
        session: Session = Depends(get_session),
    ) -> BudgetReservationView:
        return EnterpriseProcurementReadService(session).query_budget_reservation(
            request_id=request_id
        )

    @app.post(
        "/internal/v1/procurement-requests",
        response_model=ProcurementWriteResult,
        dependencies=internal_dependencies,
    )
    def create_procurement_request_and_reserve_budget(
        command: CreateProcurementRequestAndReserveBudgetCommand,
        idempotency_key: str = Header(alias="Idempotency-Key", min_length=1),
        session: Session = Depends(get_session),
    ) -> ProcurementWriteResult:
        return EnterpriseProcurementWriteService(
            session
        ).create_request_and_reserve_budget(
            command,
            idempotency_key=idempotency_key,
        )

    @app.post(
        "/internal/v1/employee-onboarding/pending-employees",
        response_model=EmployeeOnboardingWriteResult,
        dependencies=internal_dependencies,
    )
    def create_pending_employee(
        command: CreatePendingEmployeeCommand,
        idempotency_key: str = Header(alias="Idempotency-Key", min_length=1),
        session: Session = Depends(get_session),
    ) -> EmployeeOnboardingWriteResult:
        return EnterpriseEmployeeOnboardingService(session).create_pending_employee(
            command,
            idempotency_key=idempotency_key,
        )

    @app.post(
        "/internal/v1/employee-onboarding/disabled-accounts",
        response_model=EmployeeOnboardingWriteResult,
        dependencies=internal_dependencies,
    )
    def create_disabled_corporate_account(
        command: CreateDisabledCorporateAccountCommand,
        idempotency_key: str = Header(alias="Idempotency-Key", min_length=1),
        session: Session = Depends(get_session),
    ) -> EmployeeOnboardingWriteResult:
        return EnterpriseEmployeeOnboardingService(
            session
        ).create_disabled_corporate_account(
            command,
            idempotency_key=idempotency_key,
        )

    @app.post(
        "/internal/v1/employee-onboarding/baseline-access",
        response_model=EmployeeOnboardingWriteResult,
        dependencies=internal_dependencies,
    )
    def assign_baseline_access_package(
        command: AssignBaselineAccessPackageCommand,
        idempotency_key: str = Header(alias="Idempotency-Key", min_length=1),
        session: Session = Depends(get_session),
    ) -> EmployeeOnboardingWriteResult:
        return EnterpriseEmployeeOnboardingService(
            session
        ).assign_baseline_access_package(
            command,
            idempotency_key=idempotency_key,
        )

    @app.post(
        "/internal/v1/employee-onboarding/asset-assignment-tasks",
        response_model=EmployeeOnboardingWriteResult,
        dependencies=internal_dependencies,
    )
    def create_asset_assignment_task(
        command: CreateAssetAssignmentTaskCommand,
        idempotency_key: str = Header(alias="Idempotency-Key", min_length=1),
        session: Session = Depends(get_session),
    ) -> EmployeeOnboardingWriteResult:
        return EnterpriseEmployeeOnboardingService(
            session
        ).create_asset_assignment_task(
            command,
            idempotency_key=idempotency_key,
        )

    @app.post(
        "/internal/v1/employee-onboarding/activations",
        response_model=EmployeeOnboardingWriteResult,
        dependencies=internal_dependencies,
    )
    def activate_employee_and_account(
        command: ActivateEmployeeAndAccountCommand,
        idempotency_key: str = Header(alias="Idempotency-Key", min_length=1),
        session: Session = Depends(get_session),
    ) -> EmployeeOnboardingWriteResult:
        return EnterpriseEmployeeOnboardingService(
            session
        ).activate_employee_and_account(
            command,
            idempotency_key=idempotency_key,
        )

    @app.post(
        "/internal/v1/employee-transfer/assignments",
        response_model=EmployeeOnboardingWriteResult,
        dependencies=internal_dependencies,
    )
    def update_employee_assignment(
        command: UpdateEmployeeAssignmentCommand,
        idempotency_key: str = Header(alias="Idempotency-Key", min_length=1),
        session: Session = Depends(get_session),
    ) -> EmployeeOnboardingWriteResult:
        return EnterpriseEmployeeLifecycleWriteService(
            session
        ).update_employee_assignment(command, idempotency_key=idempotency_key)

    @app.post(
        "/internal/v1/employee-transfer/access-revocations",
        response_model=EmployeeOnboardingWriteResult,
        dependencies=internal_dependencies,
    )
    def revoke_obsolete_baseline_access(
        command: ChangeEmployeeAccessCommand,
        idempotency_key: str = Header(alias="Idempotency-Key", min_length=1),
        session: Session = Depends(get_session),
    ) -> EmployeeOnboardingWriteResult:
        return EnterpriseEmployeeLifecycleWriteService(
            session
        ).revoke_obsolete_baseline_access(
            command, idempotency_key=idempotency_key
        )

    @app.post(
        "/internal/v1/employee-transfer/access-grants",
        response_model=EmployeeOnboardingWriteResult,
        dependencies=internal_dependencies,
    )
    def grant_target_baseline_access(
        command: ChangeEmployeeAccessCommand,
        idempotency_key: str = Header(alias="Idempotency-Key", min_length=1),
        session: Session = Depends(get_session),
    ) -> EmployeeOnboardingWriteResult:
        return EnterpriseEmployeeLifecycleWriteService(
            session
        ).grant_target_baseline_access(
            command, idempotency_key=idempotency_key
        )

    @app.post(
        "/internal/v1/employee-transfer/asset-adjustment-tasks",
        response_model=EmployeeOnboardingWriteResult,
        dependencies=internal_dependencies,
    )
    def create_asset_adjustment_task(
        command: CreateAssetAdjustmentTaskCommand,
        idempotency_key: str = Header(alias="Idempotency-Key", min_length=1),
        session: Session = Depends(get_session),
    ) -> EmployeeOnboardingWriteResult:
        return EnterpriseEmployeeLifecycleWriteService(
            session
        ).create_asset_adjustment_task(
            command, idempotency_key=idempotency_key
        )

    @app.post(
        "/internal/v1/employee-transfer/verifications",
        response_model=EmployeeOnboardingWriteResult,
        dependencies=internal_dependencies,
    )
    def verify_employee_transfer_consistency(
        command: VerifyEmployeeTransferCommand,
        idempotency_key: str = Header(alias="Idempotency-Key", min_length=1),
        session: Session = Depends(get_session),
    ) -> EmployeeOnboardingWriteResult:
        return EnterpriseEmployeeLifecycleWriteService(
            session
        ).verify_employee_transfer_consistency(
            command, idempotency_key=idempotency_key
        )

    @app.post(
        "/internal/v1/employee-offboarding/account-disables",
        response_model=EmployeeOnboardingWriteResult,
        dependencies=internal_dependencies,
    )
    def disable_corporate_account(
        command: DisableCorporateAccountCommand,
        idempotency_key: str = Header(alias="Idempotency-Key", min_length=1),
        session: Session = Depends(get_session),
    ) -> EmployeeOnboardingWriteResult:
        return EnterpriseEmployeeLifecycleWriteService(
            session
        ).disable_corporate_account(command, idempotency_key=idempotency_key)

    @app.post(
        "/internal/v1/employee-offboarding/access-revocations",
        response_model=EmployeeOnboardingWriteResult,
        dependencies=internal_dependencies,
    )
    def revoke_all_employee_access(
        command: RevokeAllEmployeeAccessCommand,
        idempotency_key: str = Header(alias="Idempotency-Key", min_length=1),
        session: Session = Depends(get_session),
    ) -> EmployeeOnboardingWriteResult:
        return EnterpriseEmployeeLifecycleWriteService(
            session
        ).revoke_all_employee_access(command, idempotency_key=idempotency_key)

    @app.post(
        "/internal/v1/employee-offboarding/asset-return-tasks",
        response_model=EmployeeOnboardingWriteResult,
        dependencies=internal_dependencies,
    )
    def create_asset_return_task(
        command: CreateAssetReturnTaskCommand,
        idempotency_key: str = Header(alias="Idempotency-Key", min_length=1),
        session: Session = Depends(get_session),
    ) -> EmployeeOnboardingWriteResult:
        return EnterpriseEmployeeLifecycleWriteService(
            session
        ).create_asset_return_task(command, idempotency_key=idempotency_key)

    @app.post(
        "/internal/v1/employee-offboarding/inactive-employees",
        response_model=EmployeeOnboardingWriteResult,
        dependencies=internal_dependencies,
    )
    def mark_employee_inactive(
        command: MarkEmployeeInactiveCommand,
        idempotency_key: str = Header(alias="Idempotency-Key", min_length=1),
        session: Session = Depends(get_session),
    ) -> EmployeeOnboardingWriteResult:
        return EnterpriseEmployeeLifecycleWriteService(
            session
        ).mark_employee_inactive(command, idempotency_key=idempotency_key)

    @app.post(
        "/internal/v1/employee-offboarding/verifications",
        response_model=EmployeeOnboardingWriteResult,
        dependencies=internal_dependencies,
    )
    def verify_employee_offboarding_consistency(
        command: VerifyEmployeeOffboardingCommand,
        idempotency_key: str = Header(alias="Idempotency-Key", min_length=1),
        session: Session = Depends(get_session),
    ) -> EmployeeOnboardingWriteResult:
        return EnterpriseEmployeeLifecycleWriteService(
            session
        ).verify_employee_offboarding_consistency(
            command, idempotency_key=idempotency_key
        )

    @app.get(
        "/internal/v1/applications/{application_code}",
        response_model=ApplicationView,
        dependencies=internal_dependencies,
    )
    def query_application(
        application_code: str,
        session: Session = Depends(get_session),
    ) -> ApplicationView:
        return EnterpriseAccessService(session).query_application(application_code)

    @app.get(
        "/internal/v1/employees/{employee_id}/access",
        response_model=list[UserAccessView],
        dependencies=internal_dependencies,
    )
    def query_user_access(
        employee_id: str,
        session: Session = Depends(get_session),
    ) -> tuple[UserAccessView, ...]:
        return EnterpriseAccessService(session).query_user_access(employee_id)

    @app.get(
        "/internal/v1/access-requests/{request_id}",
        response_model=AccessRequestView,
        dependencies=internal_dependencies,
    )
    def query_access_request(
        request_id: str,
        session: Session = Depends(get_session),
    ) -> AccessRequestView:
        return EnterpriseAccessService(session).query_access_request(request_id)

    @app.get(
        "/internal/v1/equipment/{equipment_code}",
        response_model=EquipmentView,
        dependencies=internal_dependencies,
    )
    def query_equipment(
        equipment_code: str,
        session: Session = Depends(get_session),
    ) -> EquipmentView:
        return EnterpriseEquipmentService(session).query_equipment(equipment_code)

    @app.get(
        "/internal/v1/equipment/{equipment_code}/status",
        response_model=EquipmentStatusView,
        dependencies=internal_dependencies,
    )
    def query_equipment_status(
        equipment_code: str,
        session: Session = Depends(get_session),
    ) -> EquipmentStatusView:
        return EnterpriseEquipmentService(session).query_equipment_status(
            equipment_code
        )

    @app.get(
        "/internal/v1/equipment/{equipment_code}/maintenance-history",
        response_model=list[MaintenanceHistoryView],
        dependencies=internal_dependencies,
    )
    def query_maintenance_history(
        equipment_code: str,
        limit: int = Query(default=10, ge=1, le=50),
        session: Session = Depends(get_session),
    ) -> tuple[MaintenanceHistoryView, ...]:
        return EnterpriseEquipmentService(session).query_maintenance_history(
            equipment_code,
            limit=limit,
        )

    @app.get(
        "/internal/v1/maintenance-work-orders/{work_order_id}",
        response_model=MaintenanceWorkOrderView,
        dependencies=internal_dependencies,
    )
    def query_maintenance_work_order(
        work_order_id: str,
        session: Session = Depends(get_session),
    ) -> MaintenanceWorkOrderView:
        return EnterpriseEquipmentService(session).query_maintenance_work_order(
            work_order_id=work_order_id
        )

    @app.get(
        "/internal/v1/maintenance-work-orders",
        response_model=MaintenanceWorkOrderView,
        dependencies=internal_dependencies,
    )
    def query_maintenance_work_order_by_idempotency(
        idempotency_key: str = Query(min_length=1),
        session: Session = Depends(get_session),
    ) -> MaintenanceWorkOrderView:
        return EnterpriseEquipmentService(session).query_maintenance_work_order(
            idempotency_key=idempotency_key
        )

    @app.post(
        "/internal/v1/maintenance-work-orders",
        response_model=MaintenanceWorkOrderWriteResult,
        dependencies=internal_dependencies,
    )
    def create_maintenance_work_order(
        command: CreateMaintenanceWorkOrderCommand,
        idempotency_key: str = Header(alias="Idempotency-Key", min_length=1),
        session: Session = Depends(get_session),
    ) -> MaintenanceWorkOrderWriteResult:
        return EnterpriseEquipmentService(session).create_maintenance_work_order(
            command,
            idempotency_key=idempotency_key,
        )

    @app.post(
        "/internal/v1/access-requests",
        response_model=EnterpriseWriteResult,
        dependencies=internal_dependencies,
    )
    def create_access_request(
        command: CreateAccessRequestCommand,
        idempotency_key: str = Header(alias="Idempotency-Key"),
        session: Session = Depends(get_session),
    ) -> EnterpriseWriteResult:
        return EnterpriseAccessService(session).create_access_request(
            command, idempotency_key=idempotency_key
        )

    @app.post(
        "/internal/v1/access/grants",
        response_model=EnterpriseWriteResult,
        dependencies=internal_dependencies,
    )
    def grant_application_access(
        command: GrantApplicationAccessCommand,
        idempotency_key: str = Header(alias="Idempotency-Key"),
        session: Session = Depends(get_session),
    ) -> EnterpriseWriteResult:
        return EnterpriseAccessService(session).grant_application_access(
            command, idempotency_key=idempotency_key
        )

    @app.post(
        "/internal/v1/access/verify",
        response_model=AccessVerificationResult,
        dependencies=internal_dependencies,
    )
    def verify_application_access(
        query: VerifyApplicationAccessQuery,
        session: Session = Depends(get_session),
    ) -> AccessVerificationResult:
        return EnterpriseAccessService(session).verify_application_access(query)

    return app


def create_runtime_app() -> FastAPI:
    """Uvicorn factory using environment-only connection and credential settings."""
    database_url = os.getenv("ENTERPRISE_DATABASE_URL", "")
    internal_token = os.getenv("ENTERPRISE_INTERNAL_TOKEN", "")
    if not database_url.strip():
        raise RuntimeError("ENTERPRISE_DATABASE_URL must be configured")
    upgrade_enterprise_schema(database_url)
    engine = build_enterprise_engine(database_url)
    factory = build_enterprise_session_factory(engine)
    with factory.begin() as session:
        seed_demo_data(session)
    return create_app(factory, internal_token=internal_token)
    AssetTaskView,
    CorporateAccountView,
    JobProfileView,
