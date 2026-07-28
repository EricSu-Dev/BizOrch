"""Pure model-extractable access fields without business service dependencies."""

from pydantic import BaseModel, ConfigDict, Field


class AccessIntentFields(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    application_code: str | None = None
    role_code: str | None = None
    duration_days: int | None = Field(default=None, ge=1)
    business_reason: str | None = None
