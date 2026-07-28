"""Validated API contracts owned by the simulated enterprise system."""

from datetime import date, datetime
from decimal import Decimal
from enum import Enum

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class EmployeeView(BaseModel):
    model_config = ConfigDict(frozen=True)

    employee_id: str
    display_name: str
    department_code: str
    manager_id: str | None
    active: bool
    employment_status: str
    job_code: str
    work_location_code: str
    version: int
    updated_at: datetime


class EmploymentStatus(str, Enum):
    PENDING_ONBOARDING = "PENDING_ONBOARDING"
    ACTIVE = "ACTIVE"
    INACTIVE = "INACTIVE"


class CorporateAccountStatus(str, Enum):
    DISABLED = "DISABLED"
    ACTIVE = "ACTIVE"


class AssetTaskType(str, Enum):
    PROVISION = "PROVISION"
    ADJUST = "ADJUST"
    RETURN = "RETURN"


class AssetTaskStatus(str, Enum):
    OPEN = "OPEN"
    COMPLETED = "COMPLETED"
    CANCELLED = "CANCELLED"


class EmployeeLifecycleRequestType(str, Enum):
    ONBOARDING = "ONBOARDING"
    TRANSFER = "TRANSFER"
    OFFBOARDING = "OFFBOARDING"


class EmployeeLifecycleRequestStatus(str, Enum):
    PENDING_APPROVAL = "PENDING_APPROVAL"
    EXECUTING = "EXECUTING"
    WAITING_HUMAN = "WAITING_HUMAN"
    COMPLETED = "COMPLETED"
    CANCELLED = "CANCELLED"


class OrganizationUnitView(BaseModel):
    model_config = ConfigDict(frozen=True)

    department_code: str
    display_name: str
    manager_id: str
    active: bool
    version: int


class WorkLocationView(BaseModel):
    model_config = ConfigDict(frozen=True)

    location_code: str
    display_name: str
    active: bool
    version: int


class JobProfileView(BaseModel):
    model_config = ConfigDict(frozen=True)

    job_code: str
    display_name: str
    department_code: str
    baseline_access_package_code: str
    asset_profile_code: str
    active: bool
    version: int


class CorporateAccountView(BaseModel):
    model_config = ConfigDict(frozen=True)

    account_id: str
    employee_id: str
    username: str
    status: CorporateAccountStatus
    version: int
    updated_at: datetime


class AccessPackageView(BaseModel):
    model_config = ConfigDict(frozen=True)

    package_code: str
    display_name: str
    role_bindings: tuple[dict[str, str], ...]
    version: int
    active: bool


class AssetTaskView(BaseModel):
    model_config = ConfigDict(frozen=True)

    task_id: str
    employee_id: str
    task_type: AssetTaskType
    asset_profile_code: str
    status: AssetTaskStatus
    created_at: datetime
    updated_at: datetime


class EmployeeLifecycleRequestView(BaseModel):
    model_config = ConfigDict(frozen=True)

    request_id: str
    request_type: EmployeeLifecycleRequestType
    subject_employee_id: str
    initiator_id: str
    status: EmployeeLifecycleRequestStatus
    effective_date: date
    safe_summary: str
    created_at: datetime
    updated_at: datetime


class ApplicationView(BaseModel):
    model_config = ConfigDict(frozen=True)

    application_code: str
    display_name: str
    active: bool
    allowed_role_codes: tuple[str, ...]


class UserAccessView(BaseModel):
    model_config = ConfigDict(frozen=True)

    access_id: str
    employee_id: str
    application_code: str
    role_code: str
    expires_at: datetime
    active: bool


class EmployeeLifecycleSnapshot(BaseModel):
    """Read-only authoritative facts used before generating a plan."""

    model_config = ConfigDict(frozen=True)

    employee: EmployeeView
    department: OrganizationUnitView
    job: JobProfileView
    account: CorporateAccountView | None
    active_access: tuple[UserAccessView, ...]
    asset_tasks: tuple[AssetTaskView, ...]
    open_lifecycle_requests: tuple[EmployeeLifecycleRequestView, ...]


class ProcurementRequestStatus(str, Enum):
    """Read states required for duplicate-risk and execution verification."""

    PENDING_APPROVAL = "PENDING_APPROVAL"
    WAITING_HUMAN = "WAITING_HUMAN"
    SUBMITTED = "SUBMITTED"
    CANCELLED = "CANCELLED"


class BudgetReservationStatus(str, Enum):
    ACTIVE = "ACTIVE"
    RELEASED = "RELEASED"


class CostCenterView(BaseModel):
    """Authoritative budget snapshot. It is internal, not a browser DTO."""

    model_config = ConfigDict(frozen=True)

    cost_center_code: str
    display_name: str
    department_code: str
    budget_owner_id: str
    currency: str
    budget_total: Decimal
    spent_amount: Decimal
    reserved_amount: Decimal
    available_amount: Decimal
    active: bool
    version: int
    updated_at: datetime


class ProcurementPolicyView(BaseModel):
    model_config = ConfigDict(frozen=True)

    policy_code: str
    version: int
    currency: str
    level_one_limit: Decimal
    level_two_limit: Decimal
    procurement_approver_id: str
    allowed_item_categories: tuple[str, ...]
    active: bool
    effective_from: date


class ProcurementRequestItemView(BaseModel):
    model_config = ConfigDict(frozen=True)

    line_no: int
    item_name: str
    item_category: str
    quantity: int
    specification_note: str | None


class ProcurementRequestView(BaseModel):
    model_config = ConfigDict(frozen=True)

    request_id: str
    external_workflow_run_id: str
    requester_id: str
    cost_center_code: str
    estimated_total_amount: Decimal
    currency: str
    desired_date: date
    delivery_location_code: str
    business_reason_summary: str
    status: ProcurementRequestStatus
    policy_code: str
    policy_version: int
    items: tuple[ProcurementRequestItemView, ...]
    created_at: datetime
    updated_at: datetime


class BudgetReservationView(BaseModel):
    model_config = ConfigDict(frozen=True)

    reservation_id: str
    request_id: str
    cost_center_code: str
    amount: Decimal
    currency: str
    status: BudgetReservationStatus
    created_at: datetime
    updated_at: datetime


class CreateProcurementRequestItemCommand(BaseModel):
    """One immutable line in an approved procurement request."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    line_no: int = Field(ge=1, le=20)
    item_name: str = Field(min_length=2, max_length=200)
    item_category: str = Field(min_length=2, max_length=100)
    quantity: int = Field(ge=1, le=999)
    specification_note: str | None = Field(default=None, max_length=500)


class CreateProcurementRequestAndReserveBudgetCommand(BaseModel):
    """Atomic enterprise write accepted only after BizOrch approval."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    workflow_run_id: str = Field(min_length=1, max_length=36)
    requester_id: str = Field(min_length=1, max_length=100)
    items: tuple[CreateProcurementRequestItemCommand, ...] = Field(
        min_length=1, max_length=20
    )
    estimated_total_amount: Decimal = Field(gt=0, max_digits=18, decimal_places=2)
    currency: str = Field(min_length=3, max_length=3)
    cost_center_code: str = Field(min_length=1, max_length=100)
    desired_date: date
    delivery_location_code: str = Field(min_length=2, max_length=100)
    business_reason_summary: str = Field(min_length=8, max_length=1000)
    policy_code: str = Field(min_length=1, max_length=100)
    policy_version: int = Field(ge=1)
    expected_cost_center_version: int = Field(ge=1)
    expected_reserved_amount: Decimal = Field(
        ge=0, max_digits=18, decimal_places=2
    )
    expected_business_approver_id: str = Field(min_length=1, max_length=100)
    expected_budget_owner_id: str = Field(min_length=1, max_length=100)
    expected_procurement_approver_id: str = Field(min_length=1, max_length=100)

    @model_validator(mode="after")
    def validate_lines(self) -> "CreateProcurementRequestAndReserveBudgetCommand":
        line_numbers = [item.line_no for item in self.items]
        if sorted(line_numbers) != list(range(1, len(self.items) + 1)):
            raise ValueError("procurement line numbers must be contiguous from one")
        return self


class ProcurementWriteResult(BaseModel):
    """Minimal receipt for the atomic request and reservation transaction."""

    model_config = ConfigDict(frozen=True)

    request_id: str
    reservation_id: str
    cost_center_code: str
    reserved_amount: Decimal
    cost_center_version: int
    replayed: bool = False


class CreatePendingEmployeeCommand(BaseModel):
    """Create the non-active employee shell used by onboarding."""

    model_config = ConfigDict(frozen=True, str_strip_whitespace=True)

    employee_id: str
    display_name: str
    department_code: str
    job_code: str
    manager_id: str
    work_location_code: str
    initiator_id: str
    effective_date: date
    business_reason: str
    expected_department_version: int = Field(ge=1)
    expected_job_version: int = Field(ge=1)
    expected_work_location_version: int = Field(ge=1)

    @field_validator(
        "employee_id",
        "display_name",
        "department_code",
        "job_code",
        "manager_id",
        "work_location_code",
        "initiator_id",
        "business_reason",
    )
    @classmethod
    def reject_blank_pending_employee_text(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("onboarding command text fields must not be blank")
        return value


class CreateDisabledCorporateAccountCommand(BaseModel):
    model_config = ConfigDict(frozen=True, str_strip_whitespace=True)

    employee_id: str

    @field_validator("employee_id")
    @classmethod
    def reject_blank_account_employee(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("employee_id must not be blank")
        return value


class AssignBaselineAccessPackageCommand(BaseModel):
    model_config = ConfigDict(frozen=True, str_strip_whitespace=True)

    employee_id: str
    package_code: str
    package_version: int = Field(ge=1)
    role_bindings: tuple[dict[str, str], ...]

    @field_validator("employee_id", "package_code")
    @classmethod
    def reject_blank_access_package_text(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("baseline access fields must not be blank")
        return value


class CreateAssetAssignmentTaskCommand(BaseModel):
    model_config = ConfigDict(frozen=True, str_strip_whitespace=True)

    employee_id: str
    asset_profile_code: str
    task_type: AssetTaskType = AssetTaskType.PROVISION

    @field_validator("employee_id", "asset_profile_code")
    @classmethod
    def reject_blank_asset_task_text(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("asset task fields must not be blank")
        return value


class ActivateEmployeeAndAccountCommand(BaseModel):
    model_config = ConfigDict(frozen=True, str_strip_whitespace=True)

    employee_id: str
    expected_employee_version: int = Field(ge=1)
    expected_account_version: int = Field(ge=1)

    @field_validator("employee_id")
    @classmethod
    def reject_blank_activation_employee(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("employee_id must not be blank")
        return value


class EmployeeOnboardingWriteResult(BaseModel):
    """Small, operation-neutral result returned by every onboarding write."""

    model_config = ConfigDict(frozen=True)

    action_type: str
    employee_id: str
    resource_id: str
    employee_version: int | None = None
    account_version: int | None = None
    replayed: bool = False


# Kept as an alias so V4-09 callers remain source-compatible while V4-10
# broadens the same wire contract to transfer and offboarding.
EmployeeLifecycleWriteResult = EmployeeOnboardingWriteResult


class UpdateEmployeeAssignmentCommand(BaseModel):
    model_config = ConfigDict(frozen=True, str_strip_whitespace=True)

    employee_id: str
    department_code: str
    job_code: str
    manager_id: str
    work_location_code: str
    initiator_id: str
    effective_date: date
    business_reason: str
    expected_employee_version: int = Field(ge=1)
    expected_department_version: int = Field(ge=1)
    expected_job_version: int = Field(ge=1)
    expected_work_location_version: int = Field(ge=1)


class ChangeEmployeeAccessCommand(BaseModel):
    model_config = ConfigDict(frozen=True, str_strip_whitespace=True)

    employee_id: str
    role_bindings: tuple[dict[str, str], ...]
    package_code: str | None = None
    package_version: int | None = Field(default=None, ge=1)


class CreateAssetAdjustmentTaskCommand(BaseModel):
    model_config = ConfigDict(frozen=True, str_strip_whitespace=True)

    employee_id: str
    asset_profile_code: str
    task_type: AssetTaskType = AssetTaskType.ADJUST


class VerifyEmployeeTransferCommand(BaseModel):
    model_config = ConfigDict(frozen=True, str_strip_whitespace=True)

    employee_id: str
    expected_department_code: str
    expected_job_code: str
    expected_manager_id: str
    expected_work_location_code: str


class DisableCorporateAccountCommand(BaseModel):
    model_config = ConfigDict(frozen=True, str_strip_whitespace=True)

    employee_id: str
    initiator_id: str
    effective_date: date
    business_reason: str
    expected_account_version: int | None = Field(default=None, ge=1)


class RevokeAllEmployeeAccessCommand(BaseModel):
    model_config = ConfigDict(frozen=True, str_strip_whitespace=True)

    employee_id: str
    access_ids: tuple[str, ...]


class CreateAssetReturnTaskCommand(BaseModel):
    model_config = ConfigDict(frozen=True, str_strip_whitespace=True)

    employee_id: str
    task_type: AssetTaskType = AssetTaskType.RETURN
    asset_return_note: str | None = None
    existing_asset_task_ids: tuple[str, ...] = ()


class MarkEmployeeInactiveCommand(BaseModel):
    model_config = ConfigDict(frozen=True, str_strip_whitespace=True)

    employee_id: str
    target_status: EmploymentStatus = EmploymentStatus.INACTIVE
    expected_employee_version: int = Field(ge=1)
    offboarding_reason: str | None = None


class VerifyEmployeeOffboardingCommand(BaseModel):
    model_config = ConfigDict(frozen=True, str_strip_whitespace=True)

    employee_id: str
    expected_employee_status: EmploymentStatus = EmploymentStatus.INACTIVE
    expected_account_status: CorporateAccountStatus = CorporateAccountStatus.DISABLED
    expected_active_access_count: int = Field(default=0, ge=0)


class AccessRequestStatus(str, Enum):
    CREATED = "CREATED"
    GRANTED = "GRANTED"


class AccessRequestView(BaseModel):
    model_config = ConfigDict(frozen=True)

    request_id: str
    employee_id: str
    application_code: str
    role_code: str
    duration_days: int
    business_reason: str
    status: AccessRequestStatus


class CreateAccessRequestCommand(BaseModel):
    model_config = ConfigDict(frozen=True, str_strip_whitespace=True)

    request_id: str
    employee_id: str
    application_code: str
    role_code: str
    duration_days: int = Field(ge=1, le=90)
    business_reason: str

    @field_validator(
        "request_id",
        "employee_id",
        "application_code",
        "role_code",
        "business_reason",
    )
    @classmethod
    def reject_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("command text fields must not be blank")
        return value


class GrantApplicationAccessCommand(BaseModel):
    model_config = ConfigDict(frozen=True, str_strip_whitespace=True)

    employee_id: str
    application_code: str
    role_code: str
    duration_days: int = Field(ge=1, le=90)
    access_request_id: str | None = None

    @field_validator("employee_id", "application_code", "role_code")
    @classmethod
    def reject_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("grant command text fields must not be blank")
        return value


class WriteResultStatus(str, Enum):
    CREATED = "CREATED"
    GRANTED = "GRANTED"
    ALREADY_PRESENT = "ALREADY_PRESENT"
    REPLAYED = "REPLAYED"


class EnterpriseWriteResult(BaseModel):
    model_config = ConfigDict(frozen=True)

    status: WriteResultStatus
    resource_id: str
    replayed: bool = False


class VerifyApplicationAccessQuery(BaseModel):
    model_config = ConfigDict(frozen=True)

    employee_id: str
    application_code: str
    role_code: str


class AccessVerificationResult(BaseModel):
    model_config = ConfigDict(frozen=True)

    confirmed: bool
    access: UserAccessView | None = None


class EquipmentCriticality(str, Enum):
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"


class EquipmentStatus(str, Enum):
    RUNNING = "RUNNING"
    DEGRADED = "DEGRADED"
    MAINTENANCE_PENDING = "MAINTENANCE_PENDING"
    IN_MAINTENANCE = "IN_MAINTENANCE"
    OUT_OF_SERVICE = "OUT_OF_SERVICE"


class EquipmentView(BaseModel):
    model_config = ConfigDict(frozen=True)

    equipment_id: str
    equipment_code: str
    name: str
    site_code: str
    workshop_code: str
    production_line: str
    criticality: EquipmentCriticality
    status: EquipmentStatus
    responsible_manager_id: str
    version: int
    updated_at: datetime


class EquipmentStatusView(BaseModel):
    model_config = ConfigDict(frozen=True)

    equipment_code: str
    status: EquipmentStatus
    version: int
    updated_at: datetime


class MaintenanceHistoryView(BaseModel):
    model_config = ConfigDict(frozen=True)

    record_id: str
    equipment_code: str
    fault_summary: str
    resolution_summary: str
    completed_at: datetime


class MaintenanceWorkOrderStatus(str, Enum):
    OPEN = "OPEN"
    ASSIGNED = "ASSIGNED"
    CANCELLED = "CANCELLED"
    COMPLETED = "COMPLETED"


class MaintenanceWorkOrderView(BaseModel):
    model_config = ConfigDict(frozen=True)

    work_order_id: str
    equipment_code: str
    requester_id: str
    fault_description: str
    observed_at: datetime
    production_impact: str
    safety_observation: str
    business_reason: str
    priority: str
    status: MaintenanceWorkOrderStatus
    idempotency_key: str
    created_at: datetime
    updated_at: datetime


class CreateMaintenanceWorkOrderCommand(BaseModel):
    model_config = ConfigDict(frozen=True, str_strip_whitespace=True)

    requester_id: str
    equipment_code: str
    expected_equipment_version: int = Field(ge=1)
    fault_description: str
    observed_at: datetime
    production_impact: str
    safety_observation: str
    business_reason: str
    priority: str

    @field_validator(
        "requester_id",
        "equipment_code",
        "fault_description",
        "production_impact",
        "safety_observation",
        "business_reason",
        "priority",
    )
    @classmethod
    def reject_blank_maintenance_text(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("maintenance command text fields must not be blank")
        return value


class MaintenanceWorkOrderWriteResult(BaseModel):
    model_config = ConfigDict(frozen=True)

    work_order_id: str
    equipment_code: str
    work_order_status: MaintenanceWorkOrderStatus
    equipment_status: EquipmentStatus
    equipment_version: int
    replayed: bool = False
