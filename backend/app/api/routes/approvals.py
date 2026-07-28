"""Read-only endpoints for the authenticated approver's workbench."""

from typing import Annotated

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, ConfigDict, Field

from app.api.dependencies import (
    CurrentActor,
    get_approval_decisions,
    get_approval_workbench,
    require_approver,
)
from app.approval.contracts import (
    ApprovalDecisionResult,
    ApprovalDecisionType,
    ApprovalStatus,
    ApprovalTaskView,
)
from app.approval.dispatcher import ApprovalDecisionDispatcher
from app.approval.workbench import ApprovalWorkbenchService

router = APIRouter(prefix="/approvals", tags=["approvals"])


class ApprovalDecisionBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    expected_workflow_version: int = Field(ge=0)
    decision: ApprovalDecisionType
    comment: str | None = Field(default=None, max_length=1000)


@router.get("", response_model=tuple[ApprovalTaskView, ...])
def list_my_approval_tasks(
    actor: Annotated[CurrentActor, Depends(require_approver)],
    service: Annotated[
        ApprovalWorkbenchService,
        Depends(get_approval_workbench),
    ],
    approval_status: Annotated[ApprovalStatus | None, Query(alias="status")] = None,
) -> tuple[ApprovalTaskView, ...]:
    return service.list_for_approver(
        actor.user_id,
        status=approval_status,
    )


@router.get("/{approval_id}", response_model=ApprovalTaskView)
def get_my_approval_task(
    approval_id: str,
    actor: Annotated[CurrentActor, Depends(require_approver)],
    service: Annotated[
        ApprovalWorkbenchService,
        Depends(get_approval_workbench),
    ],
) -> ApprovalTaskView:
    return service.get_for_approver(
        approval_id,
        approver_id=actor.user_id,
    )


@router.post(
    "/{approval_id}/decisions",
    response_model=ApprovalDecisionResult,
)
def decide_my_approval_task(
    approval_id: str,
    body: ApprovalDecisionBody,
    actor: Annotated[CurrentActor, Depends(require_approver)],
    dispatcher: Annotated[
        ApprovalDecisionDispatcher,
        Depends(get_approval_decisions),
    ],
) -> ApprovalDecisionResult:
    return dispatcher.decide(
        approval_id=approval_id,
        expected_workflow_version=body.expected_workflow_version,
        actor_id=actor.user_id,
        decision=body.decision,
        comment=body.comment,
    )
