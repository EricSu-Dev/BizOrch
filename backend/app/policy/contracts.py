"""Shared output types for deterministic scenario policy engines."""

from enum import Enum

from pydantic import BaseModel, ConfigDict


class RiskLevel(str, Enum):
    """Risk classification understood by the platform approval layer."""

    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"


class PolicyOutcome(str, Enum):
    """A deterministic next-step recommendation for the workflow."""

    NEEDS_INPUT = "NEEDS_INPUT"
    DENIED = "DENIED"
    NO_ACTION = "NO_ACTION"
    APPROVAL_REQUIRED = "APPROVAL_REQUIRED"
    HUMAN_REVIEW = "HUMAN_REVIEW"


class PolicyDecision(BaseModel):
    """Auditable policy result that contains no model reasoning trace."""

    model_config = ConfigDict(frozen=True)

    outcome: PolicyOutcome
    risk_level: RiskLevel
    reason_codes: tuple[str, ...]
    approver_id: str | None = None
    approval_route: str | None = None

