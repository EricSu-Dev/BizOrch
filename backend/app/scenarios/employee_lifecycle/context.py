"""Capability-restricted enterprise fact resolution for employee lifecycle."""

from typing import Protocol

from app.integrations.enterprise_ops import (
    EnterpriseOpsAccessPackage,
    EnterpriseOpsEmployeeLifecycleProfile,
    EnterpriseOpsEmployeeLifecycleRequest,
    EnterpriseOpsJobProfile,
    EnterpriseOpsOrganizationUnit,
    EnterpriseOpsWorkLocation,
)
from app.scenarios.employee_lifecycle.contracts import (
    EmployeeLifecycleContext,
    EmployeeLifecycleRequestDraft,
    EmployeeLifecycleRequestType,
)


class EmployeeLifecycleContextClientPort(Protocol):
    """Only the read capabilities granted to the lifecycle Domain Agent."""

    def query_employee_lifecycle_profile(
        self,
        employee_id: str,
    ) -> EnterpriseOpsEmployeeLifecycleProfile | None: ...

    def query_organization_unit(
        self,
        department_code: str,
    ) -> EnterpriseOpsOrganizationUnit | None: ...

    def query_job_profile(
        self,
        job_code: str,
    ) -> EnterpriseOpsJobProfile | None: ...

    def query_work_location(
        self,
        location_code: str,
    ) -> EnterpriseOpsWorkLocation | None: ...

    def query_access_package(
        self,
        package_code: str,
    ) -> EnterpriseOpsAccessPackage | None: ...

    def query_open_employee_lifecycle_request(
        self,
        employee_id: str,
    ) -> tuple[EnterpriseOpsEmployeeLifecycleRequest, ...]: ...


class EmployeeLifecycleContextResolver:
    """Resolve available facts without exposing an employee write capability."""

    def __init__(self, client: EmployeeLifecycleContextClientPort) -> None:
        self._client = client

    def resolve(
        self,
        draft: EmployeeLifecycleRequestDraft,
    ) -> EmployeeLifecycleContext:
        subject = (
            self._client.query_employee_lifecycle_profile(
                draft.subject_employee_id
            )
            if draft.subject_employee_id
            else None
        )
        department = (
            self._client.query_organization_unit(
                draft.target_department_code
            )
            if draft.target_department_code
            else None
        )
        job = (
            self._client.query_job_profile(draft.target_job_code)
            if draft.target_job_code
            else None
        )
        location_code = draft.work_location_code or (
            subject.employee.work_location_code
            if (
                subject is not None
                and draft.request_type is EmployeeLifecycleRequestType.TRANSFER
            )
            else None
        )
        work_location = (
            self._client.query_work_location(location_code)
            if location_code
            else None
        )
        manager = (
            self._client.query_employee_lifecycle_profile(
                draft.target_manager_id
            )
            if draft.target_manager_id
            else None
        )
        approval_manager_id = (
            draft.target_manager_id
            if draft.request_type.value in {"ONBOARDING", "TRANSFER"}
            else (
                subject.employee.manager_id
                if subject is not None
                else None
            )
        )
        approval_manager = (
            manager
            if approval_manager_id == draft.target_manager_id
            else (
                self._client.query_employee_lifecycle_profile(
                    approval_manager_id
                )
                if approval_manager_id
                else None
            )
        )
        access_package = (
            self._client.query_access_package(
                job.baseline_access_package_code
            )
            if job
            else None
        )
        open_requests = (
            self._client.query_open_employee_lifecycle_request(
                draft.subject_employee_id
            )
            if draft.subject_employee_id
            else ()
        )
        return EmployeeLifecycleContext(
            subject_profile=subject,
            target_department=department,
            target_job=job,
            work_location=work_location,
            target_manager_profile=manager,
            approval_manager_profile=approval_manager,
            baseline_access_package=access_package,
            open_requests=open_requests,
        )
