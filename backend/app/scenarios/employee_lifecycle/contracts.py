"""Whitelisted lifecycle intake fields and read-only enterprise facts."""

from datetime import date
from enum import Enum

from pydantic import BaseModel, ConfigDict, field_validator

from app.integrations.enterprise_ops import (
    EnterpriseOpsAccessPackage,
    EnterpriseOpsEmployeeLifecycleProfile,
    EnterpriseOpsEmployeeLifecycleRequest,
    EnterpriseOpsJobProfile,
    EnterpriseOpsOrganizationUnit,
    EnterpriseOpsWorkLocation,
)


class EmployeeLifecycleRequestType(str, Enum):
    ONBOARDING = "ONBOARDING"
    TRANSFER = "TRANSFER"
    OFFBOARDING = "OFFBOARDING"


_REQUIRED_FIELDS = {
    EmployeeLifecycleRequestType.ONBOARDING: (
        "subject_employee_id",
        "display_name",
        "target_department_code",
        "target_job_code",
        "target_manager_id",
        "work_location_code",
        "effective_date",
        "business_reason",
    ),
    EmployeeLifecycleRequestType.TRANSFER: (
        "subject_employee_id",
        "target_department_code",
        "target_job_code",
        "target_manager_id",
        "effective_date",
        "business_reason",
    ),
    EmployeeLifecycleRequestType.OFFBOARDING: (
        "subject_employee_id",
        "effective_date",
        "offboarding_reason",
        "business_reason",
    ),
}


class EmployeeLifecycleRequestDraft(BaseModel):
    """Persisted request facts; the authenticated initiator is never model supplied."""

    model_config = ConfigDict(frozen=True, extra="forbid", str_strip_whitespace=True)

    request_type: EmployeeLifecycleRequestType
    initiator_id: str
    subject_employee_id: str | None = None
    display_name: str | None = None
    target_department_code: str | None = None
    target_job_code: str | None = None
    target_manager_id: str | None = None
    work_location_code: str | None = None
    effective_date: date | None = None
    business_reason: str | None = None
    baseline_configuration_note: str | None = None
    offboarding_reason: str | None = None
    asset_return_note: str | None = None

    @field_validator(
        "initiator_id",
        "subject_employee_id",
        "display_name",
        "target_department_code",
        "target_job_code",
        "target_manager_id",
        "work_location_code",
        "business_reason",
        "baseline_configuration_note",
        "offboarding_reason",
        "asset_return_note",
        mode="before",
    )
    @classmethod
    def normalize_blank_text(cls, value: object) -> object:
        if isinstance(value, str):
            normalized = value.strip()
            return normalized or None
        return value

    @field_validator(
        "subject_employee_id",
        "target_department_code",
        "target_job_code",
        "target_manager_id",
        "work_location_code",
    )
    @classmethod
    def normalize_codes(cls, value: str | None) -> str | None:
        return value.upper() if value else None

    def missing_fields(self) -> tuple[str, ...]:
        values = self.model_dump()
        return tuple(
            field
            for field in _REQUIRED_FIELDS[self.request_type]
            if values[field] is None
        )


class EmployeeLifecycleContext(BaseModel):
    """Read-only facts available to deterministic lifecycle policy."""

    model_config = ConfigDict(frozen=True)

    subject_profile: EnterpriseOpsEmployeeLifecycleProfile | None = None
    target_department: EnterpriseOpsOrganizationUnit | None = None
    target_job: EnterpriseOpsJobProfile | None = None
    work_location: EnterpriseOpsWorkLocation | None = None
    target_manager_profile: EnterpriseOpsEmployeeLifecycleProfile | None = None
    approval_manager_profile: EnterpriseOpsEmployeeLifecycleProfile | None = None
    baseline_access_package: EnterpriseOpsAccessPackage | None = None
    open_requests: tuple[EnterpriseOpsEmployeeLifecycleRequest, ...] = ()
