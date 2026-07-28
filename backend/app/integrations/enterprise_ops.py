"""Validated HTTP client for the independent simulated enterprise system."""

import json
import socket
from datetime import date, datetime
from decimal import Decimal
from enum import Enum
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import ProxyHandler, Request, build_opener

from pydantic import BaseModel, ConfigDict, JsonValue, ValidationError


def _build_direct_opener():
    """Build an opener that never sends internal enterprise traffic to proxies."""
    return build_opener(ProxyHandler({}))


_DIRECT_OPENER = _build_direct_opener()


def _direct_urlopen(request: Request, timeout: float):
    return _DIRECT_OPENER.open(request, timeout=timeout)


class EnterpriseOpsEmployee(BaseModel):
    model_config = ConfigDict(frozen=True)

    employee_id: str
    display_name: str
    department_code: str
    manager_id: str | None
    active: bool


class EnterpriseOpsLifecycleEmployee(BaseModel):
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


class EnterpriseOpsOrganizationUnit(BaseModel):
    model_config = ConfigDict(frozen=True)

    department_code: str
    display_name: str
    manager_id: str
    active: bool
    version: int


class EnterpriseOpsWorkLocation(BaseModel):
    model_config = ConfigDict(frozen=True)

    location_code: str
    display_name: str
    active: bool
    version: int


class EnterpriseOpsJobProfile(BaseModel):
    model_config = ConfigDict(frozen=True)

    job_code: str
    display_name: str
    department_code: str
    baseline_access_package_code: str
    asset_profile_code: str
    active: bool
    version: int


class EnterpriseOpsCorporateAccount(BaseModel):
    model_config = ConfigDict(frozen=True)

    account_id: str
    employee_id: str
    username: str
    status: str
    version: int
    updated_at: datetime


class EnterpriseOpsAccessPackage(BaseModel):
    model_config = ConfigDict(frozen=True)

    package_code: str
    display_name: str
    role_bindings: tuple[dict[str, str], ...]
    version: int
    active: bool


class EnterpriseOpsAssetTask(BaseModel):
    model_config = ConfigDict(frozen=True)

    task_id: str
    employee_id: str
    task_type: str
    asset_profile_code: str
    status: str
    created_at: datetime
    updated_at: datetime


class EnterpriseOpsEmployeeLifecycleRequest(BaseModel):
    model_config = ConfigDict(frozen=True)

    request_id: str
    request_type: str
    subject_employee_id: str
    initiator_id: str
    status: str
    effective_date: date
    safe_summary: str
    created_at: datetime
    updated_at: datetime


class EnterpriseOpsApplication(BaseModel):
    model_config = ConfigDict(frozen=True)

    application_code: str
    display_name: str
    active: bool
    allowed_role_codes: tuple[str, ...]


class EnterpriseOpsUserAccess(BaseModel):
    model_config = ConfigDict(frozen=True)

    access_id: str
    employee_id: str
    application_code: str
    role_code: str
    expires_at: datetime
    active: bool


class EnterpriseOpsEmployeeLifecycleProfile(BaseModel):
    model_config = ConfigDict(frozen=True)

    employee: EnterpriseOpsLifecycleEmployee
    department: EnterpriseOpsOrganizationUnit
    job: EnterpriseOpsJobProfile
    account: EnterpriseOpsCorporateAccount | None
    active_access: tuple[EnterpriseOpsUserAccess, ...]
    asset_tasks: tuple[EnterpriseOpsAssetTask, ...]
    open_lifecycle_requests: tuple[
        EnterpriseOpsEmployeeLifecycleRequest, ...
    ]


class EnterpriseOpsCostCenter(BaseModel):
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


class EnterpriseOpsProcurementPolicy(BaseModel):
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


class EnterpriseOpsProcurementRequestItem(BaseModel):
    model_config = ConfigDict(frozen=True)

    line_no: int
    item_name: str
    item_category: str
    quantity: int
    specification_note: str | None


class EnterpriseOpsProcurementRequest(BaseModel):
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
    status: str
    policy_code: str
    policy_version: int
    items: tuple[EnterpriseOpsProcurementRequestItem, ...]
    created_at: datetime
    updated_at: datetime


class EnterpriseOpsBudgetReservation(BaseModel):
    model_config = ConfigDict(frozen=True)

    reservation_id: str
    request_id: str
    cost_center_code: str
    amount: Decimal
    currency: str
    status: str
    created_at: datetime
    updated_at: datetime


class EnterpriseOpsProcurementWriteResult(BaseModel):
    model_config = ConfigDict(frozen=True)

    request_id: str
    reservation_id: str
    cost_center_code: str
    reserved_amount: Decimal
    cost_center_version: int
    replayed: bool = False


class EnterpriseOpsEmployeeOnboardingWriteResult(BaseModel):
    model_config = ConfigDict(frozen=True)

    action_type: str
    employee_id: str
    resource_id: str
    employee_version: int | None = None
    account_version: int | None = None
    replayed: bool = False


class EnterpriseOpsRequest(BaseModel):
    model_config = ConfigDict(frozen=True)

    request_id: str
    employee_id: str
    application_code: str
    role_code: str
    duration_days: int
    business_reason: str
    status: str


class EnterpriseOpsEquipmentCriticality(str, Enum):
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"


class EnterpriseOpsEquipmentStatus(str, Enum):
    RUNNING = "RUNNING"
    DEGRADED = "DEGRADED"
    MAINTENANCE_PENDING = "MAINTENANCE_PENDING"
    IN_MAINTENANCE = "IN_MAINTENANCE"
    OUT_OF_SERVICE = "OUT_OF_SERVICE"


class EnterpriseOpsEquipment(BaseModel):
    model_config = ConfigDict(frozen=True)

    equipment_id: str
    equipment_code: str
    name: str
    site_code: str
    workshop_code: str
    production_line: str
    criticality: EnterpriseOpsEquipmentCriticality
    status: EnterpriseOpsEquipmentStatus
    responsible_manager_id: str
    version: int
    updated_at: datetime


class EnterpriseOpsEquipmentStatusView(BaseModel):
    model_config = ConfigDict(frozen=True)

    equipment_code: str
    status: EnterpriseOpsEquipmentStatus
    version: int
    updated_at: datetime


class EnterpriseOpsMaintenanceHistory(BaseModel):
    model_config = ConfigDict(frozen=True)

    record_id: str
    equipment_code: str
    fault_summary: str
    resolution_summary: str
    completed_at: datetime


class EnterpriseOpsMaintenanceWorkOrderStatus(str, Enum):
    OPEN = "OPEN"
    ASSIGNED = "ASSIGNED"
    CANCELLED = "CANCELLED"
    COMPLETED = "COMPLETED"


class EnterpriseOpsMaintenanceWorkOrder(BaseModel):
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
    status: EnterpriseOpsMaintenanceWorkOrderStatus
    idempotency_key: str
    created_at: datetime
    updated_at: datetime


class EnterpriseOpsMaintenanceWriteResult(BaseModel):
    model_config = ConfigDict(frozen=True)

    work_order_id: str
    equipment_code: str
    work_order_status: EnterpriseOpsMaintenanceWorkOrderStatus
    equipment_status: EnterpriseOpsEquipmentStatus
    equipment_version: int
    replayed: bool = False


class EnterpriseOpsWriteStatus(str, Enum):
    CREATED = "CREATED"
    GRANTED = "GRANTED"
    ALREADY_PRESENT = "ALREADY_PRESENT"
    REPLAYED = "REPLAYED"


class EnterpriseOpsWriteResult(BaseModel):
    model_config = ConfigDict(frozen=True)

    status: EnterpriseOpsWriteStatus
    resource_id: str
    replayed: bool = False


class EnterpriseOpsVerification(BaseModel):
    model_config = ConfigDict(frozen=True)

    confirmed: bool
    access: EnterpriseOpsUserAccess | None = None


class EnterpriseOpsClientError(RuntimeError):
    """Base connector error safe for application-level classification."""


class EnterpriseOpsUnavailableError(EnterpriseOpsClientError):
    """Network failure where no trustworthy response was received."""


class EnterpriseOpsRejectedError(EnterpriseOpsClientError):
    """The enterprise service explicitly rejected a valid HTTP request."""


class EnterpriseOpsResourceNotFoundError(EnterpriseOpsRejectedError):
    """The requested authoritative enterprise resource does not exist."""


class EnterpriseOpsProtocolError(EnterpriseOpsClientError):
    """The remote response violated the expected integration contract."""


class EnterpriseOpsHttpClient:
    """Small synchronous connector with timeouts and untrusted-output validation."""

    def __init__(
        self,
        base_url: str,
        *,
        internal_token: str,
        timeout_seconds: float = 5.0,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._internal_token = internal_token
        self._timeout_seconds = timeout_seconds
        if not self._base_url:
            raise ValueError("enterprise ops base_url must not be blank")
        if not self._internal_token:
            raise ValueError("enterprise ops internal_token must not be blank")
        if timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")

    def query_employee(self, employee_id: str) -> EnterpriseOpsEmployee:
        payload = self._request("GET", f"/internal/v1/employees/{quote(employee_id)}")
        return self._validate(EnterpriseOpsEmployee, payload)

    def query_employee_manager(self, employee_id: str) -> EnterpriseOpsEmployee:
        payload = self._request(
            "GET", f"/internal/v1/employees/{quote(employee_id)}/manager"
        )
        return self._validate(EnterpriseOpsEmployee, payload)

    def query_application(
        self, application_code: str
    ) -> EnterpriseOpsApplication:
        payload = self._request(
            "GET", f"/internal/v1/applications/{quote(application_code)}"
        )
        return self._validate(EnterpriseOpsApplication, payload)

    def query_user_access(
        self, employee_id: str
    ) -> tuple[EnterpriseOpsUserAccess, ...]:
        payload = self._request(
            "GET", f"/internal/v1/employees/{quote(employee_id)}/access"
        )
        if not isinstance(payload, list):
            raise EnterpriseOpsProtocolError("user access response must be a list")
        return tuple(self._validate(EnterpriseOpsUserAccess, item) for item in payload)

    def query_employee_lifecycle_profile(
        self,
        employee_id: str,
    ) -> EnterpriseOpsEmployeeLifecycleProfile:
        payload = self._request(
            "GET",
            f"/internal/v1/employees/{quote(employee_id)}/lifecycle-snapshot",
        )
        return self._validate(EnterpriseOpsEmployeeLifecycleProfile, payload)

    def query_organization_unit(
        self,
        department_code: str,
    ) -> EnterpriseOpsOrganizationUnit:
        payload = self._request(
            "GET",
            f"/internal/v1/organization-units/{quote(department_code)}",
        )
        return self._validate(EnterpriseOpsOrganizationUnit, payload)

    def query_job_profile(self, job_code: str) -> EnterpriseOpsJobProfile:
        payload = self._request(
            "GET",
            f"/internal/v1/job-profiles/{quote(job_code)}",
        )
        return self._validate(EnterpriseOpsJobProfile, payload)

    def query_work_location(
        self,
        location_code: str,
    ) -> EnterpriseOpsWorkLocation:
        payload = self._request(
            "GET",
            f"/internal/v1/work-locations/{quote(location_code)}",
        )
        return self._validate(EnterpriseOpsWorkLocation, payload)

    def query_corporate_account(
        self,
        employee_id: str,
    ) -> EnterpriseOpsCorporateAccount | None:
        payload = self._request(
            "GET",
            f"/internal/v1/employees/{quote(employee_id)}/corporate-account",
        )
        if payload is None:
            return None
        return self._validate(EnterpriseOpsCorporateAccount, payload)

    def query_access_package(
        self,
        package_code: str,
    ) -> EnterpriseOpsAccessPackage:
        payload = self._request(
            "GET",
            f"/internal/v1/access-packages/{quote(package_code)}",
        )
        return self._validate(EnterpriseOpsAccessPackage, payload)

    def query_employee_asset_tasks(
        self,
        employee_id: str,
    ) -> tuple[EnterpriseOpsAssetTask, ...]:
        payload = self._request(
            "GET",
            f"/internal/v1/employees/{quote(employee_id)}/asset-tasks",
        )
        if not isinstance(payload, list):
            raise EnterpriseOpsProtocolError("asset task response must be a list")
        return tuple(self._validate(EnterpriseOpsAssetTask, item) for item in payload)

    def query_open_employee_lifecycle_request(
        self,
        employee_id: str,
    ) -> tuple[EnterpriseOpsEmployeeLifecycleRequest, ...]:
        payload = self._request(
            "GET",
            f"/internal/v1/employees/{quote(employee_id)}"
            "/lifecycle-requests/open",
        )
        if not isinstance(payload, list):
            raise EnterpriseOpsProtocolError(
                "open lifecycle request response must be a list"
            )
        return tuple(
            self._validate(EnterpriseOpsEmployeeLifecycleRequest, item)
            for item in payload
        )

    def query_employee_lifecycle_request(
        self,
        *,
        request_id: str | None = None,
        idempotency_key: str | None = None,
    ) -> EnterpriseOpsEmployeeLifecycleRequest:
        if bool(request_id) == bool(idempotency_key):
            raise ValueError(
                "provide exactly one of request_id or idempotency_key"
            )
        path = (
            "/internal/v1/employee-lifecycle-requests/"
            + quote(request_id or "")
            if request_id
            else "/internal/v1/employee-lifecycle-requests?idempotency_key="
            + quote(idempotency_key or "")
        )
        return self._validate(
            EnterpriseOpsEmployeeLifecycleRequest,
            self._request("GET", path),
        )

    def query_cost_center(
        self, cost_center_code: str
    ) -> EnterpriseOpsCostCenter:
        return self._validate(
            EnterpriseOpsCostCenter,
            self._request(
                "GET",
                f"/internal/v1/cost-centers/{quote(cost_center_code)}",
            ),
        )

    def query_current_procurement_policy(
        self,
    ) -> EnterpriseOpsProcurementPolicy:
        return self._validate(
            EnterpriseOpsProcurementPolicy,
            self._request("GET", "/internal/v1/procurement-policies/current"),
        )

    def query_open_procurement_requests(
        self,
        *,
        requester_id: str,
        cost_center_code: str,
    ) -> tuple[EnterpriseOpsProcurementRequest, ...]:
        payload = self._request(
            "GET",
            "/internal/v1/procurement-requests/open?"
            f"requester_id={quote(requester_id)}&"
            f"cost_center_code={quote(cost_center_code)}",
        )
        if not isinstance(payload, list):
            raise EnterpriseOpsProtocolError(
                "open procurement request response must be a list"
            )
        return tuple(
            self._validate(EnterpriseOpsProcurementRequest, item)
            for item in payload
        )

    def query_procurement_request(
        self,
        *,
        request_id: str | None = None,
        workflow_run_id: str | None = None,
    ) -> EnterpriseOpsProcurementRequest:
        if bool(request_id) == bool(workflow_run_id):
            raise ValueError(
                "provide exactly one of request_id or workflow_run_id"
            )
        path = (
            f"/internal/v1/procurement-requests/{quote(request_id or '')}"
            if request_id
            else "/internal/v1/procurement-requests/by-workflow/"
            + quote(workflow_run_id or "")
        )
        return self._validate(
            EnterpriseOpsProcurementRequest,
            self._request("GET", path),
        )

    def query_budget_reservation(
        self, request_id: str
    ) -> EnterpriseOpsBudgetReservation:
        return self._validate(
            EnterpriseOpsBudgetReservation,
            self._request(
                "GET",
                "/internal/v1/budget-reservations/by-request/"
                + quote(request_id),
            ),
        )

    def create_procurement_request_and_reserve_budget(
        self,
        payload: dict[str, JsonValue],
        *,
        idempotency_key: str,
    ) -> EnterpriseOpsProcurementWriteResult:
        return self._validate(
            EnterpriseOpsProcurementWriteResult,
            self._request(
                "POST",
                "/internal/v1/procurement-requests",
                payload=payload,
                idempotency_key=idempotency_key,
            ),
        )

    def create_pending_employee(
        self,
        payload: dict[str, JsonValue],
        *,
        idempotency_key: str,
    ) -> EnterpriseOpsEmployeeOnboardingWriteResult:
        return self._onboarding_write(
            "/internal/v1/employee-onboarding/pending-employees",
            payload,
            idempotency_key,
        )

    def create_disabled_corporate_account(
        self,
        payload: dict[str, JsonValue],
        *,
        idempotency_key: str,
    ) -> EnterpriseOpsEmployeeOnboardingWriteResult:
        return self._onboarding_write(
            "/internal/v1/employee-onboarding/disabled-accounts",
            payload,
            idempotency_key,
        )

    def assign_baseline_access_package(
        self,
        payload: dict[str, JsonValue],
        *,
        idempotency_key: str,
    ) -> EnterpriseOpsEmployeeOnboardingWriteResult:
        return self._onboarding_write(
            "/internal/v1/employee-onboarding/baseline-access",
            payload,
            idempotency_key,
        )

    def create_asset_assignment_task(
        self,
        payload: dict[str, JsonValue],
        *,
        idempotency_key: str,
    ) -> EnterpriseOpsEmployeeOnboardingWriteResult:
        return self._onboarding_write(
            "/internal/v1/employee-onboarding/asset-assignment-tasks",
            payload,
            idempotency_key,
        )

    def activate_employee_and_account(
        self,
        payload: dict[str, JsonValue],
        *,
        idempotency_key: str,
    ) -> EnterpriseOpsEmployeeOnboardingWriteResult:
        return self._onboarding_write(
            "/internal/v1/employee-onboarding/activations",
            payload,
            idempotency_key,
        )

    def update_employee_assignment(
        self, payload: dict[str, JsonValue], *, idempotency_key: str
    ) -> EnterpriseOpsEmployeeOnboardingWriteResult:
        return self._onboarding_write(
            "/internal/v1/employee-transfer/assignments",
            payload,
            idempotency_key,
        )

    def revoke_obsolete_baseline_access(
        self, payload: dict[str, JsonValue], *, idempotency_key: str
    ) -> EnterpriseOpsEmployeeOnboardingWriteResult:
        return self._onboarding_write(
            "/internal/v1/employee-transfer/access-revocations",
            payload,
            idempotency_key,
        )

    def grant_target_baseline_access(
        self, payload: dict[str, JsonValue], *, idempotency_key: str
    ) -> EnterpriseOpsEmployeeOnboardingWriteResult:
        return self._onboarding_write(
            "/internal/v1/employee-transfer/access-grants",
            payload,
            idempotency_key,
        )

    def create_asset_adjustment_task(
        self, payload: dict[str, JsonValue], *, idempotency_key: str
    ) -> EnterpriseOpsEmployeeOnboardingWriteResult:
        return self._onboarding_write(
            "/internal/v1/employee-transfer/asset-adjustment-tasks",
            payload,
            idempotency_key,
        )

    def verify_employee_transfer_consistency(
        self, payload: dict[str, JsonValue], *, idempotency_key: str
    ) -> EnterpriseOpsEmployeeOnboardingWriteResult:
        return self._onboarding_write(
            "/internal/v1/employee-transfer/verifications",
            payload,
            idempotency_key,
        )

    def disable_corporate_account(
        self, payload: dict[str, JsonValue], *, idempotency_key: str
    ) -> EnterpriseOpsEmployeeOnboardingWriteResult:
        return self._onboarding_write(
            "/internal/v1/employee-offboarding/account-disables",
            payload,
            idempotency_key,
        )

    def revoke_all_employee_access(
        self, payload: dict[str, JsonValue], *, idempotency_key: str
    ) -> EnterpriseOpsEmployeeOnboardingWriteResult:
        return self._onboarding_write(
            "/internal/v1/employee-offboarding/access-revocations",
            payload,
            idempotency_key,
        )

    def create_asset_return_task(
        self, payload: dict[str, JsonValue], *, idempotency_key: str
    ) -> EnterpriseOpsEmployeeOnboardingWriteResult:
        return self._onboarding_write(
            "/internal/v1/employee-offboarding/asset-return-tasks",
            payload,
            idempotency_key,
        )

    def mark_employee_inactive(
        self, payload: dict[str, JsonValue], *, idempotency_key: str
    ) -> EnterpriseOpsEmployeeOnboardingWriteResult:
        return self._onboarding_write(
            "/internal/v1/employee-offboarding/inactive-employees",
            payload,
            idempotency_key,
        )

    def verify_employee_offboarding_consistency(
        self, payload: dict[str, JsonValue], *, idempotency_key: str
    ) -> EnterpriseOpsEmployeeOnboardingWriteResult:
        return self._onboarding_write(
            "/internal/v1/employee-offboarding/verifications",
            payload,
            idempotency_key,
        )

    def _onboarding_write(
        self,
        path: str,
        payload: dict[str, JsonValue],
        idempotency_key: str,
    ) -> EnterpriseOpsEmployeeOnboardingWriteResult:
        response = self._request(
            "POST",
            path,
            payload=payload,
            idempotency_key=idempotency_key,
        )
        return self._validate(
            EnterpriseOpsEmployeeOnboardingWriteResult,
            response,
        )

    def query_access_request(self, request_id: str) -> EnterpriseOpsRequest:
        payload = self._request(
            "GET", f"/internal/v1/access-requests/{quote(request_id)}"
        )
        return self._validate(EnterpriseOpsRequest, payload)

    def query_equipment(self, equipment_code: str) -> EnterpriseOpsEquipment:
        payload = self._request(
            "GET", f"/internal/v1/equipment/{quote(equipment_code)}"
        )
        return self._validate(EnterpriseOpsEquipment, payload)

    def query_equipment_status(
        self,
        equipment_code: str,
    ) -> EnterpriseOpsEquipmentStatusView:
        payload = self._request(
            "GET", f"/internal/v1/equipment/{quote(equipment_code)}/status"
        )
        return self._validate(EnterpriseOpsEquipmentStatusView, payload)

    def query_maintenance_history(
        self,
        equipment_code: str,
        *,
        limit: int = 10,
    ) -> tuple[EnterpriseOpsMaintenanceHistory, ...]:
        payload = self._request(
            "GET",
            f"/internal/v1/equipment/{quote(equipment_code)}/maintenance-history"
            f"?limit={limit}",
        )
        if not isinstance(payload, list):
            raise EnterpriseOpsProtocolError(
                "maintenance history response must be a list"
            )
        return tuple(
            self._validate(EnterpriseOpsMaintenanceHistory, item)
            for item in payload
        )

    def query_maintenance_work_order(
        self,
        *,
        work_order_id: str | None = None,
        idempotency_key: str | None = None,
    ) -> EnterpriseOpsMaintenanceWorkOrder:
        if bool(work_order_id) == bool(idempotency_key):
            raise ValueError(
                "provide exactly one of work_order_id or idempotency_key"
            )
        path = (
            f"/internal/v1/maintenance-work-orders/{quote(work_order_id or '')}"
            if work_order_id
            else "/internal/v1/maintenance-work-orders?idempotency_key="
            + quote(idempotency_key or "")
        )
        return self._validate(
            EnterpriseOpsMaintenanceWorkOrder,
            self._request("GET", path),
        )

    def create_maintenance_work_order(
        self,
        payload: dict[str, JsonValue],
        *,
        idempotency_key: str,
    ) -> EnterpriseOpsMaintenanceWriteResult:
        response = self._request(
            "POST",
            "/internal/v1/maintenance-work-orders",
            payload=payload,
            idempotency_key=idempotency_key,
        )
        return self._validate(EnterpriseOpsMaintenanceWriteResult, response)

    def create_access_request(
        self,
        payload: dict[str, JsonValue],
        *,
        idempotency_key: str,
    ) -> EnterpriseOpsWriteResult:
        response = self._request(
            "POST",
            "/internal/v1/access-requests",
            payload=payload,
            idempotency_key=idempotency_key,
        )
        return self._validate(EnterpriseOpsWriteResult, response)

    def grant_application_access(
        self,
        payload: dict[str, JsonValue],
        *,
        idempotency_key: str,
    ) -> EnterpriseOpsWriteResult:
        response = self._request(
            "POST",
            "/internal/v1/access/grants",
            payload=payload,
            idempotency_key=idempotency_key,
        )
        return self._validate(EnterpriseOpsWriteResult, response)

    def verify_application_access(
        self,
        payload: dict[str, JsonValue],
    ) -> EnterpriseOpsVerification:
        response = self._request(
            "POST", "/internal/v1/access/verify", payload=payload
        )
        return self._validate(EnterpriseOpsVerification, response)

    def _request(
        self,
        method: str,
        path: str,
        *,
        payload: dict[str, JsonValue] | None = None,
        idempotency_key: str | None = None,
    ) -> Any:
        headers = {
            "Accept": "application/json",
            "X-Enterprise-Internal-Token": self._internal_token,
        }
        body = None
        if payload is not None:
            body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            headers["Content-Type"] = "application/json"
        if idempotency_key is not None:
            headers["Idempotency-Key"] = idempotency_key
        request = Request(
            f"{self._base_url}{path}", data=body, headers=headers, method=method
        )
        try:
            with _direct_urlopen(request, timeout=self._timeout_seconds) as response:
                return json.loads(response.read().decode("utf-8"))
        except HTTPError as exc:
            if exc.code == 404:
                raise EnterpriseOpsResourceNotFoundError(
                    "enterprise resource was not found"
                ) from exc
            raise EnterpriseOpsRejectedError(f"enterprise HTTP {exc.code}") from exc
        except (URLError, TimeoutError, socket.timeout) as exc:
            raise EnterpriseOpsUnavailableError(
                "enterprise service did not return a trustworthy response"
            ) from exc
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise EnterpriseOpsProtocolError("enterprise response is not valid JSON") from exc

    @staticmethod
    def _validate(model_type, payload):
        try:
            return model_type.model_validate(payload)
        except ValidationError as exc:
            raise EnterpriseOpsProtocolError(
                f"enterprise response does not match {model_type.__name__}"
            ) from exc
