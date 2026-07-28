from datetime import UTC, datetime, timedelta
from pathlib import Path

from app.evaluation.contracts import EvaluationCategory
from app.evaluation.runner import EvaluationRunner, load_evaluation_suite
from app.knowledge.contracts import (
    KnowledgeIndexStatus,
    KnowledgePublicationStatus,
    KnowledgeTrustLevel,
)
from app.knowledge.models import KnowledgeDocument
from tests.knowledge.test_service import build_service


def suite_path() -> Path:
    return (
        Path(__file__).resolve().parents[3]
        / "evaluations"
        / "v3_knowledge_governance.json"
    )


def test_v3_governance_suite_uses_real_authorization_and_lifecycle_filters(
    tmp_path,
) -> None:
    sessions, _, service = build_service(tmp_path)
    now = datetime.now(UTC)

    def add_document(
        source_uri: str,
        *,
        version_label: str,
        knowledge_space: str = "access_and_security",
        effective_from: datetime | None = None,
        effective_until: datetime | None = None,
        allowed_roles: frozenset[str] = frozenset(),
        allowed_user_ids: frozenset[str] = frozenset(),
    ):
        return service.ingest_text(
            knowledge_space=knowledge_space,
            title=f"Governance fixture {source_uri}",
            content=(
                "governance visibility policy fixed evaluation content "
                f"for {source_uri} {version_label}"
            ),
            source_uri=source_uri,
            version_label=version_label,
            source_department="Evaluation",
            trust_level=KnowledgeTrustLevel.AUTHORITATIVE,
            effective_from=effective_from,
            effective_until=effective_until,
            allowed_roles=allowed_roles,
            allowed_user_ids=allowed_user_ids,
            actor_id="EMP-EVALUATION-SEED",
        )

    documents = {
        "public": add_document("policy://v3/public", version_label="public-v1"),
        "draft": add_document("policy://v3/draft", version_label="draft-v1"),
        "unindexed": add_document(
            "policy://v3/unindexed", version_label="unindexed-v1"
        ),
        "future": add_document(
            "policy://v3/future",
            version_label="future-v1",
            effective_from=now + timedelta(days=30),
        ),
        "expired": add_document(
            "policy://v3/expired",
            version_label="expired-v1",
            effective_from=now - timedelta(days=60),
            effective_until=now - timedelta(days=30),
        ),
        "admin": add_document(
            "policy://v3/admin",
            version_label="admin-v1",
            allowed_roles=frozenset({"admin"}),
        ),
        "user": add_document(
            "policy://v3/user-specific",
            version_label="user-v1",
            allowed_user_ids=frozenset({"EMP-TARGET"}),
        ),
        "old": add_document(
            "policy://v3/versioned", version_label="obsolete-v1"
        ),
        "current": add_document(
            "policy://v3/versioned", version_label="current-v2"
        ),
        "retired": add_document(
            "policy://v3/retired", version_label="retired-v1"
        ),
        "equipment": add_document(
            "policy://v3/equipment",
            version_label="equipment-v1",
            knowledge_space="equipment_maintenance",
        ),
    }

    with sessions.begin() as session:
        session.get(KnowledgeDocument, documents["draft"].document_id).publication_status = (
            KnowledgePublicationStatus.DRAFT.value
        )
        session.get(KnowledgeDocument, documents["unindexed"].document_id).index_status = (
            KnowledgeIndexStatus.PENDING.value
        )
        session.get(KnowledgeDocument, documents["old"].document_id).publication_status = (
            KnowledgePublicationStatus.RETIRED.value
        )
        session.get(KnowledgeDocument, documents["retired"].document_id).publication_status = (
            KnowledgePublicationStatus.RETIRED.value
        )

    suite = load_evaluation_suite(suite_path())
    report = EvaluationRunner(knowledge=service).run(suite)

    assert len(suite.cases) == 12
    assert {case.category for case in suite.cases} == {
        EvaluationCategory.KNOWLEDGE_GOVERNANCE
    }
    assert report.total == report.passed == 12
    assert report.failed == 0
    assert report.categories[0].category is EvaluationCategory.KNOWLEDGE_GOVERNANCE
    assert report.categories[0].pass_rate == 1.0

