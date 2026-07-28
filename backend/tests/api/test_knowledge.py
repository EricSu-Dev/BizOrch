import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.api.dependencies import CurrentActor, get_current_actor
from app.core.config import Settings
from app.knowledge.embeddings import DashScopeEmbeddings
from app.knowledge.files import KnowledgeFileParser
from app.knowledge.index_worker import KnowledgeIndexWorker
from app.knowledge.service import KnowledgeService
from app.knowledge.storage import KnowledgeFileStorage
from app.main import create_app
from app.persistence.base import Base
from tests.knowledge.fakes import FakeEmbeddings, InMemoryVectorStore


class KnowledgeRuntimeHarness:
    def __init__(
        self,
        knowledge: KnowledgeService,
        upload_root,
        sessions: sessionmaker[Session],
        vectors: InMemoryVectorStore,
    ) -> None:
        self.knowledge = knowledge
        self.upload_root = upload_root
        self.sessions = sessions
        self.vectors = vectors
        self.closed = False

    def close(self) -> None:
        self.closed = True


def build_app(tmp_path, *, embeddings=None, file_parser=None):
    engine = create_engine(f"sqlite+pysqlite:///{tmp_path / 'knowledge-api.db'}")
    Base.metadata.create_all(engine)
    sessions: sessionmaker[Session] = sessionmaker(
        bind=engine,
        expire_on_commit=False,
    )
    upload_root = tmp_path / "uploads"
    vectors = InMemoryVectorStore()
    knowledge = KnowledgeService(
        sessions,
        embeddings or FakeEmbeddings(),
        vectors,
        file_parser=file_parser,
        file_storage=KnowledgeFileStorage(upload_root),
    )
    runtime = KnowledgeRuntimeHarness(
        knowledge,
        upload_root,
        sessions,
        vectors,
    )
    application = create_app(
        settings=Settings(database_url="configured-for-test"),
        runtime_factory=lambda settings: runtime,
    )
    return application, runtime


def document_body():
    return {
        "knowledge_space": "access_and_security",
        "title": "VPN access policy",
        "content": (
            "VPN remote access requires manager approval and expires in 30 days."
        ),
        "source_uri": "policy://vpn-access",
        "version_label": "2026.1",
        "source_department": "Information Security",
        "trust_level": "AUTHORITATIVE",
        "allowed_roles": ["employee", "approver"],
    }


def upload_form(
    *,
    source_uri: str = "policy://uploaded-vpn",
    version_label: str = "2026.1",
    title: str = "Uploaded VPN policy",
):
    return {
        "knowledge_space": "access_and_security",
        "title": title,
        "source_uri": source_uri,
        "version_label": version_label,
        "source_department": "Information Security",
        "trust_level": "AUTHORITATIVE",
    }


def manager_actor(role: str = "operator") -> CurrentActor:
    return CurrentActor(
        user_id=f"EMP-{role.upper()}",
        roles=frozenset({role}),
    )


def upload_document(
    client: TestClient,
    *,
    source_uri: str,
    version_label: str = "2026.1",
    title: str = "Uploaded VPN policy",
    content: bytes = b"# VPN\nManager approval required.",
):
    return client.post(
        "/api/v1/knowledge/documents/upload",
        data=upload_form(
            source_uri=source_uri,
            version_label=version_label,
            title=title,
        ),
        files={"file": ("vpn-policy.md", content)},
    )


def run_index_worker(runtime: KnowledgeRuntimeHarness, *, embeddings=None):
    return KnowledgeIndexWorker(
        runtime.sessions,
        embeddings or FakeEmbeddings(),
        runtime.vectors,
        worker_id="test-index-worker",
    ).run_once()


def test_operator_uploads_draft_and_pending_job_without_embedding_call(tmp_path) -> None:
    class EmbeddingsMustNotRun(FakeEmbeddings):
        def embed_documents(self, texts: list[str]) -> list[list[float]]:
            raise AssertionError("upload must not call embedding provider")

    application, runtime = build_app(tmp_path, embeddings=EmbeddingsMustNotRun())
    application.dependency_overrides[get_current_actor] = lambda: CurrentActor(
        user_id="EMP-OPERATOR",
        roles=frozenset({"operator"}),
    )

    with TestClient(application) as client:
        response = client.post(
            "/api/v1/knowledge/documents/upload",
            data=upload_form(),
            files={"file": ("vpn-policy.md", b"# VPN\nManager approval required.")},
        )

    assert response.status_code == 202
    body = response.json()
    assert body["document"]["publication_status"] == "DRAFT"
    assert body["document"]["index_status"] == "PENDING"
    assert body["index_job"]["status"] == "PENDING"
    assert "storage_key" not in body["document"]
    assert len(list(runtime.upload_root.rglob("*.md"))) == 1


def test_employee_cannot_persist_uploaded_knowledge(tmp_path) -> None:
    application, runtime = build_app(tmp_path)
    application.dependency_overrides[get_current_actor] = lambda: CurrentActor(
        user_id="EMP-1001",
        roles=frozenset({"employee"}),
    )

    with TestClient(application) as client:
        response = client.post(
            "/api/v1/knowledge/documents/upload",
            data=upload_form(),
            files={"file": ("vpn-policy.md", b"# VPN policy")},
        )

    assert response.status_code == 403
    assert response.json()["error"]["code"] == "ACTION_FORBIDDEN"
    assert not runtime.upload_root.exists()


def test_upload_rejects_unsupported_invalid_and_oversized_files(tmp_path) -> None:
    application, _ = build_app(
        tmp_path,
        file_parser=KnowledgeFileParser(max_bytes=8),
    )
    application.dependency_overrides[get_current_actor] = lambda: CurrentActor(
        user_id="EMP-OPERATOR",
        roles=frozenset({"operator"}),
    )

    with TestClient(application) as client:
        unsupported = client.post(
            "/api/v1/knowledge/documents/upload",
            data=upload_form(source_uri="policy://docx"),
            files={"file": ("policy.docx", b"content")},
        )
        invalid = client.post(
            "/api/v1/knowledge/documents/upload",
            data=upload_form(source_uri="policy://fake-pdf"),
            files={"file": ("policy.pdf", b"not pdf")},
        )
        oversized = client.post(
            "/api/v1/knowledge/documents/upload",
            data=upload_form(source_uri="policy://large"),
            files={"file": ("policy.txt", b"123456789")},
        )

    assert unsupported.status_code == 415
    assert unsupported.json()["error"]["code"] == "KNOWLEDGE_FILE_UNSUPPORTED"
    assert invalid.status_code == 422
    assert invalid.json()["error"]["code"] == "KNOWLEDGE_FILE_INVALID"
    assert oversized.status_code == 413
    assert oversized.json()["error"]["code"] == "KNOWLEDGE_FILE_TOO_LARGE"


def test_conflicting_upload_keeps_one_persistent_original(tmp_path) -> None:
    application, runtime = build_app(tmp_path)
    application.dependency_overrides[get_current_actor] = lambda: CurrentActor(
        user_id="EMP-OPERATOR",
        roles=frozenset({"operator"}),
    )

    with TestClient(application) as client:
        first = client.post(
            "/api/v1/knowledge/documents/upload",
            data=upload_form(),
            files={"file": ("vpn-policy.md", b"# VPN policy")},
        )
        conflict = client.post(
            "/api/v1/knowledge/documents/upload",
            data=upload_form(),
            files={"file": ("vpn-policy.md", b"# Changed VPN policy")},
        )

    assert first.status_code == 202
    assert conflict.status_code == 409
    assert len(list(runtime.upload_root.rglob("*.md"))) == 1


def test_operator_ingests_and_employee_searches_with_citation(tmp_path) -> None:
    application, runtime = build_app(tmp_path)
    application.dependency_overrides[get_current_actor] = lambda: CurrentActor(
        user_id="EMP-ADMIN",
        roles=frozenset({"operator"}),
    )

    with TestClient(application) as client:
        ingested = client.post("/api/v1/knowledge/documents", json=document_body())
        assert ingested.status_code == 201
        assert ingested.json()["index_status"] == "INDEXED"
        document_id = ingested.json()["document_id"]

        application.dependency_overrides[get_current_actor] = lambda: CurrentActor(
            user_id="EMP-1001",
            roles=frozenset({"employee"}),
        )
        search = client.post(
            "/api/v1/knowledge/search",
            json={
                "query": "Who approves VPN remote access?",
                "knowledge_space": "access_and_security",
                "top_k": 3,
            },
        )
        assert search.status_code == 200
        assert search.json()["citations"][0]["document_id"] == document_id
        assert search.json()["citations"][0]["source_uri"] == "policy://vpn-access"

    assert runtime.closed


@pytest.mark.parametrize("role", ["employee", "approver"])
def test_non_manager_roles_cannot_ingest_knowledge(tmp_path, role: str) -> None:
    application, _ = build_app(tmp_path)
    application.dependency_overrides[get_current_actor] = lambda: CurrentActor(
        user_id="EMP-1001",
        roles=frozenset({role}),
    )

    with TestClient(application) as client:
        response = client.post("/api/v1/knowledge/documents", json=document_body())
        assert response.status_code == 403
        assert response.json()["error"]["code"] == "ACTION_FORBIDDEN"


def test_admin_can_ingest_knowledge(tmp_path) -> None:
    application, _ = build_app(tmp_path)
    application.dependency_overrides[get_current_actor] = lambda: CurrentActor(
        user_id="EMP-ADMIN",
        roles=frozenset({"admin"}),
    )

    with TestClient(application) as client:
        response = client.post("/api/v1/knowledge/documents", json=document_body())

    assert response.status_code == 201
    assert response.json()["publication_status"] == "PUBLISHED"


def test_missing_embedding_key_returns_stable_service_unavailable(tmp_path) -> None:
    application, _ = build_app(tmp_path, embeddings=DashScopeEmbeddings(None))
    application.dependency_overrides[get_current_actor] = lambda: CurrentActor(
        user_id="EMP-1001",
        roles=frozenset({"employee"}),
    )

    with TestClient(application) as client:
        response = client.post(
            "/api/v1/knowledge/search",
            json={"query": "VPN policy", "knowledge_space": "access_and_security"},
        )
        assert response.status_code == 503
        assert response.json()["error"]["code"] == "KNOWLEDGE_SERVICE_UNAVAILABLE"


def test_manager_lists_filters_and_paginates_documents(tmp_path) -> None:
    application, _ = build_app(tmp_path)
    application.dependency_overrides[get_current_actor] = lambda: manager_actor()

    with TestClient(application) as client:
        for index in range(3):
            response = upload_document(
                client,
                source_uri=f"policy://managed-{index}",
                title=f"Managed policy {index}",
            )
            assert response.status_code == 202

        first_page = client.get(
            "/api/v1/knowledge/documents",
            params={"page": 1, "page_size": 2},
        )
        filtered = client.get(
            "/api/v1/knowledge/documents",
            params={
                "publication_status": "DRAFT",
                "index_status": "PENDING",
                "source_department": "Information Security",
                "keyword": "Managed policy 1",
            },
        )
        invalid_page = client.get(
            "/api/v1/knowledge/documents",
            params={"page": 0},
        )

    assert first_page.status_code == 200
    assert first_page.json()["total"] == 3
    assert first_page.json()["total_pages"] == 2
    assert len(first_page.json()["items"]) == 2
    assert filtered.status_code == 200
    assert filtered.json()["total"] == 1
    assert filtered.json()["items"][0]["title"] == "Managed policy 1"
    assert invalid_page.status_code == 422
    assert invalid_page.json()["error"]["code"] == "VALIDATION_ERROR"


def test_document_detail_jobs_and_events_are_safe_and_paginated(tmp_path) -> None:
    application, _ = build_app(tmp_path)
    application.dependency_overrides[get_current_actor] = lambda: manager_actor()
    source = b"# Policy\n" + (b"A" * 650) + b"PRIVATE_TAIL_MUST_NOT_LEAK"

    with TestClient(application) as client:
        uploaded = upload_document(
            client,
            source_uri="policy://safe-detail",
            content=source,
        )
        assert uploaded.status_code == 202
        document_id = uploaded.json()["document"]["document_id"]
        job_id = uploaded.json()["index_job"]["job_id"]

        detail = client.get(f"/api/v1/knowledge/documents/{document_id}")
        jobs = client.get(
            "/api/v1/knowledge/index-jobs",
            params={"document_id": document_id, "status": "PENDING"},
        )
        job = client.get(f"/api/v1/knowledge/index-jobs/{job_id}")
        events = client.get(
            f"/api/v1/knowledge/documents/{document_id}/events",
            params={"page": 1, "page_size": 1},
        )

    assert detail.status_code == 200
    serialized_detail = detail.text
    assert "storage_key" not in serialized_detail
    assert "PRIVATE_TAIL_MUST_NOT_LEAK" not in serialized_detail
    assert all(len(item["excerpt"]) <= 300 for item in detail.json()["chunks"])
    assert detail.json()["latest_index_job"]["job_id"] == job_id
    assert jobs.status_code == 200
    assert jobs.json()["total"] == 1
    assert jobs.json()["items"][0]["job_id"] == job_id
    assert job.status_code == 200
    assert job.json()["status"] == "PENDING"
    assert events.status_code == 200
    assert events.json()["total"] == 2
    assert events.json()["total_pages"] == 2
    assert len(events.json()["items"]) == 1


def test_management_lifecycle_routes_enforce_state_machine(tmp_path) -> None:
    application, runtime = build_app(tmp_path)
    application.dependency_overrides[get_current_actor] = lambda: manager_actor()

    with TestClient(application) as client:
        uploaded = upload_document(
            client,
            source_uri="policy://lifecycle-api",
        )
        assert uploaded.status_code == 202
        document_id = uploaded.json()["document"]["document_id"]

        premature = client.post(
            f"/api/v1/knowledge/documents/{document_id}/publish"
        )
        assert premature.status_code == 409
        assert (
            premature.json()["error"]["code"]
            == "KNOWLEDGE_LIFECYCLE_CONFLICT"
        )

        indexed = run_index_worker(runtime)
        assert indexed is not None
        assert indexed.outcome.value == "SUCCEEDED"

        published = client.post(
            f"/api/v1/knowledge/documents/{document_id}/publish"
        )
        retired = client.post(
            f"/api/v1/knowledge/documents/{document_id}/retire"
        )
        retired_again = client.post(
            f"/api/v1/knowledge/documents/{document_id}/retire"
        )
        retry_retired = client.post(
            f"/api/v1/knowledge/documents/{document_id}/index"
        )

    assert published.status_code == 200
    assert published.json()["document"]["publication_status"] == "PUBLISHED"
    assert retired.status_code == 200
    assert retired.json()["document"]["publication_status"] == "RETIRED"
    assert retired_again.status_code == 409
    assert retry_retired.status_code == 409
    assert (
        retry_retired.json()["error"]["code"]
        == "KNOWLEDGE_INDEX_RETRY_NOT_ALLOWED"
    )


def test_failed_index_retry_route_is_idempotent(tmp_path) -> None:
    application, runtime = build_app(tmp_path)
    application.dependency_overrides[get_current_actor] = lambda: manager_actor()

    with TestClient(application) as client:
        uploaded = upload_document(
            client,
            source_uri="policy://retry-api",
        )
        assert uploaded.status_code == 202
        document_id = uploaded.json()["document"]["document_id"]
        initial_job_id = uploaded.json()["index_job"]["job_id"]

        failed = run_index_worker(
            runtime,
            embeddings=DashScopeEmbeddings(None),
        )
        assert failed is not None
        assert failed.outcome.value == "FAILED"

        first_retry = client.post(
            f"/api/v1/knowledge/documents/{document_id}/index"
        )
        second_retry = client.post(
            f"/api/v1/knowledge/documents/{document_id}/index"
        )

    assert first_retry.status_code == 202
    assert second_retry.status_code == 202
    assert first_retry.json()["job_id"] == second_retry.json()["job_id"]
    assert first_retry.json()["job_id"] != initial_job_id
    assert first_retry.json()["status"] == "PENDING"


def test_retrieval_audit_is_admin_only_and_omits_raw_query(tmp_path) -> None:
    application, _ = build_app(tmp_path)
    application.dependency_overrides[get_current_actor] = lambda: manager_actor()
    secret_query = "confidential original query phrase"

    with TestClient(application) as client:
        ingested = client.post("/api/v1/knowledge/documents", json=document_body())
        assert ingested.status_code == 201

        application.dependency_overrides[get_current_actor] = lambda: CurrentActor(
            user_id="EMP-1001",
            roles=frozenset({"employee"}),
        )
        searched = client.post(
            "/api/v1/knowledge/search",
            json={
                "query": secret_query,
                "knowledge_space": "access_and_security",
                "top_k": 3,
            },
        )
        assert searched.status_code == 200

        application.dependency_overrides[get_current_actor] = lambda: manager_actor()
        forbidden = client.get("/api/v1/knowledge/retrieval-logs")

        application.dependency_overrides[get_current_actor] = lambda: manager_actor(
            "admin"
        )
        audits = client.get(
            "/api/v1/knowledge/retrieval-logs",
            params={
                "knowledge_space": "access_and_security",
                "actor_id": "EMP-1001",
            },
        )

    assert forbidden.status_code == 403
    assert audits.status_code == 200
    assert audits.json()["total"] == 1
    item = audits.json()["items"][0]
    assert item["actor_id"] == "EMP-1001"
    assert len(item["query_digest"]) == 64
    assert item["result_count"] == len(item["returned_chunk_ids"])
    assert "query_text" not in item
    assert secret_query not in audits.text


def test_search_uses_authenticated_actor_and_does_not_leak_filtered_documents(
    tmp_path,
) -> None:
    application, _ = build_app(tmp_path)
    application.dependency_overrides[get_current_actor] = lambda: manager_actor()
    employee_document = document_body()
    employee_document.update(
        {
            "title": "Employee VPN policy",
            "source_uri": "policy://employee-vpn",
            "allowed_roles": ["employee"],
        }
    )
    admin_document = document_body()
    admin_document.update(
        {
            "title": "Confidential admin break-glass policy",
            "content": "VPN emergency access uses confidential break-glass controls.",
            "source_uri": "policy://confidential-admin-vpn",
            "allowed_roles": ["admin"],
        }
    )

    with TestClient(application) as client:
        assert client.post(
            "/api/v1/knowledge/documents", json=employee_document
        ).status_code == 201
        assert client.post(
            "/api/v1/knowledge/documents", json=admin_document
        ).status_code == 201

        application.dependency_overrides[get_current_actor] = lambda: CurrentActor(
            user_id="EMP-1001",
            roles=frozenset({"employee"}),
        )
        response = client.post(
            "/api/v1/knowledge/search",
            json={
                "query": "VPN access policy",
                "knowledge_space": "access_and_security",
                "top_k": 10,
            },
        )
        impersonation = client.post(
            "/api/v1/knowledge/search",
            json={
                "query": "VPN access policy",
                "knowledge_space": "access_and_security",
                "top_k": 10,
                "actor_id": "EMP-ADMIN",
            },
        )

    assert response.status_code == 200
    citations = response.json()["citations"]
    assert [item["title"] for item in citations] == ["Employee VPN policy"]
    assert "Confidential admin break-glass policy" not in response.text
    assert "policy://confidential-admin-vpn" not in response.text
    assert "candidate_count" not in response.text
    assert impersonation.status_code == 422


@pytest.mark.parametrize("role", ["employee", "approver"])
def test_non_manager_roles_cannot_read_management_views(tmp_path, role: str) -> None:
    application, _ = build_app(tmp_path)
    application.dependency_overrides[get_current_actor] = lambda: manager_actor(role)

    with TestClient(application) as client:
        documents = client.get("/api/v1/knowledge/documents")
        jobs = client.get("/api/v1/knowledge/index-jobs")

    assert documents.status_code == 403
    assert documents.json()["error"]["code"] == "ACTION_FORBIDDEN"
    assert jobs.status_code == 403


def test_management_resources_use_stable_not_found_errors(tmp_path) -> None:
    application, _ = build_app(tmp_path)
    application.dependency_overrides[get_current_actor] = lambda: manager_actor()

    with TestClient(application) as client:
        document = client.get("/api/v1/knowledge/documents/missing-document")
        job = client.get("/api/v1/knowledge/index-jobs/missing-job")

    assert document.status_code == 404
    assert document.json()["error"]["code"] == "RESOURCE_NOT_FOUND"
    assert job.status_code == 404
    assert job.json()["error"]["code"] == "RESOURCE_NOT_FOUND"


def test_openapi_management_contract_does_not_expose_storage_keys(tmp_path) -> None:
    application, _ = build_app(tmp_path)

    with TestClient(application) as client:
        response = client.get("/openapi.json")

    assert response.status_code == 200
    schema = response.text
    assert "storage_key" not in schema
    assert "KnowledgeDocumentPage" in schema
    assert "KnowledgeRetrievalAuditPage" in schema
