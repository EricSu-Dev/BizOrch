"""Request dependencies for application services and authenticated identity."""

from typing import Annotated

from fastapi import Depends, HTTPException, Request, Security, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, ConfigDict

from app.runtime import ApplicationRuntime
from app.auth.service import (
    AuthService,
    InsufficientRoleError,
    InvalidAccessTokenError,
)
from app.approval.workbench import ApprovalWorkbenchService
from app.approval.dispatcher import ApprovalDecisionDispatcher
from app.agents.orchestrator import MultiAgentService
from app.scenarios.access_management.commands import AccessRequestCommandService
from app.knowledge.service import KnowledgeService
from app.tickets.service import TicketProjectionService
from app.conversations.service import ConversationService
from app.evaluation.catalog import EvaluationSuiteCatalog, resolve_evaluation_repository_root
from app.evaluation.service import EvaluationService
from app.evaluation.governance import EvaluationGovernanceService
from app.persistence.database import build_session_factory
from pathlib import Path
from app.workflow.query import WorkflowProgressQueryService
from app.workflow.human_review import HumanReviewService


class CurrentActor(BaseModel):
    """Authenticated identity exposed to business endpoints."""

    model_config = ConfigDict(frozen=True)

    user_id: str
    username: str | None = None
    roles: frozenset[str] = frozenset()


_bearer_scheme = HTTPBearer(auto_error=False)


def get_runtime(request: Request) -> ApplicationRuntime:
    runtime = getattr(request.app.state, "runtime", None)
    if runtime is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="application runtime is not configured",
        )
    return runtime


def get_access_requests(
    runtime: Annotated[ApplicationRuntime, Depends(get_runtime)],
) -> AccessRequestCommandService:
    return runtime.access_requests


def get_tickets(
    runtime: Annotated[ApplicationRuntime, Depends(get_runtime)],
) -> TicketProjectionService:
    return runtime.tickets


def get_workflow_progress(
    runtime: Annotated[ApplicationRuntime, Depends(get_runtime)],
) -> WorkflowProgressQueryService:
    return runtime.workflow_progress


def get_human_review(
    runtime: Annotated[ApplicationRuntime, Depends(get_runtime)],
) -> HumanReviewService:
    return runtime.human_review


def get_approval_workbench(
    runtime: Annotated[ApplicationRuntime, Depends(get_runtime)],
) -> ApprovalWorkbenchService:
    return runtime.approval_workbench


def get_approval_decisions(
    runtime: Annotated[ApplicationRuntime, Depends(get_runtime)],
) -> ApprovalDecisionDispatcher:
    return runtime.approval_decisions


def get_knowledge_service(
    runtime: Annotated[ApplicationRuntime, Depends(get_runtime)],
) -> KnowledgeService:
    return runtime.knowledge


def get_multi_agent_service(
    runtime: Annotated[ApplicationRuntime, Depends(get_runtime)],
) -> MultiAgentService:
    return runtime.multi_agent


def get_conversation_service(
    runtime: Annotated[ApplicationRuntime, Depends(get_runtime)],
) -> ConversationService:
    return runtime.conversations


def get_auth_service(
    runtime: Annotated[ApplicationRuntime, Depends(get_runtime)],
) -> AuthService:
    return runtime.auth


def get_evaluation_catalog() -> EvaluationSuiteCatalog:
    return EvaluationSuiteCatalog(resolve_evaluation_repository_root(Path(__file__)))


def get_evaluation_service(
    runtime: Annotated[ApplicationRuntime, Depends(get_runtime)],
) -> EvaluationService:
    return EvaluationService(build_session_factory(runtime.engine), get_evaluation_catalog())


def get_evaluation_governance(
    runtime: Annotated[ApplicationRuntime, Depends(get_runtime)],
) -> EvaluationGovernanceService:
    return EvaluationGovernanceService(
        build_session_factory(runtime.engine),
        get_evaluation_catalog(),
    )


def get_bearer_token(
    credentials: Annotated[
        HTTPAuthorizationCredentials | None,
        Security(_bearer_scheme),
    ],
) -> str:
    if credentials is None or credentials.scheme.lower() != "bearer":
        raise InvalidAccessTokenError("authentication required")
    return credentials.credentials


def get_current_actor(
    token: Annotated[str, Depends(get_bearer_token)],
    auth: Annotated[AuthService, Depends(get_auth_service)],
) -> CurrentActor:
    principal = auth.authenticate(token)
    return CurrentActor(
        user_id=principal.employee_id,
        username=principal.username,
        roles=frozenset(role.value for role in principal.roles),
    )


def require_approver(
    actor: Annotated[CurrentActor, Depends(get_current_actor)],
) -> CurrentActor:
    if not actor.roles.intersection({"approver", "admin"}):
        raise InsufficientRoleError(actor.user_id)
    return actor


def require_human_reviewer(
    actor: Annotated[CurrentActor, Depends(get_current_actor)],
) -> CurrentActor:
    if not actor.roles.intersection({"operator", "admin"}):
        raise InsufficientRoleError(actor.user_id)
    return actor


def require_knowledge_manager(
    actor: Annotated[CurrentActor, Depends(get_current_actor)],
) -> CurrentActor:
    if not actor.roles.intersection({"operator", "admin"}):
        raise InsufficientRoleError(actor.user_id)
    return actor


def require_evaluation_manager(
    actor: Annotated[CurrentActor, Depends(get_current_actor)],
) -> CurrentActor:
    """Allow only AI operators and administrators into the evaluation center."""
    if not actor.roles.intersection({"operator", "admin"}):
        raise InsufficientRoleError(actor.user_id)
    return actor


def require_hr_operator(
    actor: Annotated[CurrentActor, Depends(get_current_actor)],
) -> CurrentActor:
    """Allow employee-lifecycle intake only to HR operators or administrators."""
    if not actor.roles.intersection({"hr", "admin"}):
        raise InsufficientRoleError(actor.user_id)
    return actor


def require_admin(
    actor: Annotated[CurrentActor, Depends(get_current_actor)],
) -> CurrentActor:
    if "admin" not in actor.roles:
        raise InsufficientRoleError(actor.user_id)
    return actor
