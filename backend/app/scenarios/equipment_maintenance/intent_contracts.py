"""Pure model-extractable maintenance fields without command dependencies."""

from datetime import datetime
from enum import Enum

from pydantic import BaseModel, ConfigDict


class ProductionImpact(str, Enum):
    """Observable production effect, not an AI-generated risk score."""

    NONE = "NONE"
    SLOWDOWN = "SLOWDOWN"
    STOPPED = "STOPPED"


class MaintenanceIntentFields(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    equipment_code: str | None = None
    fault_description: str | None = None
    observed_at: datetime | None = None
    production_impact: ProductionImpact | None = None
    safety_observation: str | None = None
    business_reason: str | None = None
