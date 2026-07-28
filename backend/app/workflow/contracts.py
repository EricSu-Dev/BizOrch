"""Stable requester-facing workflow progress contracts."""

from datetime import datetime

from pydantic import BaseModel, ConfigDict

from app.approval.contracts import ApprovalRouteProgressView
from app.workflow.state import WorkflowState


class WorkflowProgressEventView(BaseModel):
    """One safe lifecycle fact without internal workflow payloads."""

    model_config = ConfigDict(frozen=True)

    sequence: int
    event_type: str
    from_state: WorkflowState | None
    to_state: WorkflowState
    created_at: datetime


class WorkflowActionPlanStepProgressView(BaseModel):
    """Safe, requester-facing execution projection of one plan step."""

    model_config = ConfigDict(frozen=True)

    step_id: str
    step_order: int
    depends_on_step_ids: tuple[str, ...]
    action_type: str
    content_summary: str
    reversibility: str
    status: str
    attempt_count: int
    last_error_code: str | None
    started_at: datetime | None
    completed_at: datetime | None


class WorkflowActionPlanProgressView(BaseModel):
    """Read-only plan progress; excludes raw tool parameters and digests."""

    model_config = ConfigDict(frozen=True)

    plan_id: str
    plan_version: int
    plan_type: str
    subject_reference: str
    content_summary: str
    status: str
    steps: tuple[WorkflowActionPlanStepProgressView, ...]


class WorkflowBusinessSummaryView(BaseModel):
    """Scenario-controlled, requester-safe business facts for a plan."""

    model_config = ConfigDict(frozen=True)

    kind: str
    fields: dict[str, str]


class WorkflowProgressView(BaseModel):
    """Current workflow state and ordered safe audit facts for one requester."""

    model_config = ConfigDict(frozen=True)

    workflow_run_id: str
    ticket_id: str
    scenario_key: str
    state: WorkflowState
    version: int
    terminal: bool
    created_at: datetime
    updated_at: datetime
    events: tuple[WorkflowProgressEventView, ...]
    action_plan: WorkflowActionPlanProgressView | None = None
    approval_route: ApprovalRouteProgressView | None = None
    business_summary: WorkflowBusinessSummaryView | None = None
