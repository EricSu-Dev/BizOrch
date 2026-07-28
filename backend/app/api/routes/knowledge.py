"""Authenticated enterprise knowledge ingestion and retrieval endpoints."""

from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, File, Form, Query, UploadFile, status
from pydantic import BaseModel, ConfigDict, Field

from app.api.dependencies import (
    CurrentActor,
    get_current_actor,
    get_knowledge_service,
    require_admin,
    require_knowledge_manager,
)
from app.auth.contracts import RoleName
from app.knowledge.contracts import (
    KnowledgeDocumentDetail,
    KnowledgeDocumentEventPage,
    KnowledgeDocumentEventType,
    KnowledgeDocumentPage,
    KnowledgeDocumentView,
    KnowledgeIndexJobPage,
    KnowledgeIndexJobStatus,
    KnowledgeIndexJobView,
    KnowledgeIndexStatus,
    KnowledgeLifecycleResult,
    KnowledgePublicationStatus,
    KnowledgeRetrievalAuditPage,
    KnowledgeSearchResult,
    KnowledgeTrustLevel,
    KnowledgeUploadResult,
)
from app.knowledge.service import KnowledgeService

router = APIRouter(prefix="/knowledge", tags=["knowledge"])


class IngestKnowledgeDocumentBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    knowledge_space: str = Field(min_length=1, max_length=100)
    title: str = Field(min_length=1, max_length=255)
    content: str = Field(min_length=1, max_length=500_000)
    source_uri: str = Field(min_length=1, max_length=500)
    version_label: str = Field(min_length=1, max_length=100)
    source_department: str = Field(min_length=1, max_length=100)
    trust_level: KnowledgeTrustLevel = KnowledgeTrustLevel.HIGH
    effective_from: datetime | None = None
    effective_until: datetime | None = None
    allowed_roles: frozenset[RoleName] = frozenset()
    allowed_user_ids: frozenset[str] = frozenset()


class SearchKnowledgeBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    query: str = Field(min_length=1, max_length=2000)
    knowledge_space: str = Field(min_length=1, max_length=100)
    top_k: int = Field(default=5, ge=1, le=20)


@router.post(
    "/documents",
    response_model=KnowledgeDocumentView,
    status_code=status.HTTP_201_CREATED,
)
def ingest_knowledge_document(
    body: IngestKnowledgeDocumentBody,
    actor: Annotated[CurrentActor, Depends(require_knowledge_manager)],
    service: Annotated[KnowledgeService, Depends(get_knowledge_service)],
) -> KnowledgeDocumentView:
    return service.ingest_text(
        knowledge_space=body.knowledge_space,
        title=body.title,
        content=body.content,
        source_uri=body.source_uri,
        version_label=body.version_label,
        source_department=body.source_department,
        trust_level=body.trust_level,
        effective_from=body.effective_from,
        effective_until=body.effective_until,
        allowed_roles=frozenset(role.value for role in body.allowed_roles),
        allowed_user_ids=body.allowed_user_ids,
        actor_id=actor.user_id,
    )


@router.post(
    "/documents/upload",
    response_model=KnowledgeUploadResult,
    status_code=status.HTTP_202_ACCEPTED,
)
async def upload_knowledge_document(
    file: Annotated[UploadFile, File()],
    knowledge_space: Annotated[str, Form(min_length=1, max_length=100)],
    title: Annotated[str, Form(min_length=1, max_length=255)],
    source_uri: Annotated[str, Form(min_length=1, max_length=500)],
    version_label: Annotated[str, Form(min_length=1, max_length=100)],
    source_department: Annotated[str, Form(min_length=1, max_length=100)],
    actor: Annotated[CurrentActor, Depends(require_knowledge_manager)],
    service: Annotated[KnowledgeService, Depends(get_knowledge_service)],
    trust_level: Annotated[KnowledgeTrustLevel, Form()] = KnowledgeTrustLevel.HIGH,
    effective_from: Annotated[datetime | None, Form()] = None,
    effective_until: Annotated[datetime | None, Form()] = None,
    allowed_roles: Annotated[list[RoleName] | None, Form()] = None,
    allowed_user_ids: Annotated[list[str] | None, Form()] = None,
) -> KnowledgeUploadResult:
    """Validate and persist one original file without indexing it inline."""
    try:
        content = await file.read(service.max_upload_bytes + 1)
    finally:
        await file.close()
    return service.create_upload_draft(
        file_name=file.filename or "",
        content=content,
        knowledge_space=knowledge_space,
        title=title,
        source_uri=source_uri,
        version_label=version_label,
        source_department=source_department,
        trust_level=trust_level,
        effective_from=effective_from,
        effective_until=effective_until,
        allowed_roles=frozenset(role.value for role in allowed_roles or []),
        allowed_user_ids=frozenset(allowed_user_ids or []),
        actor_id=actor.user_id,
        actor_roles=actor.roles,
    )


@router.get("/documents", response_model=KnowledgeDocumentPage)
def list_knowledge_documents(
    actor: Annotated[CurrentActor, Depends(require_knowledge_manager)],
    service: Annotated[KnowledgeService, Depends(get_knowledge_service)],
    knowledge_space: Annotated[str | None, Query(max_length=100)] = None,
    publication_status: Annotated[
        KnowledgePublicationStatus | None,
        Query(),
    ] = None,
    index_status: Annotated[KnowledgeIndexStatus | None, Query()] = None,
    source_department: Annotated[str | None, Query(max_length=100)] = None,
    keyword: Annotated[str | None, Query(max_length=255)] = None,
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 20,
) -> KnowledgeDocumentPage:
    return service.list_documents(
        knowledge_space=knowledge_space,
        publication_status=publication_status,
        index_status=index_status,
        source_department=source_department,
        keyword=keyword,
        page=page,
        page_size=page_size,
    )


@router.get("/documents/{document_id}", response_model=KnowledgeDocumentDetail)
def get_knowledge_document(
    document_id: str,
    actor: Annotated[CurrentActor, Depends(require_knowledge_manager)],
    service: Annotated[KnowledgeService, Depends(get_knowledge_service)],
) -> KnowledgeDocumentDetail:
    return service.get_document_detail(document_id)


@router.get(
    "/documents/{document_id}/events",
    response_model=KnowledgeDocumentEventPage,
)
def list_knowledge_document_events(
    document_id: str,
    actor: Annotated[CurrentActor, Depends(require_knowledge_manager)],
    service: Annotated[KnowledgeService, Depends(get_knowledge_service)],
    event_type: Annotated[KnowledgeDocumentEventType | None, Query()] = None,
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 20,
) -> KnowledgeDocumentEventPage:
    return service.list_document_events(
        document_id,
        event_type=event_type,
        page=page,
        page_size=page_size,
    )


@router.post(
    "/documents/{document_id}/index",
    response_model=KnowledgeIndexJobView,
    status_code=status.HTTP_202_ACCEPTED,
)
def retry_knowledge_document_index(
    document_id: str,
    actor: Annotated[CurrentActor, Depends(require_knowledge_manager)],
    service: Annotated[KnowledgeService, Depends(get_knowledge_service)],
) -> KnowledgeIndexJobView:
    return service.request_index_retry(
        document_id,
        actor_id=actor.user_id,
        actor_roles=actor.roles,
    )


@router.post(
    "/documents/{document_id}/publish",
    response_model=KnowledgeLifecycleResult,
)
def publish_knowledge_document(
    document_id: str,
    actor: Annotated[CurrentActor, Depends(require_knowledge_manager)],
    service: Annotated[KnowledgeService, Depends(get_knowledge_service)],
) -> KnowledgeLifecycleResult:
    return service.publish_document(
        document_id,
        actor_id=actor.user_id,
        actor_roles=actor.roles,
    )


@router.post(
    "/documents/{document_id}/retire",
    response_model=KnowledgeLifecycleResult,
)
def retire_knowledge_document(
    document_id: str,
    actor: Annotated[CurrentActor, Depends(require_knowledge_manager)],
    service: Annotated[KnowledgeService, Depends(get_knowledge_service)],
) -> KnowledgeLifecycleResult:
    return service.retire_document(
        document_id,
        actor_id=actor.user_id,
        actor_roles=actor.roles,
    )


@router.post(
    "/documents/{document_id}/vector-cleanup",
    response_model=KnowledgeLifecycleResult,
)
def retry_knowledge_vector_cleanup(
    document_id: str,
    actor: Annotated[CurrentActor, Depends(require_knowledge_manager)],
    service: Annotated[KnowledgeService, Depends(get_knowledge_service)],
) -> KnowledgeLifecycleResult:
    return service.retry_vector_cleanup(
        document_id,
        actor_id=actor.user_id,
        actor_roles=actor.roles,
    )


@router.get("/index-jobs", response_model=KnowledgeIndexJobPage)
def list_knowledge_index_jobs(
    actor: Annotated[CurrentActor, Depends(require_knowledge_manager)],
    service: Annotated[KnowledgeService, Depends(get_knowledge_service)],
    document_id: Annotated[str | None, Query(max_length=36)] = None,
    job_status: Annotated[
        KnowledgeIndexJobStatus | None,
        Query(alias="status"),
    ] = None,
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 20,
) -> KnowledgeIndexJobPage:
    return service.list_index_jobs(
        document_id=document_id,
        status=job_status,
        page=page,
        page_size=page_size,
    )


@router.get("/index-jobs/{job_id}", response_model=KnowledgeIndexJobView)
def get_knowledge_index_job(
    job_id: str,
    actor: Annotated[CurrentActor, Depends(require_knowledge_manager)],
    service: Annotated[KnowledgeService, Depends(get_knowledge_service)],
) -> KnowledgeIndexJobView:
    return service.get_index_job(job_id)


@router.get("/retrieval-logs", response_model=KnowledgeRetrievalAuditPage)
def list_knowledge_retrieval_logs(
    actor: Annotated[CurrentActor, Depends(require_admin)],
    service: Annotated[KnowledgeService, Depends(get_knowledge_service)],
    knowledge_space: Annotated[str | None, Query(max_length=100)] = None,
    actor_id: Annotated[str | None, Query(max_length=100)] = None,
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 20,
) -> KnowledgeRetrievalAuditPage:
    return service.list_retrieval_audits(
        knowledge_space=knowledge_space,
        actor_id=actor_id,
        page=page,
        page_size=page_size,
    )


@router.post("/search", response_model=KnowledgeSearchResult)
def search_knowledge(
    body: SearchKnowledgeBody,
    actor: Annotated[CurrentActor, Depends(get_current_actor)],
    service: Annotated[KnowledgeService, Depends(get_knowledge_service)],
) -> KnowledgeSearchResult:
    return service.search(
        query=body.query,
        knowledge_space=body.knowledge_space,
        actor_id=actor.user_id,
        actor_roles=actor.roles,
        top_k=body.top_k,
    )
