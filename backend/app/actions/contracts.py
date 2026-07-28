"""Immutable contracts shared by action proposals and Action Gateway ports."""

from __future__ import annotations

import hashlib
import json
from enum import Enum
from typing import Any

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    JsonValue,
    computed_field,
    field_validator,
    model_validator,
)


class ActionProposal(BaseModel):
    """A versioned, digestible proposal that an approval can safely bind to."""

    model_config = ConfigDict(frozen=True)

    action_id: str
    action_type: str
    target_resource: str
    parameters: dict[str, JsonValue] = Field(default_factory=dict)
    version: int = Field(default=1, ge=1)
    content_summary: str

    @field_validator("action_id", "action_type", "target_resource", "content_summary")
    @classmethod
    def reject_blank_text(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("action proposal text fields must not be blank")
        return normalized

    def canonical_content(self) -> str:
        """Return the stable JSON representation protected by approval."""
        return json.dumps(
            {
                "action_id": self.action_id,
                "action_type": self.action_type,
                "target_resource": self.target_resource,
                "parameters": self.parameters,
                "version": self.version,
                "content_summary": self.content_summary,
            },
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )

    @computed_field
    @property
    def content_digest(self) -> str:
        """SHA-256 fingerprint used to invalidate approval after any change."""
        return hashlib.sha256(self.canonical_content().encode("utf-8")).hexdigest()


class ActionPlanStatus(str, Enum):
    """Mutable execution status of one immutable plan version."""

    DRAFT = "DRAFT"
    PENDING_APPROVAL = "PENDING_APPROVAL"
    APPROVED = "APPROVED"
    EXECUTING = "EXECUTING"
    WAITING_HUMAN = "WAITING_HUMAN"
    COMPLETED = "COMPLETED"
    CANCELLED = "CANCELLED"


class ActionPlanStepStatus(str, Enum):
    """Execution lifecycle of one action inside a plan."""

    PENDING = "PENDING"
    BLOCKED = "BLOCKED"
    EXECUTING = "EXECUTING"
    SUCCEEDED = "SUCCEEDED"
    REPLAYED = "REPLAYED"
    FAILED = "FAILED"
    RESULT_UNKNOWN = "RESULT_UNKNOWN"
    VERIFICATION_FAILED = "VERIFICATION_FAILED"
    COMPENSATION_PENDING = "COMPENSATION_PENDING"
    COMPENSATED = "COMPENSATED"
    COMPENSATION_FAILED = "COMPENSATION_FAILED"
    SKIPPED = "SKIPPED"


class ActionStepReversibility(str, Enum):
    """Whether a step may be compensated after a partial plan failure."""

    REVERSIBLE = "REVERSIBLE"
    MANUAL_ONLY = "MANUAL_ONLY"
    IRREVERSIBLE = "IRREVERSIBLE"


class ActionPlanStep(BaseModel):
    """One immutable, dependency-aware action in an approved plan."""

    model_config = ConfigDict(frozen=True)

    step_id: str
    step_order: int = Field(ge=1)
    depends_on_step_ids: tuple[str, ...] = ()
    proposal: ActionProposal
    reversibility: ActionStepReversibility = ActionStepReversibility.MANUAL_ONLY
    compensation_action_type: str | None = None

    @field_validator("step_id")
    @classmethod
    def reject_blank_step_id(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("action plan step id must not be blank")
        return normalized

    @field_validator("depends_on_step_ids")
    @classmethod
    def validate_dependency_ids(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        normalized = tuple(item.strip() for item in value)
        if any(not item for item in normalized):
            raise ValueError("action plan dependency id must not be blank")
        if len(normalized) != len(set(normalized)):
            raise ValueError("action plan dependency ids must be unique")
        return normalized

    @field_validator("compensation_action_type")
    @classmethod
    def normalize_compensation_action(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        if not normalized:
            raise ValueError("compensation action type must not be blank")
        return normalized

    @model_validator(mode="after")
    def validate_compensation_contract(self) -> "ActionPlanStep":
        if self.step_id in self.depends_on_step_ids:
            raise ValueError("action plan step cannot depend on itself")
        if (
            self.reversibility is not ActionStepReversibility.REVERSIBLE
            and self.compensation_action_type is not None
        ):
            raise ValueError(
                "only reversible steps may declare a compensation action"
            )
        return self


class ActionPlan(BaseModel):
    """Versioned plan whose digest protects every step and dependency."""

    model_config = ConfigDict(frozen=True)

    plan_id: str
    scenario_key: str
    plan_type: str
    subject_reference: str
    version: int = Field(default=1, ge=1)
    content_summary: str
    steps: tuple[ActionPlanStep, ...] = Field(min_length=1)

    @field_validator(
        "plan_id",
        "scenario_key",
        "plan_type",
        "subject_reference",
        "content_summary",
    )
    @classmethod
    def reject_blank_plan_text(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("action plan text fields must not be blank")
        return normalized

    @model_validator(mode="after")
    def validate_step_graph(self) -> "ActionPlan":
        step_ids = [step.step_id for step in self.steps]
        orders = [step.step_order for step in self.steps]
        action_versions = [
            (step.proposal.action_id, step.proposal.version) for step in self.steps
        ]
        if len(step_ids) != len(set(step_ids)):
            raise ValueError("action plan step ids must be unique")
        if len(orders) != len(set(orders)):
            raise ValueError("action plan step orders must be unique")
        if sorted(orders) != list(range(1, len(self.steps) + 1)):
            raise ValueError("action plan step orders must be contiguous from one")
        if len(action_versions) != len(set(action_versions)):
            raise ValueError("an action proposal version may belong to only one step")

        order_by_id = {step.step_id: step.step_order for step in self.steps}
        for step in self.steps:
            for dependency_id in step.depends_on_step_ids:
                dependency_order = order_by_id.get(dependency_id)
                if dependency_order is None:
                    raise ValueError("action plan dependency does not exist")
                if dependency_order >= step.step_order:
                    raise ValueError(
                        "action plan dependencies must reference an earlier step"
                    )
        return self

    def canonical_content(self) -> str:
        """Return stable content protected by plan-level human approval."""
        return json.dumps(
            {
                "plan_id": self.plan_id,
                "scenario_key": self.scenario_key,
                "plan_type": self.plan_type,
                "subject_reference": self.subject_reference,
                "version": self.version,
                "content_summary": self.content_summary,
                "steps": [
                    {
                        "step_id": step.step_id,
                        "step_order": step.step_order,
                        "depends_on_step_ids": step.depends_on_step_ids,
                        "action_content_digest": step.proposal.content_digest,
                        "reversibility": step.reversibility.value,
                        "compensation_action_type": step.compensation_action_type,
                    }
                    for step in sorted(self.steps, key=lambda item: item.step_order)
                ],
            },
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )

    @computed_field
    @property
    def content_digest(self) -> str:
        return hashlib.sha256(self.canonical_content().encode("utf-8")).hexdigest()

    def contains_proposal(self, proposal: ActionProposal) -> bool:
        """Check exact action membership in this approved plan version."""
        return any(
            step.proposal.action_id == proposal.action_id
            and step.proposal.version == proposal.version
            and step.proposal.content_digest == proposal.content_digest
            for step in self.steps
        )


class ToolExecutionStatus(str, Enum):
    """Outcome categories returned by an external write tool."""

    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    UNKNOWN = "UNKNOWN"


class ToolExecutionResult(BaseModel):
    """Normalized write-tool result; UNKNOWN must never be blindly retried."""

    model_config = ConfigDict(frozen=True)

    status: ToolExecutionStatus
    external_reference: str | None = None
    details: dict[str, JsonValue] = Field(default_factory=dict)


class VerificationResult(BaseModel):
    """Independent read-after-write verification result."""

    model_config = ConfigDict(frozen=True)

    confirmed: bool
    details: dict[str, JsonValue] = Field(default_factory=dict)


class ActionGatewayOutcome(str, Enum):
    """Stable result exposed to the workflow layer."""

    SUCCEEDED = "SUCCEEDED"
    REPLAYED = "REPLAYED"
    TOOL_FAILED = "TOOL_FAILED"
    RESULT_UNKNOWN = "RESULT_UNKNOWN"
    VERIFICATION_FAILED = "VERIFICATION_FAILED"


class ActionGatewayResult(BaseModel):
    """Auditable Action Gateway result without hidden reasoning."""

    model_config = ConfigDict(frozen=True)

    outcome: ActionGatewayOutcome
    action_id: str
    action_version: int
    idempotency_key: str
    tool_result: ToolExecutionResult | None = None
    verification: VerificationResult | None = None
    replay_payload: dict[str, Any] | None = None
