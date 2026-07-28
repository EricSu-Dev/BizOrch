"""JSON endpoints for the first access-request vertical slice."""

from typing import Annotated

from fastapi import APIRouter, Depends, status
from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.api.dependencies import (
    CurrentActor,
    get_access_requests,
    get_current_actor,
    require_approver,
)
from app.approval.contracts import ApprovalDecisionType
from app.scenarios.access_management.commands import AccessRequestCommandService
from app.scenarios.access_management.contracts import AccessRequestDraft
from app.scenarios.access_management.workflow import AccessWorkflowSnapshot

router = APIRouter(prefix="/access-requests", tags=["access requests"])


class CreateAccessRequestBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    draft: AccessRequestDraft


class ProvideInformationBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_workflow_version: int = Field(ge=0)
    updates: AccessRequestDraft

    @model_validator(mode="after")
    def require_concrete_update(self) -> "ProvideInformationBody":
        values = self.updates.model_dump(exclude_unset=True)
        if not values or any(value is None for value in values.values()):
            raise ValueError("at least one concrete update is required")
        return self


class ApprovalDecisionBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    approval_id: str = Field(min_length=1)
    expected_workflow_version: int = Field(ge=0)
    decision: ApprovalDecisionType
    comment: str | None = Field(default=None, max_length=1000)


@router.post(
    "",
    response_model=AccessWorkflowSnapshot,
    status_code=status.HTTP_201_CREATED,
)
def create_access_request(
    body: CreateAccessRequestBody,
    actor: Annotated[CurrentActor, Depends(get_current_actor)],
    service: Annotated[AccessRequestCommandService, Depends(get_access_requests)],
) -> AccessWorkflowSnapshot:
    return service.create(body.draft, actor_id=actor.user_id)


@router.get("/{workflow_run_id}", response_model=AccessWorkflowSnapshot)
def get_access_request(
    workflow_run_id: str,
    actor: Annotated[CurrentActor, Depends(get_current_actor)],
    service: Annotated[AccessRequestCommandService, Depends(get_access_requests)],
) -> AccessWorkflowSnapshot:
    return service.get(workflow_run_id, actor_id=actor.user_id)


@router.post(
    "/{workflow_run_id}/information",
    response_model=AccessWorkflowSnapshot,
)
def provide_access_request_information(
    workflow_run_id: str,
    body: ProvideInformationBody,
    actor: Annotated[CurrentActor, Depends(get_current_actor)],
    service: Annotated[AccessRequestCommandService, Depends(get_access_requests)],
) -> AccessWorkflowSnapshot:
    return service.provide_information(
        workflow_run_id=workflow_run_id,
        expected_workflow_version=body.expected_workflow_version,
        updates=body.updates,
        actor_id=actor.user_id,
    )


@router.post(
    "/{workflow_run_id}/approval-decisions",
    response_model=AccessWorkflowSnapshot,
)
def decide_access_request(
    workflow_run_id: str,
    body: ApprovalDecisionBody,
    actor: Annotated[CurrentActor, Depends(require_approver)],
    service: Annotated[AccessRequestCommandService, Depends(get_access_requests)],
) -> AccessWorkflowSnapshot:
    return service.decide(
        workflow_run_id=workflow_run_id,
        approval_id=body.approval_id,
        expected_workflow_version=body.expected_workflow_version,
        actor_id=actor.user_id,
        decision=body.decision,
        comment=body.comment,
    )
