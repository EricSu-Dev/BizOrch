"""V5 procurement intake contracts; they intentionally contain no approval route."""

from datetime import date
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.integrations.enterprise_ops import (
    EnterpriseOpsCostCenter,
    EnterpriseOpsEmployeeLifecycleProfile,
    EnterpriseOpsProcurementPolicy,
    EnterpriseOpsProcurementRequest,
)


class ProcurementItemDraft(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    item_name: str | None = None
    item_category: str | None = None
    quantity: int | None = Field(default=None, ge=1)
    specification_note: str | None = None

    @field_validator("item_name", "item_category")
    @classmethod
    def reject_blank_item_text(cls, value: str | None) -> str | None:
        if value is not None and not value.strip():
            raise ValueError("item text must not be blank")
        return value


class ProcurementRequestDraft(BaseModel):
    """Model-extracted request facts; actor identity is assigned by the server."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    requester_id: str
    items: tuple[ProcurementItemDraft, ...] = ()
    estimated_total_amount: Decimal | None = Field(default=None, gt=0)
    cost_center_code: str | None = None
    desired_date: date | None = None
    delivery_location_code: str | None = None
    business_reason: str | None = None

    @field_validator(
        "requester_id",
        "cost_center_code",
        "delivery_location_code",
        "business_reason",
    )
    @classmethod
    def reject_blank_request_text(cls, value: str | None) -> str | None:
        if value is not None and not value.strip():
            raise ValueError("request text must not be blank")
        return value

    def missing_fields(self) -> tuple[str, ...]:
        missing: list[str] = []
        if not self.items or any(
            item.item_name is None
            or item.item_category is None
            or item.quantity is None
            for item in self.items
        ):
            missing.append("items")
        for field in (
            "estimated_total_amount",
            "cost_center_code",
            "desired_date",
            "delivery_location_code",
            "business_reason",
        ):
            if getattr(self, field) is None:
                missing.append(field)
        return tuple(missing)


class ProcurementRequestContext(BaseModel):
    """Trusted facts gathered before V5 policy/approval work is allowed."""

    model_config = ConfigDict(frozen=True)

    requester_profile: EnterpriseOpsEmployeeLifecycleProfile | None = None
    cost_center: EnterpriseOpsCostCenter | None = None
    policy: EnterpriseOpsProcurementPolicy | None = None
    open_requests: tuple[EnterpriseOpsProcurementRequest, ...] = ()
    approver_profiles: tuple[EnterpriseOpsEmployeeLifecycleProfile, ...] = ()
