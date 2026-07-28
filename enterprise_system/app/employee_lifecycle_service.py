"""Read-only employee lifecycle facts owned by the simulated enterprise."""

from sqlalchemy import select
from sqlalchemy.orm import Session

from enterprise_system.app.contracts import (
    AccessPackageView,
    AssetTaskStatus,
    AssetTaskType,
    AssetTaskView,
    CorporateAccountStatus,
    CorporateAccountView,
    EmployeeLifecycleRequestStatus,
    EmployeeLifecycleRequestType,
    EmployeeLifecycleRequestView,
    EmployeeLifecycleSnapshot,
    JobProfileView,
    OrganizationUnitView,
    WorkLocationView,
)
from enterprise_system.app.models import (
    AccessPackageRecord,
    AssetTaskRecord,
    CorporateAccountRecord,
    EmployeeLifecycleRequestRecord,
    JobProfileRecord,
    OrganizationUnitRecord,
    WorkLocationRecord,
)
from enterprise_system.app.service import (
    EnterpriseAccessService,
    EnterpriseResourceNotFoundError,
)

_OPEN_LIFECYCLE_STATUSES = (
    EmployeeLifecycleRequestStatus.PENDING_APPROVAL.value,
    EmployeeLifecycleRequestStatus.EXECUTING.value,
    EmployeeLifecycleRequestStatus.WAITING_HUMAN.value,
)


class EnterpriseEmployeeLifecycleReadService:
    """Return authoritative HR, account, baseline and conflict-check facts."""

    def __init__(self, session: Session) -> None:
        self._session = session
        self._access = EnterpriseAccessService(session)

    def query_organization_unit(self, department_code: str) -> OrganizationUnitView:
        record = self._session.get(OrganizationUnitRecord, department_code)
        if record is None:
            raise EnterpriseResourceNotFoundError(
                f"organization_unit:{department_code}"
            )
        return OrganizationUnitView(
            department_code=record.department_code,
            display_name=record.display_name,
            manager_id=record.manager_id,
            active=record.active,
            version=record.version,
        )

    def query_job_profile(self, job_code: str) -> JobProfileView:
        record = self._session.get(JobProfileRecord, job_code)
        if record is None:
            raise EnterpriseResourceNotFoundError(f"job_profile:{job_code}")
        return JobProfileView(
            job_code=record.job_code,
            display_name=record.display_name,
            department_code=record.department_code,
            baseline_access_package_code=record.baseline_access_package_code,
            asset_profile_code=record.asset_profile_code,
            active=record.active,
            version=record.version,
        )

    def query_work_location(self, location_code: str) -> WorkLocationView:
        record = self._session.get(WorkLocationRecord, location_code)
        if record is None:
            raise EnterpriseResourceNotFoundError(
                f"work_location:{location_code}"
            )
        return WorkLocationView(
            location_code=record.location_code,
            display_name=record.display_name,
            active=record.active,
            version=record.version,
        )

    def query_access_package(self, package_code: str) -> AccessPackageView:
        record = self._session.get(AccessPackageRecord, package_code)
        if record is None:
            raise EnterpriseResourceNotFoundError(
                f"access_package:{package_code}"
            )
        return AccessPackageView(
            package_code=record.package_code,
            display_name=record.display_name,
            role_bindings=tuple(record.role_bindings),
            version=record.version,
            active=record.active,
        )

    def query_job_access_baseline(self, job_code: str) -> AccessPackageView:
        job = self.query_job_profile(job_code)
        return self.query_access_package(job.baseline_access_package_code)

    def query_corporate_account(
        self,
        employee_id: str,
    ) -> CorporateAccountView | None:
        record = self._session.scalar(
            select(CorporateAccountRecord).where(
                CorporateAccountRecord.employee_id == employee_id
            )
        )
        if record is None:
            return None
        return CorporateAccountView(
            account_id=record.account_id,
            employee_id=record.employee_id,
            username=record.username,
            status=CorporateAccountStatus(record.status),
            version=record.version,
            updated_at=record.updated_at,
        )

    def query_asset_tasks(self, employee_id: str) -> tuple[AssetTaskView, ...]:
        self._access.query_employee(employee_id)
        records = self._session.scalars(
            select(AssetTaskRecord)
            .where(AssetTaskRecord.employee_id == employee_id)
            .order_by(AssetTaskRecord.created_at, AssetTaskRecord.task_id)
        )
        return tuple(self._asset_task_view(record) for record in records)

    def query_open_lifecycle_requests(
        self,
        employee_id: str,
    ) -> tuple[EmployeeLifecycleRequestView, ...]:
        records = self._session.scalars(
            select(EmployeeLifecycleRequestRecord)
            .where(
                EmployeeLifecycleRequestRecord.subject_employee_id == employee_id,
                EmployeeLifecycleRequestRecord.status.in_(
                    _OPEN_LIFECYCLE_STATUSES
                ),
            )
            .order_by(
                EmployeeLifecycleRequestRecord.created_at,
                EmployeeLifecycleRequestRecord.request_id,
            )
        )
        return tuple(self._lifecycle_request_view(record) for record in records)

    def query_lifecycle_request(
        self,
        *,
        request_id: str | None = None,
        idempotency_key: str | None = None,
    ) -> EmployeeLifecycleRequestView:
        if bool(request_id) == bool(idempotency_key):
            raise ValueError(
                "provide exactly one of request_id or idempotency_key"
            )
        record = (
            self._session.get(EmployeeLifecycleRequestRecord, request_id)
            if request_id
            else self._session.scalar(
                select(EmployeeLifecycleRequestRecord).where(
                    EmployeeLifecycleRequestRecord.idempotency_key
                    == idempotency_key
                )
            )
        )
        if record is None:
            reference = request_id or idempotency_key
            raise EnterpriseResourceNotFoundError(
                f"employee_lifecycle_request:{reference}"
            )
        return self._lifecycle_request_view(record)

    def query_employee_snapshot(
        self,
        employee_id: str,
    ) -> EmployeeLifecycleSnapshot:
        employee = self._access.query_employee(employee_id)
        return EmployeeLifecycleSnapshot(
            employee=employee,
            department=self.query_organization_unit(employee.department_code),
            job=self.query_job_profile(employee.job_code),
            account=self.query_corporate_account(employee_id),
            active_access=tuple(
                access
                for access in self._access.query_user_access(employee_id)
                if access.active
            ),
            asset_tasks=self.query_asset_tasks(employee_id),
            open_lifecycle_requests=self.query_open_lifecycle_requests(employee_id),
        )

    @staticmethod
    def _asset_task_view(record: AssetTaskRecord) -> AssetTaskView:
        return AssetTaskView(
            task_id=record.task_id,
            employee_id=record.employee_id,
            task_type=AssetTaskType(record.task_type),
            asset_profile_code=record.asset_profile_code,
            status=AssetTaskStatus(record.status),
            created_at=record.created_at,
            updated_at=record.updated_at,
        )

    @staticmethod
    def _lifecycle_request_view(
        record: EmployeeLifecycleRequestRecord,
    ) -> EmployeeLifecycleRequestView:
        return EmployeeLifecycleRequestView(
            request_id=record.request_id,
            request_type=EmployeeLifecycleRequestType(record.request_type),
            subject_employee_id=record.subject_employee_id,
            initiator_id=record.initiator_id,
            status=EmployeeLifecycleRequestStatus(record.status),
            effective_date=record.effective_date,
            safe_summary=record.safe_summary,
            created_at=record.created_at,
            updated_at=record.updated_at,
        )
