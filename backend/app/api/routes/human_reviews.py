"""Restricted workbench for recording manual reconciliation outcomes."""

from typing import Annotated

from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict

from app.api.dependencies import (
    CurrentActor,
    get_human_review,
    require_human_reviewer,
)
from app.workflow.human_review import (
    HumanReviewDecision,
    HumanReviewItem,
    HumanReviewService,
)
from app.workflow.state import WorkflowState

router = APIRouter(prefix="/human-reviews", tags=["human reviews"])


class HumanReviewResult(BaseModel):
    model_config = ConfigDict(frozen=True)

    workflow_run_id: str
    state: WorkflowState


@router.get("", response_model=tuple[HumanReviewItem, ...])
def list_human_reviews(
    actor: Annotated[CurrentActor, Depends(require_human_reviewer)],
    service: Annotated[HumanReviewService, Depends(get_human_review)],
) -> tuple[HumanReviewItem, ...]:
    return service.list_pending()


@router.post("/{workflow_run_id}/decisions", response_model=HumanReviewResult)
def decide_human_review(
    workflow_run_id: str,
    body: HumanReviewDecision,
    actor: Annotated[CurrentActor, Depends(require_human_reviewer)],
    service: Annotated[HumanReviewService, Depends(get_human_review)],
) -> HumanReviewResult:
    state = service.decide(workflow_run_id, actor_id=actor.user_id, decision=body)
    return HumanReviewResult(workflow_run_id=workflow_run_id, state=state)
