"""Approval status, decision and fixed serial-route contracts."""

import hashlib
import json
from datetime import datetime
from enum import Enum

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.actions.contracts import ActionPlan
from app.workflow.state import WorkflowState


class ApprovalStatus(str, Enum):
    """Lifecycle of one human approval task."""

    QUEUED = "QUEUED"
    PENDING = "PENDING"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    CANCELLED = "CANCELLED"


class ApprovalSequenceStatus(str, Enum):
    """Lifecycle of one fixed-order approval sequence."""

    PENDING = "PENDING"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    CANCELLED = "CANCELLED"


class ApprovalDecisionType(str, Enum):
    """Final decision an assigned approver can submit."""

    APPROVE = "APPROVE"
    REJECT = "REJECT"


class ApprovalSubjectType(str, Enum):
    """Kinds of immutable content that a human may approve."""

    ACTION = "ACTION"
    PLAN = "PLAN"


class ApprovalRouteStage(BaseModel):
    """One domain-neutral stage in a fixed serial approval route."""

    model_config = ConfigDict(frozen=True)

    stage_order: int = Field(ge=1)
    stage_code: str
    approver_id: str

    @field_validator("stage_code", "approver_id")
    @classmethod
    def reject_blank_text(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("approval route stage text must not be blank")
        return normalized


class ApprovalRoute(BaseModel):
    """Versioned, immutable route supplied by a deterministic scenario policy."""

    model_config = ConfigDict(frozen=True)

    route_version: int = Field(ge=1)
    stages: tuple[ApprovalRouteStage, ...] = Field(min_length=1, max_length=3)

    @model_validator(mode="after")
    def validate_fixed_serial_route(self) -> "ApprovalRoute":
        orders = [stage.stage_order for stage in self.stages]
        if sorted(orders) != list(range(1, len(self.stages) + 1)):
            raise ValueError("approval stage orders must be contiguous from one")
        stage_codes = [stage.stage_code for stage in self.stages]
        if len(stage_codes) != len(set(stage_codes)):
            raise ValueError("approval stage codes must be unique")
        approvers = [stage.approver_id for stage in self.stages]
        if len(approvers) != len(set(approvers)):
            raise ValueError("approval stage approvers must be unique")
        return self

    def require_separation_of_duties(self, requester_id: str) -> None:
        """Reject self-approval without learning any scenario-specific roles."""
        normalized_requester = requester_id.strip()
        if not normalized_requester:
            raise ValueError("requester_id must not be blank")
        if any(
            stage.approver_id == normalized_requester for stage in self.stages
        ):
            raise ValueError("requester must not approve their own request")

    def canonical_content(self, plan: ActionPlan) -> str:
        """Return the stable route representation bound to one exact plan."""
        return json.dumps(
            {
                "action_plan_id": plan.plan_id,
                "action_plan_version": plan.version,
                "action_plan_digest": plan.content_digest,
                "route_version": self.route_version,
                "stages": [
                    {
                        "stage_order": stage.stage_order,
                        "stage_code": stage.stage_code,
                        "approver_id": stage.approver_id,
                    }
                    for stage in sorted(
                        self.stages, key=lambda item: item.stage_order
                    )
                ],
            },
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )

    def content_digest(self, plan: ActionPlan) -> str:
        """Calculate the SHA-256 route digest for an exact plan version."""
        return hashlib.sha256(
            self.canonical_content(plan).encode("utf-8")
        ).hexdigest()


class ApprovalSequenceStageView(BaseModel):
    """Safe stage projection used by approval and workflow read models."""

    model_config = ConfigDict(frozen=True)

    approval_id: str
    stage_order: int
    stage_code: str
    approver_id: str
    status: ApprovalStatus
    created_at: datetime
    decided_at: datetime | None


class ApprovalSequenceView(BaseModel):
    """Immutable sequence identity plus its mutable stage progress."""

    model_config = ConfigDict(frozen=True)

    sequence_id: str
    workflow_run_id: str
    action_plan_id: str
    action_plan_version: int
    action_plan_digest: str
    route_version: int
    route_digest: str
    status: ApprovalSequenceStatus
    current_stage_order: int | None
    stages: tuple[ApprovalSequenceStageView, ...]
    created_at: datetime
    completed_at: datetime | None


class ApprovalRouteStageProgressView(BaseModel):
    """Browser-safe progress for one persisted approval stage.

    Digests and plan internals intentionally never cross this projection.
    """

    model_config = ConfigDict(frozen=True)

    approval_id: str
    stage_order: int
    stage_code: str
    approver_id: str
    status: ApprovalStatus
    decided_at: datetime | None


class ApprovalRouteProgressView(BaseModel):
    """Browser-safe summary of a fixed serial approval route."""

    model_config = ConfigDict(frozen=True)

    sequence_id: str
    route_version: int
    status: ApprovalSequenceStatus
    current_stage_order: int | None
    stages: tuple[ApprovalRouteStageProgressView, ...]
    completed_at: datetime | None


class ApprovalPlanStepView(BaseModel):
    """Safe plan-step content displayed to the assigned approver."""

    model_config = ConfigDict(frozen=True)

    step_id: str
    step_order: int
    depends_on_step_ids: tuple[str, ...]
    action_id: str
    action_version: int
    action_type: str
    target_resource: str
    parameters: dict[str, object]
    content_summary: str
    reversibility: str
    compensation_action_type: str | None


class ApprovalPlanView(BaseModel):
    """Exact composite plan version protected by one approval."""

    model_config = ConfigDict(frozen=True)

    plan_id: str
    plan_version: int
    scenario_key: str
    plan_type: str
    subject_reference: str
    content_summary: str
    steps: tuple[ApprovalPlanStepView, ...]


class ApprovalDecisionView(BaseModel):
    """Safe, immutable decision details shown in approval history."""

    model_config = ConfigDict(frozen=True)

    decision: ApprovalDecisionType
    decided_by: str
    comment: str | None
    created_at: datetime


class ApprovalTaskView(BaseModel):
    """Approval workbench view joined with exact action and workflow facts."""

    model_config = ConfigDict(frozen=True)

    approval_id: str
    workflow_run_id: str
    workflow_state: str
    workflow_version: int
    ticket_id: str | None
    requester_id: str | None
    approval_subject_type: ApprovalSubjectType = ApprovalSubjectType.ACTION
    action_id: str | None
    action_version: int | None
    action_type: str | None
    target_resource: str | None
    parameters: dict[str, object] | None
    content_summary: str
    action_plan: ApprovalPlanView | None = None
    approval_sequence_id: str | None = None
    stage_order: int | None = None
    stage_code: str | None = None
    approval_route: ApprovalRouteProgressView | None = None
    status: ApprovalStatus
    created_at: datetime
    decided_at: datetime | None
    decision: ApprovalDecisionView | None


class ApprovalDecisionResult(BaseModel):
    """Domain-neutral result of deciding and resuming one approval workflow."""

    model_config = ConfigDict(frozen=True)

    approval_id: str
    approval_status: ApprovalStatus
    scenario_key: str
    workflow_run_id: str
    workflow_state: WorkflowState
    workflow_version: int
    checkpoint_pending: bool
