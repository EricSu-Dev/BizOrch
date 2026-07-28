"""Stable public error envelope and domain exception mappings."""

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from app.auth.service import (
    AvatarFileTooLargeError,
    AvatarNotFoundError,
    CurrentPasswordInvalidError,
    InsufficientRoleError,
    InvalidAccessTokenError,
    InvalidCredentialsError,
    PasswordReuseError,
    UnsupportedAvatarFileError,
    UserAlreadyExistsError,
)
from app.auth.avatar_storage import (
    AvatarStorageConfigurationError,
    AvatarStorageOperationError,
)
from app.agents.llm import (
    AgentModelConfigurationError,
    AgentModelExecutionError,
    AgentModelResponseError,
)
from app.agents.orchestrator import MultiAgentOrchestrationError
from app.approval.repository import (
    ApprovalActorMismatchError,
    ApprovalConflictError,
    ApprovalNotFoundError,
)
from app.approval.checkpoint import ApprovalCheckpointError
from app.approval.sequence_checkpoint import ApprovalSequenceCheckpointError
from app.approval.sequences import ApprovalSequenceValidationError
from app.approval.dispatcher import UnsupportedApprovalScenarioError
from app.integrations.enterprise_ops import EnterpriseOpsClientError
from app.knowledge.embeddings import EmbeddingConfigurationError
from app.knowledge.files import (
    KnowledgeFileDependencyError,
    KnowledgeFileError,
    KnowledgeFileTooLargeError,
    UnsupportedKnowledgeFileError,
)
from app.knowledge.repository import (
    KnowledgeDocumentNotFoundError,
    KnowledgeIndexJobNotFoundError,
)
from app.knowledge.service import (
    KnowledgeDocumentConflictError,
    KnowledgeIndexRetryNotAllowedError,
    KnowledgeIndexingError,
    KnowledgeLifecycleConflictError,
    KnowledgeRetrievalError,
)
from app.knowledge.storage import (
    KnowledgeFileStorageError,
    KnowledgeStorageConfigurationError,
)
from app.knowledge.vector_store import VectorStoreConfigurationError
from app.scenarios.access_management.workflow import AccessWorkflowResumeError
from app.scenarios.access_management.commands import AccessRequestActorMismatchError
from app.tickets.repository import TicketActorMismatchError, TicketNotFoundError
from app.tickets.service import TicketProjectionConflictError
from app.workflow.repository import (
    WorkflowNotFoundError,
    WorkflowVersionConflictError,
)
from app.conversations.repository import (
    ConversationActorMismatchError,
    ConversationMessageConflictError,
    ConversationNotFoundError,
    ConversationVersionConflictError,
)
from app.evaluation.catalog import EvaluationSuiteNotFoundError
from app.evaluation.repository import EvaluationRunNotFoundError
from app.evaluation.service import EvaluationCommandConflictError
from app.evaluation.governance import (
    EvaluationGovernanceConflictError,
    EvaluationGovernanceNotFoundError,
    EvaluationGovernanceValidationError,
)
from app.evaluation.comparison import EvaluationComparisonIncompatibleError


class ErrorDetail(BaseModel):
    code: str
    message: str


class ErrorResponse(BaseModel):
    error: ErrorDetail


def _response(status_code: int, code: str, message: str) -> JSONResponse:
    body = ErrorResponse(error=ErrorDetail(code=code, message=message))
    return JSONResponse(status_code=status_code, content=body.model_dump(mode="json"))


def register_exception_handlers(application: FastAPI) -> None:
    @application.exception_handler(HTTPException)
    async def handle_http_exception(
        request: Request,
        exc: HTTPException,
    ) -> JSONResponse:
        return _response(exc.status_code, "HTTP_ERROR", str(exc.detail))

    @application.exception_handler(RequestValidationError)
    async def handle_validation_error(
        request: Request,
        exc: RequestValidationError,
    ) -> JSONResponse:
        return _response(422, "VALIDATION_ERROR", "request validation failed")

    @application.exception_handler(InvalidCredentialsError)
    @application.exception_handler(InvalidAccessTokenError)
    async def handle_authentication_failure(
        request: Request,
        exc: Exception,
    ) -> JSONResponse:
        return _response(401, "AUTHENTICATION_FAILED", "authentication failed")

    @application.exception_handler(UserAlreadyExistsError)
    async def handle_user_conflict(request: Request, exc: Exception) -> JSONResponse:
        return _response(409, "USER_CONFLICT", "user identity already exists")

    @application.exception_handler(CurrentPasswordInvalidError)
    async def handle_current_password_invalid(
        request: Request,
        exc: Exception,
    ) -> JSONResponse:
        return _response(
            400,
            "CURRENT_PASSWORD_INVALID",
            "current password is invalid",
        )

    @application.exception_handler(PasswordReuseError)
    async def handle_password_reuse(
        request: Request,
        exc: Exception,
    ) -> JSONResponse:
        return _response(
            409,
            "PASSWORD_REUSE",
            "new password must be different",
        )

    @application.exception_handler(AvatarNotFoundError)
    async def handle_avatar_not_found(
        request: Request,
        exc: Exception,
    ) -> JSONResponse:
        return _response(404, "AVATAR_NOT_FOUND", "custom avatar was not found")

    @application.exception_handler(AvatarFileTooLargeError)
    async def handle_avatar_too_large(
        request: Request,
        exc: Exception,
    ) -> JSONResponse:
        return _response(
            413,
            "AVATAR_FILE_TOO_LARGE",
            "avatar image exceeds the configured limit",
        )

    @application.exception_handler(UnsupportedAvatarFileError)
    async def handle_unsupported_avatar(
        request: Request,
        exc: Exception,
    ) -> JSONResponse:
        return _response(
            415,
            "AVATAR_FILE_UNSUPPORTED",
            "avatar image type is not supported",
        )

    @application.exception_handler(AvatarStorageConfigurationError)
    async def handle_avatar_storage_configuration(
        request: Request,
        exc: Exception,
    ) -> JSONResponse:
        return _response(
            503,
            "AVATAR_STORAGE_UNAVAILABLE",
            "avatar storage is not configured",
        )

    @application.exception_handler(AvatarStorageOperationError)
    async def handle_avatar_storage_operation(
        request: Request,
        exc: Exception,
    ) -> JSONResponse:
        return _response(
            502,
            "AVATAR_STORAGE_OPERATION_FAILED",
            "avatar storage operation failed",
        )

    @application.exception_handler(WorkflowNotFoundError)
    @application.exception_handler(ApprovalNotFoundError)
    @application.exception_handler(TicketNotFoundError)
    @application.exception_handler(KnowledgeDocumentNotFoundError)
    @application.exception_handler(KnowledgeIndexJobNotFoundError)
    @application.exception_handler(ConversationNotFoundError)
    @application.exception_handler(EvaluationRunNotFoundError)
    @application.exception_handler(EvaluationSuiteNotFoundError)
    @application.exception_handler(EvaluationGovernanceNotFoundError)
    async def handle_not_found(request: Request, exc: Exception) -> JSONResponse:
        return _response(404, "RESOURCE_NOT_FOUND", "requested resource was not found")

    @application.exception_handler(WorkflowVersionConflictError)
    @application.exception_handler(ApprovalConflictError)
    @application.exception_handler(AccessWorkflowResumeError)
    @application.exception_handler(ApprovalCheckpointError)
    @application.exception_handler(ApprovalSequenceCheckpointError)
    @application.exception_handler(ApprovalSequenceValidationError)
    @application.exception_handler(TicketProjectionConflictError)
    @application.exception_handler(ConversationMessageConflictError)
    @application.exception_handler(ConversationVersionConflictError)
    @application.exception_handler(EvaluationCommandConflictError)
    @application.exception_handler(EvaluationGovernanceConflictError)
    @application.exception_handler(EvaluationComparisonIncompatibleError)
    async def handle_conflict(request: Request, exc: Exception) -> JSONResponse:
        return _response(409, "STATE_CONFLICT", "resource state has changed")

    @application.exception_handler(EvaluationGovernanceValidationError)
    async def handle_evaluation_governance_validation(
        request: Request,
        exc: Exception,
    ) -> JSONResponse:
        return _response(422, "VALIDATION_ERROR", "evaluation command is invalid")

    @application.exception_handler(KnowledgeDocumentConflictError)
    async def handle_knowledge_version_conflict(
        request: Request,
        exc: Exception,
    ) -> JSONResponse:
        return _response(
            409,
            "KNOWLEDGE_VERSION_CONFLICT",
            "knowledge source version already exists",
        )

    @application.exception_handler(KnowledgeLifecycleConflictError)
    async def handle_knowledge_lifecycle_conflict(
        request: Request,
        exc: Exception,
    ) -> JSONResponse:
        return _response(
            409,
            "KNOWLEDGE_LIFECYCLE_CONFLICT",
            "knowledge document state does not allow this action",
        )

    @application.exception_handler(KnowledgeIndexRetryNotAllowedError)
    async def handle_knowledge_retry_conflict(
        request: Request,
        exc: Exception,
    ) -> JSONResponse:
        return _response(
            409,
            "KNOWLEDGE_INDEX_RETRY_NOT_ALLOWED",
            "knowledge document cannot be reindexed in its current state",
        )

    @application.exception_handler(UnsupportedApprovalScenarioError)
    async def handle_unsupported_approval(
        request: Request,
        exc: Exception,
    ) -> JSONResponse:
        return _response(409, "STATE_CONFLICT", "approval scenario is unavailable")

    @application.exception_handler(ApprovalActorMismatchError)
    @application.exception_handler(AccessRequestActorMismatchError)
    @application.exception_handler(InsufficientRoleError)
    @application.exception_handler(TicketActorMismatchError)
    @application.exception_handler(ConversationActorMismatchError)
    async def handle_forbidden(request: Request, exc: Exception) -> JSONResponse:
        return _response(403, "ACTION_FORBIDDEN", "actor cannot perform this action")

    @application.exception_handler(EnterpriseOpsClientError)
    async def handle_enterprise_failure(
        request: Request,
        exc: Exception,
    ) -> JSONResponse:
        return _response(
            502,
            "ENTERPRISE_SERVICE_UNAVAILABLE",
            "enterprise service request failed",
        )

    @application.exception_handler(KnowledgeFileTooLargeError)
    async def handle_knowledge_file_too_large(
        request: Request,
        exc: Exception,
    ) -> JSONResponse:
        return _response(
            413,
            "KNOWLEDGE_FILE_TOO_LARGE",
            "knowledge file exceeds the configured limit",
        )

    @application.exception_handler(UnsupportedKnowledgeFileError)
    async def handle_unsupported_knowledge_file(
        request: Request,
        exc: Exception,
    ) -> JSONResponse:
        return _response(
            415,
            "KNOWLEDGE_FILE_UNSUPPORTED",
            "knowledge file type is not supported",
        )

    @application.exception_handler(KnowledgeFileDependencyError)
    @application.exception_handler(KnowledgeStorageConfigurationError)
    async def handle_knowledge_upload_configuration(
        request: Request,
        exc: Exception,
    ) -> JSONResponse:
        return _response(
            503,
            "KNOWLEDGE_UPLOAD_UNAVAILABLE",
            "knowledge upload service is not configured",
        )

    @application.exception_handler(KnowledgeFileError)
    async def handle_invalid_knowledge_file(
        request: Request,
        exc: Exception,
    ) -> JSONResponse:
        return _response(
            422,
            "KNOWLEDGE_FILE_INVALID",
            "knowledge file is invalid",
        )

    @application.exception_handler(KnowledgeFileStorageError)
    async def handle_knowledge_file_storage_failure(
        request: Request,
        exc: Exception,
    ) -> JSONResponse:
        return _response(
            500,
            "KNOWLEDGE_FILE_STORAGE_FAILED",
            "knowledge file could not be stored safely",
        )

    @application.exception_handler(EmbeddingConfigurationError)
    @application.exception_handler(VectorStoreConfigurationError)
    async def handle_knowledge_configuration(
        request: Request,
        exc: Exception,
    ) -> JSONResponse:
        return _response(
            503,
            "KNOWLEDGE_SERVICE_UNAVAILABLE",
            "knowledge retrieval service is not configured",
        )

    @application.exception_handler(AgentModelConfigurationError)
    async def handle_agent_configuration(
        request: Request,
        exc: Exception,
    ) -> JSONResponse:
        return _response(
            503,
            "AGENT_SERVICE_UNAVAILABLE",
            "agent model service is not configured",
        )

    @application.exception_handler(KnowledgeIndexingError)
    async def handle_knowledge_indexing(
        request: Request,
        exc: Exception,
    ) -> JSONResponse:
        return _response(
            502,
            "KNOWLEDGE_INDEXING_FAILED",
            "knowledge document indexing failed",
        )

    @application.exception_handler(KnowledgeRetrievalError)
    async def handle_knowledge_retrieval(
        request: Request,
        exc: Exception,
    ) -> JSONResponse:
        return _response(
            502,
            "KNOWLEDGE_RETRIEVAL_FAILED",
            "knowledge retrieval failed",
        )

    @application.exception_handler(AgentModelResponseError)
    @application.exception_handler(AgentModelExecutionError)
    @application.exception_handler(MultiAgentOrchestrationError)
    async def handle_agent_model_response(
        request: Request,
        exc: Exception,
    ) -> JSONResponse:
        return _response(
            502,
            "AGENT_MODEL_RESPONSE_INVALID",
            "agent model returned an invalid structured response",
        )
