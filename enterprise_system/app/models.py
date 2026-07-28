"""Access and equipment data owned outside the BizOrch platform database."""

from datetime import date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Integer,
    JSON,
    Numeric,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from enterprise_system.app.persistence import EnterpriseBase


class EmployeeRecord(EnterpriseBase):
    __tablename__ = "enterprise_employees"

    employee_id: Mapped[str] = mapped_column(String(100), primary_key=True)
    display_name: Mapped[str] = mapped_column(String(100), nullable=False)
    department_code: Mapped[str] = mapped_column(String(100), nullable=False)
    manager_id: Mapped[str | None] = mapped_column(String(100), nullable=True)
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    employment_status: Mapped[str] = mapped_column(String(32), nullable=False)
    job_code: Mapped[str] = mapped_column(String(100), nullable=False)
    work_location_code: Mapped[str] = mapped_column(String(100), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )


class OrganizationUnitRecord(EnterpriseBase):
    """Authoritative enterprise department used by lifecycle validation."""

    __tablename__ = "enterprise_organization_units"

    department_code: Mapped[str] = mapped_column(String(100), primary_key=True)
    display_name: Mapped[str] = mapped_column(String(200), nullable=False)
    manager_id: Mapped[str] = mapped_column(String(100), nullable=False)
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)


class WorkLocationRecord(EnterpriseBase):
    """Authoritative office or plant location used by lifecycle validation."""

    __tablename__ = "enterprise_work_locations"

    location_code: Mapped[str] = mapped_column(String(100), primary_key=True)
    display_name: Mapped[str] = mapped_column(String(200), nullable=False)
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)


class JobProfileRecord(EnterpriseBase):
    """A fixed job profile referencing access and asset baselines."""

    __tablename__ = "enterprise_job_profiles"

    job_code: Mapped[str] = mapped_column(String(100), primary_key=True)
    display_name: Mapped[str] = mapped_column(String(200), nullable=False)
    department_code: Mapped[str] = mapped_column(
        ForeignKey("enterprise_organization_units.department_code"),
        nullable=False,
        index=True,
    )
    baseline_access_package_code: Mapped[str] = mapped_column(
        String(100), nullable=False
    )
    asset_profile_code: Mapped[str] = mapped_column(String(100), nullable=False)
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)


class CorporateAccountRecord(EnterpriseBase):
    """Simulated directory account state without credentials."""

    __tablename__ = "enterprise_corporate_accounts"

    account_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    employee_id: Mapped[str] = mapped_column(
        String(100), nullable=False, unique=True, index=True
    )
    username: Mapped[str] = mapped_column(
        String(100), nullable=False, unique=True, index=True
    )
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )


class AccessPackageRecord(EnterpriseBase):
    """Deterministic application-role baseline for one job family."""

    __tablename__ = "enterprise_access_packages"

    package_code: Mapped[str] = mapped_column(String(100), primary_key=True)
    display_name: Mapped[str] = mapped_column(String(200), nullable=False)
    role_bindings: Mapped[list[dict[str, str]]] = mapped_column(
        JSON, nullable=False
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)


class AssetTaskRecord(EnterpriseBase):
    """Office-asset coordination task, not an inventory transaction."""

    __tablename__ = "enterprise_asset_tasks"

    task_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    employee_id: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    task_type: Mapped[str] = mapped_column(String(32), nullable=False)
    asset_profile_code: Mapped[str] = mapped_column(String(100), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    idempotency_key: Mapped[str] = mapped_column(
        String(100), nullable=False, unique=True, index=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )


class EmployeeLifecycleRequestRecord(EnterpriseBase):
    """Enterprise-side lifecycle request projection used for conflict checks."""

    __tablename__ = "enterprise_employee_lifecycle_requests"

    request_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    request_type: Mapped[str] = mapped_column(String(32), nullable=False)
    subject_employee_id: Mapped[str] = mapped_column(
        String(100), nullable=False, index=True
    )
    initiator_id: Mapped[str] = mapped_column(String(100), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    effective_date: Mapped[date] = mapped_column(Date, nullable=False)
    safe_summary: Mapped[str] = mapped_column(String(500), nullable=False)
    idempotency_key: Mapped[str] = mapped_column(
        String(100), nullable=False, unique=True, index=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )


class CostCenterRecord(EnterpriseBase):
    """Authoritative departmental budget facts used by procurement policy."""

    __tablename__ = "enterprise_cost_centers"
    __table_args__ = (
        CheckConstraint("budget_total >= 0", name="ck_cost_center_total_nonnegative"),
        CheckConstraint("spent_amount >= 0", name="ck_cost_center_spent_nonnegative"),
        CheckConstraint(
            "reserved_amount >= 0", name="ck_cost_center_reserved_nonnegative"
        ),
    )

    cost_center_code: Mapped[str] = mapped_column(String(100), primary_key=True)
    display_name: Mapped[str] = mapped_column(String(200), nullable=False)
    department_code: Mapped[str] = mapped_column(
        ForeignKey("enterprise_organization_units.department_code"),
        nullable=False,
        index=True,
    )
    budget_owner_id: Mapped[str] = mapped_column(String(100), nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    budget_total: Mapped[Decimal] = mapped_column(Numeric(18, 2), nullable=False)
    spent_amount: Mapped[Decimal] = mapped_column(Numeric(18, 2), nullable=False)
    reserved_amount: Mapped[Decimal] = mapped_column(Numeric(18, 2), nullable=False)
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )


class ProcurementPolicyRecord(EnterpriseBase):
    """Structured, versioned procurement rule; RAG documents are not authority."""

    __tablename__ = "enterprise_procurement_policies"
    __table_args__ = (
        CheckConstraint(
            "level_one_limit > 0", name="ck_procurement_policy_level_one_positive"
        ),
        CheckConstraint(
            "level_two_limit > level_one_limit",
            name="ck_procurement_policy_levels_ordered",
        ),
    )

    policy_code: Mapped[str] = mapped_column(String(100), primary_key=True)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    level_one_limit: Mapped[Decimal] = mapped_column(Numeric(18, 2), nullable=False)
    level_two_limit: Mapped[Decimal] = mapped_column(Numeric(18, 2), nullable=False)
    procurement_approver_id: Mapped[str] = mapped_column(String(100), nullable=False)
    allowed_item_categories: Mapped[list[str]] = mapped_column(JSON, nullable=False)
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    effective_from: Mapped[date] = mapped_column(Date, nullable=False)


class ProcurementRequestRecord(EnterpriseBase):
    """Enterprise-side purchase-request projection; V5-02 exposes reads only."""

    __tablename__ = "enterprise_procurement_requests"

    request_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    external_workflow_run_id: Mapped[str] = mapped_column(
        String(36), nullable=False, unique=True, index=True
    )
    requester_id: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    cost_center_code: Mapped[str] = mapped_column(
        ForeignKey("enterprise_cost_centers.cost_center_code"),
        nullable=False,
        index=True,
    )
    estimated_total_amount: Mapped[Decimal] = mapped_column(
        Numeric(18, 2), nullable=False
    )
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    desired_date: Mapped[date] = mapped_column(Date, nullable=False)
    delivery_location_code: Mapped[str] = mapped_column(String(100), nullable=False)
    business_reason_summary: Mapped[str] = mapped_column(String(1000), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    policy_code: Mapped[str] = mapped_column(String(100), nullable=False)
    policy_version: Mapped[int] = mapped_column(Integer, nullable=False)
    idempotency_key: Mapped[str] = mapped_column(
        String(100), nullable=False, unique=True, index=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )


class ProcurementRequestItemRecord(EnterpriseBase):
    """Approved request lines, deliberately separate from an ecommerce catalogue."""

    __tablename__ = "enterprise_procurement_request_items"
    __table_args__ = (
        UniqueConstraint(
            "request_id", "line_no", name="uq_procurement_request_item_line"
        ),
        CheckConstraint("quantity > 0", name="ck_procurement_request_item_quantity"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    request_id: Mapped[str] = mapped_column(
        ForeignKey("enterprise_procurement_requests.request_id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    line_no: Mapped[int] = mapped_column(Integer, nullable=False)
    item_name: Mapped[str] = mapped_column(String(200), nullable=False)
    item_category: Mapped[str] = mapped_column(String(100), nullable=False)
    quantity: Mapped[int] = mapped_column(Integer, nullable=False)
    specification_note: Mapped[str | None] = mapped_column(String(500), nullable=True)


class BudgetReservationRecord(EnterpriseBase):
    """Schema reserved for V5 execution; V5-02 does not create reservations."""

    __tablename__ = "enterprise_budget_reservations"
    __table_args__ = (
        CheckConstraint("amount > 0", name="ck_budget_reservation_amount_positive"),
    )

    reservation_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    request_id: Mapped[str] = mapped_column(
        ForeignKey("enterprise_procurement_requests.request_id"),
        nullable=False,
        unique=True,
        index=True,
    )
    cost_center_code: Mapped[str] = mapped_column(
        ForeignKey("enterprise_cost_centers.cost_center_code"),
        nullable=False,
        index=True,
    )
    amount: Mapped[Decimal] = mapped_column(Numeric(18, 2), nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    idempotency_key: Mapped[str] = mapped_column(
        String(100), nullable=False, unique=True, index=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )


class ApplicationRecord(EnterpriseBase):
    __tablename__ = "enterprise_applications"

    application_code: Mapped[str] = mapped_column(String(100), primary_key=True)
    display_name: Mapped[str] = mapped_column(String(100), nullable=False)
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    allowed_role_codes: Mapped[list[str]] = mapped_column(JSON, nullable=False)


class UserAccessRecord(EnterpriseBase):
    __tablename__ = "enterprise_user_access"
    __table_args__ = (
        UniqueConstraint(
            "employee_id",
            "application_code",
            "role_code",
            name="uq_enterprise_user_access_role",
        ),
    )

    access_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    employee_id: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    application_code: Mapped[str] = mapped_column(String(100), nullable=False)
    role_code: Mapped[str] = mapped_column(String(100), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class AccessRequestRecord(EnterpriseBase):
    __tablename__ = "enterprise_access_requests"

    request_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    employee_id: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    application_code: Mapped[str] = mapped_column(String(100), nullable=False)
    role_code: Mapped[str] = mapped_column(String(100), nullable=False)
    duration_days: Mapped[int] = mapped_column(Integer, nullable=False)
    business_reason: Mapped[str] = mapped_column(String(1000), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class ExternalIdempotencyRecord(EnterpriseBase):
    """Defense-in-depth idempotency at the enterprise write boundary."""

    __tablename__ = "enterprise_idempotency_records"

    key: Mapped[str] = mapped_column(String(100), primary_key=True)
    operation_type: Mapped[str] = mapped_column(String(100), nullable=False)
    request_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    response_payload: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class EquipmentRecord(EnterpriseBase):
    """Authoritative equipment master data owned by the enterprise system."""

    __tablename__ = "enterprise_equipment"

    equipment_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    equipment_code: Mapped[str] = mapped_column(
        String(100), nullable=False, unique=True, index=True
    )
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    site_code: Mapped[str] = mapped_column(String(100), nullable=False)
    workshop_code: Mapped[str] = mapped_column(String(100), nullable=False)
    production_line: Mapped[str] = mapped_column(String(100), nullable=False)
    criticality: Mapped[str] = mapped_column(String(20), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    responsible_manager_id: Mapped[str] = mapped_column(String(100), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )


class MaintenanceHistoryRecord(EnterpriseBase):
    """Completed maintenance evidence used as read-only scenario context."""

    __tablename__ = "enterprise_maintenance_history"

    record_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    equipment_id: Mapped[str] = mapped_column(
        ForeignKey("enterprise_equipment.equipment_id"),
        nullable=False,
        index=True,
    )
    fault_summary: Mapped[str] = mapped_column(String(1000), nullable=False)
    resolution_summary: Mapped[str] = mapped_column(String(1000), nullable=False)
    completed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )


class MaintenanceWorkOrderRecord(EnterpriseBase):
    """Authoritative maintenance order created through the controlled write API."""

    __tablename__ = "enterprise_maintenance_work_orders"

    work_order_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    equipment_id: Mapped[str] = mapped_column(
        ForeignKey("enterprise_equipment.equipment_id"),
        nullable=False,
        index=True,
    )
    requester_id: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    fault_description: Mapped[str] = mapped_column(String(1000), nullable=False)
    observed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    production_impact: Mapped[str] = mapped_column(String(32), nullable=False)
    safety_observation: Mapped[str] = mapped_column(String(1000), nullable=False)
    business_reason: Mapped[str] = mapped_column(String(1000), nullable=False)
    priority: Mapped[str] = mapped_column(String(20), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    idempotency_key: Mapped[str] = mapped_column(
        String(100), nullable=False, unique=True, index=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )
