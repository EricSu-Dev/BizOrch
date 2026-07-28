from app.knowledge.contracts import (
    KnowledgeIndexStatus,
    KnowledgePublicationStatus,
    knowledge_index_transition_allowed,
    knowledge_publication_transition_allowed,
)


def test_publication_lifecycle_is_forward_only() -> None:
    assert knowledge_publication_transition_allowed(
        KnowledgePublicationStatus.DRAFT,
        KnowledgePublicationStatus.PUBLISHED,
    )
    assert knowledge_publication_transition_allowed(
        KnowledgePublicationStatus.PUBLISHED,
        KnowledgePublicationStatus.RETIRED,
    )
    assert not knowledge_publication_transition_allowed(
        KnowledgePublicationStatus.RETIRED,
        KnowledgePublicationStatus.PUBLISHED,
    )
    assert knowledge_publication_transition_allowed(
        KnowledgePublicationStatus.DRAFT,
        KnowledgePublicationStatus.RETIRED,
    )


def test_index_lifecycle_supports_legacy_sync_and_durable_worker_paths() -> None:
    assert knowledge_index_transition_allowed(
        KnowledgeIndexStatus.PENDING,
        KnowledgeIndexStatus.INDEXED,
    )
    assert knowledge_index_transition_allowed(
        KnowledgeIndexStatus.PENDING,
        KnowledgeIndexStatus.INDEXING,
    )
    assert knowledge_index_transition_allowed(
        KnowledgeIndexStatus.INDEXING,
        KnowledgeIndexStatus.FAILED,
    )
    assert knowledge_index_transition_allowed(
        KnowledgeIndexStatus.FAILED,
        KnowledgeIndexStatus.PENDING,
    )
    assert not knowledge_index_transition_allowed(
        KnowledgeIndexStatus.INDEXED,
        KnowledgeIndexStatus.FAILED,
    )
