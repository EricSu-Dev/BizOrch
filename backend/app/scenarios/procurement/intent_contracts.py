"""Pure model-extractable procurement fields without command dependencies."""

from pydantic import BaseModel, ConfigDict


class ProcurementIntentFields(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    items: tuple[dict[str, object], ...] = ()
    estimated_total_amount: object | None = None
    cost_center_code: str | None = None
    desired_date: object | None = None
    delivery_location_code: str | None = None
    business_reason: str | None = None
