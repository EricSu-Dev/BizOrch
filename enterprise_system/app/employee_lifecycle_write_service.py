"""Deterministic transfer and offboarding writes for employee lifecycle."""

from __future__ import annotations

from uuid import NAMESPACE_URL, uuid5

from sqlalchemy import select

from enterprise_system.app.contracts import (
    AssetTaskStatus,
    AssetTaskType,
    ChangeEmployeeAccessCommand,
    CorporateAccountStatus,
    CreateAssetAdjustmentTaskCommand,
    CreateAssetReturnTaskCommand,
    DisableCorporateAccountCommand,
    EmployeeLifecycleRequestStatus,
    EmployeeLifecycleRequestType,
    EmployeeLifecycleWriteResult,
    EmploymentStatus,
    MarkEmployeeInactiveCommand,
    RevokeAllEmployeeAccessCommand,
    UpdateEmployeeAssignmentCommand,
    VerifyEmployeeOffboardingCommand,
    VerifyEmployeeTransferCommand,
)
from enterprise_system.app.employee_onboarding_service import (
    EnterpriseEmployeeOnboardingService,
    _BASELINE_EXPIRES_AT,
)
from enterprise_system.app.models import (
    AccessPackageRecord,
    ApplicationRecord,
    AssetTaskRecord,
    CorporateAccountRecord,
    EmployeeLifecycleRequestRecord,
    EmployeeRecord,
    JobProfileRecord,
    OrganizationUnitRecord,
    UserAccessRecord,
    WorkLocationRecord,
)
from enterprise_system.app.service import (
    EnterpriseResourceNotFoundError,
    EnterpriseRuleViolationError,
)


class EnterpriseEmployeeLifecycleWriteService(EnterpriseEmployeeOnboardingService):
    """Extend the onboarding boundary with reviewed transfer/offboarding steps."""

    def update_employee_assignment(
        self,
        command: UpdateEmployeeAssignmentCommand,
        *,
        idempotency_key: str,
    ) -> EmployeeLifecycleWriteResult:
        action = "update_employee_assignment"
        replay = self._find_replay(idempotency_key, action, command)
        if replay is not None:
            return replay
        employee = self._require_active_employee(command.employee_id)
        if employee.version != command.expected_employee_version:
            raise EnterpriseRuleViolationError("employee version is stale")
        department = self._require_versioned(
            OrganizationUnitRecord,
            command.department_code,
            command.expected_department_version,
            "department",
        )
        job = self._require_versioned(
            JobProfileRecord,
            command.job_code,
            command.expected_job_version,
            "job",
        )
        location = self._require_versioned(
            WorkLocationRecord,
            command.work_location_code,
            command.expected_work_location_version,
            "work location",
        )
        manager = self._require_active_employee(command.manager_id)
        if (
            not department.active
            or not job.active
            or not location.active
            or job.department_code != department.department_code
            or manager.department_code != department.department_code
        ):
            raise EnterpriseRuleViolationError(
                "transfer organization facts are no longer executable"
            )
        employee.department_code = command.department_code
        employee.job_code = command.job_code
        employee.manager_id = command.manager_id
        employee.work_location_code = command.work_location_code
        employee.version += 1
        request = self._create_lifecycle_request(
            request_type=EmployeeLifecycleRequestType.TRANSFER,
            employee_id=command.employee_id,
            initiator_id=command.initiator_id,
            effective_date=command.effective_date,
            idempotency_key=idempotency_key,
        )
        result = EmployeeLifecycleWriteResult(
            action_type=action,
            employee_id=command.employee_id,
            resource_id=request.request_id,
            employee_version=employee.version,
        )
        return self._save_result(idempotency_key, action, command, result)

    def revoke_obsolete_baseline_access(
        self,
        command: ChangeEmployeeAccessCommand,
        *,
        idempotency_key: str,
    ) -> EmployeeLifecycleWriteResult:
        action = "revoke_obsolete_baseline_access"
        replay = self._find_replay(idempotency_key, action, command)
        if replay is not None:
            return replay
        employee = self._require_active_employee(command.employee_id)
        requested = {
            (str(item["application_code"]), str(item["role_code"]))
            for item in command.role_bindings
        }
        records = self._active_access(command.employee_id)
        by_binding = {
            (record.application_code, record.role_code): record for record in records
        }
        if not requested.issubset(by_binding):
            raise EnterpriseRuleViolationError(
                "approved obsolete access no longer matches active access"
            )
        for binding in requested:
            by_binding[binding].active = False
        result = EmployeeLifecycleWriteResult(
            action_type=action,
            employee_id=command.employee_id,
            resource_id=",".join(
                sorted(by_binding[binding].access_id for binding in requested)
            )
            or command.employee_id,
            employee_version=employee.version,
        )
        return self._save_result(idempotency_key, action, command, result)

    def grant_target_baseline_access(
        self,
        command: ChangeEmployeeAccessCommand,
        *,
        idempotency_key: str,
    ) -> EmployeeLifecycleWriteResult:
        action = "grant_target_baseline_access"
        replay = self._find_replay(idempotency_key, action, command)
        if replay is not None:
            return replay
        employee = self._require_active_employee(command.employee_id)
        if command.package_code is None or command.package_version is None:
            raise EnterpriseRuleViolationError("target access package is required")
        package = self._require_versioned(
            AccessPackageRecord,
            command.package_code,
            command.package_version,
            "access package",
        )
        job = self._session.get(JobProfileRecord, employee.job_code)
        allowed = {
            (str(item["application_code"]), str(item["role_code"]))
            for item in package.role_bindings
        }
        requested = {
            (str(item["application_code"]), str(item["role_code"]))
            for item in command.role_bindings
        }
        if (
            not package.active
            or job is None
            or job.baseline_access_package_code != command.package_code
            or not requested.issubset(allowed)
        ):
            raise EnterpriseRuleViolationError(
                "target access package no longer matches employee job"
            )
        access_ids: list[str] = []
        for application_code, role_code in sorted(requested):
            application = self._session.get(ApplicationRecord, application_code)
            if (
                application is None
                or not application.active
                or role_code not in application.allowed_role_codes
            ):
                raise EnterpriseRuleViolationError(
                    "target access binding is no longer allowed"
                )
            record = self._session.scalar(
                select(UserAccessRecord).where(
                    UserAccessRecord.employee_id == command.employee_id,
                    UserAccessRecord.application_code == application_code,
                    UserAccessRecord.role_code == role_code,
                )
            )
            if record is None:
                record = UserAccessRecord(
                    access_id=str(
                        uuid5(
                            NAMESPACE_URL,
                            "bizorch:baseline-access:"
                            f"{command.employee_id}:{application_code}:{role_code}",
                        )
                    ),
                    employee_id=command.employee_id,
                    application_code=application_code,
                    role_code=role_code,
                    expires_at=_BASELINE_EXPIRES_AT,
                    active=True,
                )
                self._session.add(record)
            else:
                record.active = True
                record.expires_at = _BASELINE_EXPIRES_AT
            access_ids.append(record.access_id)
        result = EmployeeLifecycleWriteResult(
            action_type=action,
            employee_id=command.employee_id,
            resource_id=",".join(access_ids) or command.package_code,
            employee_version=employee.version,
        )
        return self._save_result(idempotency_key, action, command, result)

    def create_asset_adjustment_task(
        self,
        command: CreateAssetAdjustmentTaskCommand,
        *,
        idempotency_key: str,
    ) -> EmployeeLifecycleWriteResult:
        action = "create_asset_adjustment_task"
        replay = self._find_replay(idempotency_key, action, command)
        if replay is not None:
            return replay
        employee = self._require_active_employee(command.employee_id)
        job = self._session.get(JobProfileRecord, employee.job_code)
        if (
            job is None
            or job.asset_profile_code != command.asset_profile_code
            or command.task_type is not AssetTaskType.ADJUST
        ):
            raise EnterpriseRuleViolationError(
                "asset adjustment no longer matches employee job"
            )
        task = self._create_asset_task(
            employee_id=command.employee_id,
            task_type=AssetTaskType.ADJUST,
            asset_profile_code=command.asset_profile_code,
            idempotency_key=idempotency_key,
        )
        result = EmployeeLifecycleWriteResult(
            action_type=action,
            employee_id=command.employee_id,
            resource_id=task.task_id,
            employee_version=employee.version,
        )
        return self._save_result(idempotency_key, action, command, result)

    def verify_employee_transfer_consistency(
        self,
        command: VerifyEmployeeTransferCommand,
        *,
        idempotency_key: str,
    ) -> EmployeeLifecycleWriteResult:
        action = "verify_employee_transfer_consistency"
        replay = self._find_replay(idempotency_key, action, command)
        if replay is not None:
            return replay
        employee = self._require_active_employee(command.employee_id)
        expected = (
            command.expected_department_code,
            command.expected_job_code,
            command.expected_manager_id,
            command.expected_work_location_code,
        )
        actual = (
            employee.department_code,
            employee.job_code,
            employee.manager_id,
            employee.work_location_code,
        )
        job = self._session.get(JobProfileRecord, employee.job_code)
        package = (
            self._session.get(AccessPackageRecord, job.baseline_access_package_code)
            if job
            else None
        )
        expected_access = {
            (str(item["application_code"]), str(item["role_code"]))
            for item in (package.role_bindings if package else [])
        }
        actual_access = {
            (record.application_code, record.role_code)
            for record in self._active_access(command.employee_id)
        }
        adjustment = self._session.scalar(
            select(AssetTaskRecord).where(
                AssetTaskRecord.employee_id == command.employee_id,
                AssetTaskRecord.task_type == AssetTaskType.ADJUST.value,
                AssetTaskRecord.status == AssetTaskStatus.OPEN.value,
                AssetTaskRecord.asset_profile_code
                == (job.asset_profile_code if job else ""),
            )
        )
        if actual != expected or actual_access != expected_access or adjustment is None:
            raise EnterpriseRuleViolationError(
                "employee transfer consistency verification failed"
            )
        request = self._complete_lifecycle_request(
            command.employee_id, EmployeeLifecycleRequestType.TRANSFER
        )
        result = EmployeeLifecycleWriteResult(
            action_type=action,
            employee_id=command.employee_id,
            resource_id=request.request_id,
            employee_version=employee.version,
        )
        return self._save_result(idempotency_key, action, command, result)

    def disable_corporate_account(
        self,
        command: DisableCorporateAccountCommand,
        *,
        idempotency_key: str,
    ) -> EmployeeLifecycleWriteResult:
        action = "disable_corporate_account"
        replay = self._find_replay(idempotency_key, action, command)
        if replay is not None:
            return replay
        employee = self._require_active_employee(command.employee_id)
        account = self._session.scalar(
            select(CorporateAccountRecord).where(
                CorporateAccountRecord.employee_id == command.employee_id
            )
        )
        if command.expected_account_version is None:
            if account is not None:
                raise EnterpriseRuleViolationError(
                    "account appeared after offboarding approval"
                )
        else:
            if (
                account is None
                or account.version != command.expected_account_version
            ):
                raise EnterpriseRuleViolationError("account version is stale")
            if account.status != CorporateAccountStatus.DISABLED.value:
                account.status = CorporateAccountStatus.DISABLED.value
                account.version += 1
        request = self._create_lifecycle_request(
            request_type=EmployeeLifecycleRequestType.OFFBOARDING,
            employee_id=command.employee_id,
            initiator_id=command.initiator_id,
            effective_date=command.effective_date,
            idempotency_key=idempotency_key,
        )
        result = EmployeeLifecycleWriteResult(
            action_type=action,
            employee_id=command.employee_id,
            resource_id=account.account_id if account else request.request_id,
            employee_version=employee.version,
            account_version=account.version if account else None,
        )
        return self._save_result(idempotency_key, action, command, result)

    def revoke_all_employee_access(
        self,
        command: RevokeAllEmployeeAccessCommand,
        *,
        idempotency_key: str,
    ) -> EmployeeLifecycleWriteResult:
        action = "revoke_all_employee_access"
        replay = self._find_replay(idempotency_key, action, command)
        if replay is not None:
            return replay
        employee = self._require_active_employee(command.employee_id)
        records = self._active_access(command.employee_id)
        actual_ids = {record.access_id for record in records}
        if actual_ids != set(command.access_ids):
            raise EnterpriseRuleViolationError(
                "active access changed after offboarding approval"
            )
        for record in records:
            record.active = False
        result = EmployeeLifecycleWriteResult(
            action_type=action,
            employee_id=command.employee_id,
            resource_id=",".join(sorted(actual_ids)) or command.employee_id,
            employee_version=employee.version,
        )
        return self._save_result(idempotency_key, action, command, result)

    def create_asset_return_task(
        self,
        command: CreateAssetReturnTaskCommand,
        *,
        idempotency_key: str,
    ) -> EmployeeLifecycleWriteResult:
        action = "create_asset_return_task"
        replay = self._find_replay(idempotency_key, action, command)
        if replay is not None:
            return replay
        employee = self._require_active_employee(command.employee_id)
        actual_task_ids = set(
            self._session.scalars(
                select(AssetTaskRecord.task_id).where(
                    AssetTaskRecord.employee_id == command.employee_id
                )
            )
        )
        if actual_task_ids != set(command.existing_asset_task_ids):
            raise EnterpriseRuleViolationError(
                "employee asset tasks changed after offboarding approval"
            )
        if command.task_type is not AssetTaskType.RETURN:
            raise EnterpriseRuleViolationError("offboarding requires a return task")
        job = self._session.get(JobProfileRecord, employee.job_code)
        if job is None:
            raise EnterpriseResourceNotFoundError(f"job:{employee.job_code}")
        task = self._create_asset_task(
            employee_id=command.employee_id,
            task_type=AssetTaskType.RETURN,
            asset_profile_code=job.asset_profile_code,
            idempotency_key=idempotency_key,
        )
        result = EmployeeLifecycleWriteResult(
            action_type=action,
            employee_id=command.employee_id,
            resource_id=task.task_id,
            employee_version=employee.version,
        )
        return self._save_result(idempotency_key, action, command, result)

    def mark_employee_inactive(
        self,
        command: MarkEmployeeInactiveCommand,
        *,
        idempotency_key: str,
    ) -> EmployeeLifecycleWriteResult:
        action = "mark_employee_inactive"
        replay = self._find_replay(idempotency_key, action, command)
        if replay is not None:
            return replay
        employee = self._require_active_employee(command.employee_id)
        if (
            employee.version != command.expected_employee_version
            or command.target_status is not EmploymentStatus.INACTIVE
        ):
            raise EnterpriseRuleViolationError(
                "employee version or target status is invalid"
            )
        account = self._session.scalar(
            select(CorporateAccountRecord).where(
                CorporateAccountRecord.employee_id == command.employee_id
            )
        )
        if account is not None and account.status != CorporateAccountStatus.DISABLED.value:
            raise EnterpriseRuleViolationError("account must be disabled first")
        if self._active_access(command.employee_id):
            raise EnterpriseRuleViolationError("employee access must be revoked first")
        employee.active = False
        employee.employment_status = EmploymentStatus.INACTIVE.value
        employee.version += 1
        result = EmployeeLifecycleWriteResult(
            action_type=action,
            employee_id=command.employee_id,
            resource_id=command.employee_id,
            employee_version=employee.version,
            account_version=account.version if account else None,
        )
        return self._save_result(idempotency_key, action, command, result)

    def verify_employee_offboarding_consistency(
        self,
        command: VerifyEmployeeOffboardingCommand,
        *,
        idempotency_key: str,
    ) -> EmployeeLifecycleWriteResult:
        action = "verify_employee_offboarding_consistency"
        replay = self._find_replay(idempotency_key, action, command)
        if replay is not None:
            return replay
        employee = self._session.get(EmployeeRecord, command.employee_id)
        if employee is None:
            raise EnterpriseResourceNotFoundError(f"employee:{command.employee_id}")
        account = self._session.scalar(
            select(CorporateAccountRecord).where(
                CorporateAccountRecord.employee_id == command.employee_id
            )
        )
        return_task = self._session.scalar(
            select(AssetTaskRecord).where(
                AssetTaskRecord.employee_id == command.employee_id,
                AssetTaskRecord.task_type == AssetTaskType.RETURN.value,
                AssetTaskRecord.status == AssetTaskStatus.OPEN.value,
            )
        )
        if (
            employee.active
            or employee.employment_status != command.expected_employee_status.value
            or (
                account is not None
                and account.status != command.expected_account_status.value
            )
            or len(self._active_access(command.employee_id))
            != command.expected_active_access_count
            or return_task is None
        ):
            raise EnterpriseRuleViolationError(
                "employee offboarding consistency verification failed"
            )
        request = self._complete_lifecycle_request(
            command.employee_id, EmployeeLifecycleRequestType.OFFBOARDING
        )
        result = EmployeeLifecycleWriteResult(
            action_type=action,
            employee_id=command.employee_id,
            resource_id=request.request_id,
            employee_version=employee.version,
            account_version=account.version if account else None,
        )
        return self._save_result(idempotency_key, action, command, result)

    def _require_active_employee(self, employee_id: str) -> EmployeeRecord:
        employee = self._session.get(EmployeeRecord, employee_id)
        if employee is None:
            raise EnterpriseResourceNotFoundError(f"employee:{employee_id}")
        if not employee.active or employee.employment_status != EmploymentStatus.ACTIVE.value:
            raise EnterpriseRuleViolationError("employee is not active")
        return employee

    def _active_access(self, employee_id: str) -> list[UserAccessRecord]:
        return list(
            self._session.scalars(
                select(UserAccessRecord).where(
                    UserAccessRecord.employee_id == employee_id,
                    UserAccessRecord.active.is_(True),
                )
            )
        )

    def _create_asset_task(
        self,
        *,
        employee_id: str,
        task_type: AssetTaskType,
        asset_profile_code: str,
        idempotency_key: str,
    ) -> AssetTaskRecord:
        task = AssetTaskRecord(
            task_id=str(uuid5(NAMESPACE_URL, f"bizorch:asset-task:{idempotency_key}")),
            employee_id=employee_id,
            task_type=task_type.value,
            asset_profile_code=asset_profile_code,
            status=AssetTaskStatus.OPEN.value,
            idempotency_key=idempotency_key,
        )
        self._session.add(task)
        return task

    def _create_lifecycle_request(
        self,
        *,
        request_type: EmployeeLifecycleRequestType,
        employee_id: str,
        initiator_id: str,
        effective_date,
        idempotency_key: str,
    ) -> EmployeeLifecycleRequestRecord:
        existing = self._session.scalar(
            select(EmployeeLifecycleRequestRecord).where(
                EmployeeLifecycleRequestRecord.subject_employee_id == employee_id,
                EmployeeLifecycleRequestRecord.status.in_(
                    {
                        EmployeeLifecycleRequestStatus.PENDING_APPROVAL.value,
                        EmployeeLifecycleRequestStatus.EXECUTING.value,
                        EmployeeLifecycleRequestStatus.WAITING_HUMAN.value,
                    }
                ),
            )
        )
        if existing is not None:
            raise EnterpriseRuleViolationError(
                "employee already has an open lifecycle request"
            )
        request = EmployeeLifecycleRequestRecord(
            request_id=str(
                uuid5(
                    NAMESPACE_URL,
                    f"bizorch:{request_type.value.lower()}-request:{idempotency_key}",
                )
            ),
            request_type=request_type.value,
            subject_employee_id=employee_id,
            initiator_id=initiator_id,
            status=EmployeeLifecycleRequestStatus.EXECUTING.value,
            effective_date=effective_date,
            safe_summary=f"员工 {employee_id} {request_type.value} 协同",
            idempotency_key=idempotency_key,
        )
        self._session.add(request)
        return request

    def _complete_lifecycle_request(
        self,
        employee_id: str,
        request_type: EmployeeLifecycleRequestType,
    ) -> EmployeeLifecycleRequestRecord:
        request = self._session.scalar(
            select(EmployeeLifecycleRequestRecord).where(
                EmployeeLifecycleRequestRecord.subject_employee_id == employee_id,
                EmployeeLifecycleRequestRecord.request_type == request_type.value,
                EmployeeLifecycleRequestRecord.status
                == EmployeeLifecycleRequestStatus.EXECUTING.value,
            )
        )
        if request is None:
            raise EnterpriseRuleViolationError(
                "executing employee lifecycle request is missing"
            )
        request.status = EmployeeLifecycleRequestStatus.COMPLETED.value
        return request
