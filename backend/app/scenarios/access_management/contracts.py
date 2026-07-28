"""Structured input and enterprise context for access requests."""

from pydantic import BaseModel, ConfigDict, Field, field_validator


class AccessRequestDraft(BaseModel):
    """Fields extracted from an employee's natural-language request."""

    model_config = ConfigDict(frozen=True, str_strip_whitespace=True)

    employee_id: str | None = None
    application_code: str | None = None
    role_code: str | None = None
    duration_days: int | None = Field(default=None, ge=1)
    business_reason: str | None = None

    @field_validator(
        "employee_id",
        "application_code",
        "role_code",
        "business_reason",
        mode="before",
    )
    @classmethod
    def normalize_blank_text(cls, value: object) -> object:
        """Treat blank model output as missing rather than valid data."""
        if isinstance(value, str) and not value.strip():
            return None
        return value

    def missing_fields(self) -> tuple[str, ...]:
        """Return stable field names that must be supplied before policy runs."""
        values = self.model_dump()
        required = (
            "employee_id",
            "application_code",
            "role_code",
            "duration_days",
            "business_reason",
        )
        return tuple(name for name in required if values[name] is None)


class AccessRequestContext(BaseModel):
    """Read-only facts returned by the simulated enterprise system."""

    model_config = ConfigDict(frozen=True)

    existing_role_codes: frozenset[str] = frozenset()
    manager_id: str | None = None
    application_owner_id: str | None = None
    security_officer_id: str | None = None
