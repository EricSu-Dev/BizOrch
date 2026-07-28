"""Structured employee input and trusted enterprise facts for maintenance."""

from datetime import datetime
from pydantic import BaseModel, ConfigDict, field_validator

from app.integrations.enterprise_ops import (
    EnterpriseOpsEquipment,
    EnterpriseOpsEquipmentStatusView,
    EnterpriseOpsMaintenanceHistory,
)
from app.scenarios.equipment_maintenance.intent_contracts import ProductionImpact


class MaintenanceRequestDraft(BaseModel):
    """Natural-language maintenance fields plus authenticated ownership."""

    model_config = ConfigDict(frozen=True, str_strip_whitespace=True)

    requester_id: str | None = None
    equipment_code: str | None = None
    fault_description: str | None = None
    observed_at: datetime | None = None
    production_impact: ProductionImpact | None = None
    safety_observation: str | None = None
    business_reason: str | None = None

    @field_validator(
        "requester_id",
        "equipment_code",
        "fault_description",
        "safety_observation",
        "business_reason",
        mode="before",
    )
    @classmethod
    def normalize_blank_text(cls, value: object) -> object:
        if isinstance(value, str) and not value.strip():
            return None
        return value

    @field_validator("equipment_code")
    @classmethod
    def normalize_equipment_code(cls, value: str | None) -> str | None:
        return value.upper() if value else None

    def missing_fields(self) -> tuple[str, ...]:
        values = self.model_dump()
        required = (
            "requester_id",
            "equipment_code",
            "fault_description",
            "observed_at",
            "production_impact",
            "safety_observation",
            "business_reason",
        )
        return tuple(name for name in required if values[name] is None)


class MaintenanceRequestContext(BaseModel):
    """Read-only CMMS/EAM facts; no proposed or executed write is included."""

    model_config = ConfigDict(frozen=True)

    equipment: EnterpriseOpsEquipment | None = None
    current_status: EnterpriseOpsEquipmentStatusView | None = None
    recent_maintenance: tuple[EnterpriseOpsMaintenanceHistory, ...] = ()
