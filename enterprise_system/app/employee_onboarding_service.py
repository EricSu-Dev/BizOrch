"""Deterministic and idempotent write boundary for employee onboarding."""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from uuid import NAMESPACE_URL, uuid5

from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from enterprise_system.app.contracts import (
    ActivateEmployeeAndAccountCommand,
    AssignBaselineAccessPackageCommand,
    AssetTaskStatus,
    AssetTaskType,
    CorporateAccountStatus,
    CreateAssetAssignmentTaskCommand,
    CreateDisabledCorporateAccountCommand,
    CreatePendingEmployeeCommand,
    EmployeeLifecycleRequestStatus,
    EmployeeLifecycleRequestType,
    EmployeeOnboardingWriteResult,
    EmploymentStatus,
)
from enterprise_system.app.models import (
    AccessPackageRecord,
    ApplicationRecord,
    AssetTaskRecord,
    CorporateAccountRecord,
    EmployeeLifecycleRequestRecord,
    EmployeeRecord,
    ExternalIdempotencyRecord,
    JobProfileRecord,
    OrganizationUnitRecord,
    UserAccessRecord,
    WorkLocationRecord,
)
from enterprise_system.app.service import (
    EnterpriseIdempotencyConflictError,
    EnterpriseResourceNotFoundError,
    EnterpriseRuleViolationError,
)


_BASELINE_EXPIRES_AT = datetime(2099, 12, 31, 23, 59, 59, tzinfo=UTC)


class EnterpriseEmployeeOnboardingService:
    """Apply the five reviewed onboarding actions as separate transactions."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def create_pending_employee(
        self,
        command: CreatePendingEmployeeCommand,
        *,
        idempotency_key: str,
    ) -> EmployeeOnboardingWriteResult:
        action_type = "create_pending_employee"
        replay = self._find_replay(idempotency_key, action_type, command)
        if replay is not None:
            return replay
        if self._session.get(EmployeeRecord, command.employee_id) is not None:
            raise EnterpriseRuleViolationError("employee already exists")
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
        self._require_versioned(
            WorkLocationRecord,
            command.work_location_code,
            command.expected_work_location_version,
            "work location",
        )
        manager = self._session.get(EmployeeRecord, command.manager_id)
        if (
            not department.active
            or not job.active
            or job.department_code != command.department_code
            or manager is None
            or manager.employment_status != EmploymentStatus.ACTIVE.value
            or manager.department_code != command.department_code
        ):
            raise EnterpriseRuleViolationError(
                "onboarding organization facts are no longer executable"
            )
        employee = EmployeeRecord(
            employee_id=command.employee_id,
            display_name=command.display_name,
            department_code=command.department_code,
            manager_id=command.manager_id,
            active=False,
            employment_status=EmploymentStatus.PENDING_ONBOARDING.value,
            job_code=command.job_code,
            work_location_code=command.work_location_code,
            version=1,
        )
        request_id = str(
            uuid5(NAMESPACE_URL, f"bizorch:onboarding-request:{idempotency_key}")
        )
        self._session.add(employee)
        self._session.add(
            EmployeeLifecycleRequestRecord(
                request_id=request_id,
                request_type=EmployeeLifecycleRequestType.ONBOARDING.value,
                subject_employee_id=command.employee_id,
                initiator_id=command.initiator_id,
                status=EmployeeLifecycleRequestStatus.EXECUTING.value,
                effective_date=command.effective_date,
                safe_summary=f"员工 {command.employee_id} 入职协同",
                idempotency_key=idempotency_key,
            )
        )
        result = EmployeeOnboardingWriteResult(
            action_type=action_type,
            employee_id=command.employee_id,
            resource_id=command.employee_id,
            employee_version=1,
        )
        return self._save_result(idempotency_key, action_type, command, result)

    def create_disabled_corporate_account(
        self,
        command: CreateDisabledCorporateAccountCommand,
        *,
        idempotency_key: str,
    ) -> EmployeeOnboardingWriteResult:
        action_type = "create_disabled_corporate_account"
        replay = self._find_replay(idempotency_key, action_type, command)
        if replay is not None:
            return replay
        employee = self._require_pending_employee(command.employee_id)
        existing = self._session.scalar(
            select(CorporateAccountRecord).where(
                CorporateAccountRecord.employee_id == command.employee_id
            )
        )
        if existing is not None:
            raise EnterpriseRuleViolationError("corporate account already exists")
        account_id = str(
            uuid5(NAMESPACE_URL, f"bizorch:corporate-account:{command.employee_id}")
        )
        account = CorporateAccountRecord(
            account_id=account_id,
            employee_id=command.employee_id,
            username=command.employee_id.lower(),
            status=CorporateAccountStatus.DISABLED.value,
            version=1,
        )
        self._session.add(account)
        result = EmployeeOnboardingWriteResult(
            action_type=action_type,
            employee_id=command.employee_id,
            resource_id=account_id,
            employee_version=employee.version,
            account_version=1,
        )
        return self._save_result(idempotency_key, action_type, command, result)

    def assign_baseline_access_package(
        self,
        command: AssignBaselineAccessPackageCommand,
        *,
        idempotency_key: str,
    ) -> EmployeeOnboardingWriteResult:
        action_type = "assign_baseline_access_package"
        replay = self._find_replay(idempotency_key, action_type, command)
        if replay is not None:
            return replay
        employee = self._require_pending_employee(command.employee_id)
        package = self._require_versioned(
            AccessPackageRecord,
            command.package_code,
            command.package_version,
            "access package",
        )
        if not package.active or list(command.role_bindings) != package.role_bindings:
            raise EnterpriseRuleViolationError(
                "approved access package content is no longer current"
            )
        job = self._session.get(JobProfileRecord, employee.job_code)
        if job is None or job.baseline_access_package_code != command.package_code:
            raise EnterpriseRuleViolationError(
                "access package no longer matches employee job"
            )
        access_ids: list[str] = []
        for binding in command.role_bindings:
            application_code = str(binding.get("application_code", "")).strip()
            role_code = str(binding.get("role_code", "")).strip()
            application = self._session.get(ApplicationRecord, application_code)
            if (
                application is None
                or not application.active
                or role_code not in application.allowed_role_codes
            ):
                raise EnterpriseRuleViolationError(
                    "baseline access binding is no longer allowed"
                )
            existing = self._session.scalar(
                select(UserAccessRecord).where(
                    UserAccessRecord.employee_id == command.employee_id,
                    UserAccessRecord.application_code == application_code,
                    UserAccessRecord.role_code == role_code,
                )
            )
            if existing is not None:
                raise EnterpriseRuleViolationError(
                    "baseline access binding already exists"
                )
            access_id = str(
                uuid5(
                    NAMESPACE_URL,
                    "bizorch:baseline-access:"
                    f"{command.employee_id}:{application_code}:{role_code}",
                )
            )
            self._session.add(
                UserAccessRecord(
                    access_id=access_id,
                    employee_id=command.employee_id,
                    application_code=application_code,
                    role_code=role_code,
                    expires_at=_BASELINE_EXPIRES_AT,
                    active=True,
                )
            )
            access_ids.append(access_id)
        result = EmployeeOnboardingWriteResult(
            action_type=action_type,
            employee_id=command.employee_id,
            resource_id=",".join(access_ids) or command.package_code,
            employee_version=employee.version,
        )
        return self._save_result(idempotency_key, action_type, command, result)

    def create_asset_assignment_task(
        self,
        command: CreateAssetAssignmentTaskCommand,
        *,
        idempotency_key: str,
    ) -> EmployeeOnboardingWriteResult:
        action_type = "create_asset_assignment_task"
        replay = self._find_replay(idempotency_key, action_type, command)
        if replay is not None:
            return replay
        employee = self._require_pending_employee(command.employee_id)
        job = self._session.get(JobProfileRecord, employee.job_code)
        if job is None or job.asset_profile_code != command.asset_profile_code:
            raise EnterpriseRuleViolationError(
                "asset profile no longer matches employee job"
            )
        if command.task_type is not AssetTaskType.PROVISION:
            raise EnterpriseRuleViolationError(
                "onboarding may only create a provision asset task"
            )
        task_id = str(uuid5(NAMESPACE_URL, f"bizorch:asset-task:{idempotency_key}"))
        self._session.add(
            AssetTaskRecord(
                task_id=task_id,
                employee_id=command.employee_id,
                task_type=AssetTaskType.PROVISION.value,
                asset_profile_code=command.asset_profile_code,
                status=AssetTaskStatus.OPEN.value,
                idempotency_key=idempotency_key,
            )
        )
        result = EmployeeOnboardingWriteResult(
            action_type=action_type,
            employee_id=command.employee_id,
            resource_id=task_id,
            employee_version=employee.version,
        )
        return self._save_result(idempotency_key, action_type, command, result)

    def activate_employee_and_account(
        self,
        command: ActivateEmployeeAndAccountCommand,
        *,
        idempotency_key: str,
    ) -> EmployeeOnboardingWriteResult:
        action_type = "activate_employee_and_account"
        replay = self._find_replay(idempotency_key, action_type, command)
        if replay is not None:
            return replay
        employee = self._require_pending_employee(command.employee_id)
        account = self._session.scalar(
            select(CorporateAccountRecord).where(
                CorporateAccountRecord.employee_id == command.employee_id
            )
        )
        if account is None:
            raise EnterpriseRuleViolationError("corporate account does not exist")
        if (
            employee.version != command.expected_employee_version
            or account.version != command.expected_account_version
            or account.status != CorporateAccountStatus.DISABLED.value
        ):
            raise EnterpriseRuleViolationError(
                "employee or account version is no longer current"
            )
        job = self._session.get(JobProfileRecord, employee.job_code)
        if job is None:
            raise EnterpriseResourceNotFoundError(f"job:{employee.job_code}")
        package = self._session.get(
            AccessPackageRecord, job.baseline_access_package_code
        )
        expected_bindings = {
            (str(item["application_code"]), str(item["role_code"]))
            for item in (package.role_bindings if package else [])
        }
        actual_bindings = set(
            self._session.execute(
                select(
                    UserAccessRecord.application_code,
                    UserAccessRecord.role_code,
                ).where(
                    UserAccessRecord.employee_id == command.employee_id,
                    UserAccessRecord.active.is_(True),
                )
            ).all()
        )
        asset_task = self._session.scalar(
            select(AssetTaskRecord).where(
                AssetTaskRecord.employee_id == command.employee_id,
                AssetTaskRecord.task_type == AssetTaskType.PROVISION.value,
                AssetTaskRecord.status == AssetTaskStatus.OPEN.value,
                AssetTaskRecord.asset_profile_code == job.asset_profile_code,
            )
        )
        if not expected_bindings or actual_bindings != expected_bindings:
            raise EnterpriseRuleViolationError(
                "baseline access is incomplete or contains unexpected roles"
            )
        if asset_task is None:
            raise EnterpriseRuleViolationError("asset assignment task is missing")

        employee.active = True
        employee.employment_status = EmploymentStatus.ACTIVE.value
        employee.version += 1
        account.status = CorporateAccountStatus.ACTIVE.value
        account.version += 1
        lifecycle_request = self._session.scalar(
            select(EmployeeLifecycleRequestRecord).where(
                EmployeeLifecycleRequestRecord.subject_employee_id
                == command.employee_id,
                EmployeeLifecycleRequestRecord.request_type
                == EmployeeLifecycleRequestType.ONBOARDING.value,
                EmployeeLifecycleRequestRecord.status
                == EmployeeLifecycleRequestStatus.EXECUTING.value,
            )
        )
        if lifecycle_request is None:
            raise EnterpriseRuleViolationError(
                "executing onboarding request is missing"
            )
        lifecycle_request.status = EmployeeLifecycleRequestStatus.COMPLETED.value
        result = EmployeeOnboardingWriteResult(
            action_type=action_type,
            employee_id=command.employee_id,
            resource_id=account.account_id,
            employee_version=employee.version,
            account_version=account.version,
        )
        return self._save_result(idempotency_key, action_type, command, result)

    def _require_pending_employee(self, employee_id: str) -> EmployeeRecord:
        employee = self._session.get(EmployeeRecord, employee_id)
        if employee is None:
            raise EnterpriseResourceNotFoundError(f"employee:{employee_id}")
        if (
            employee.active
            or employee.employment_status != EmploymentStatus.PENDING_ONBOARDING.value
        ):
            raise EnterpriseRuleViolationError(
                "employee is not pending onboarding"
            )
        return employee

    def _require_versioned(
        self,
        model_type,
        key: str,
        expected_version: int,
        label: str,
    ):
        record = self._session.get(model_type, key)
        if record is None:
            raise EnterpriseResourceNotFoundError(f"{label}:{key}")
        if record.version != expected_version:
            raise EnterpriseRuleViolationError(f"{label} version is stale")
        return record

    def _find_replay(
        self,
        key: str,
        operation_type: str,
        command: BaseModel,
    ) -> EmployeeOnboardingWriteResult | None:
        normalized = key.strip()
        if not normalized:
            raise ValueError("idempotency_key must not be blank")
        record = self._session.get(ExternalIdempotencyRecord, normalized)
        if record is None:
            return None
        if (
            record.operation_type != operation_type
            or record.request_digest != self._command_digest(command)
        ):
            raise EnterpriseIdempotencyConflictError(normalized)
        return EmployeeOnboardingWriteResult.model_validate(
            record.response_payload
        ).model_copy(update={"replayed": True})

    def _save_result(
        self,
        key: str,
        operation_type: str,
        command: BaseModel,
        result: EmployeeOnboardingWriteResult,
    ) -> EmployeeOnboardingWriteResult:
        self._session.add(
            ExternalIdempotencyRecord(
                key=key.strip(),
                operation_type=operation_type,
                request_digest=self._command_digest(command),
                response_payload=result.model_dump(mode="json"),
            )
        )
        self._session.flush()
        return result

    @staticmethod
    def _command_digest(command: BaseModel) -> str:
        canonical = json.dumps(
            command.model_dump(mode="json"),
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()
