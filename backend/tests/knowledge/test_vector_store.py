from types import SimpleNamespace

import pytest

from app.knowledge.contracts import VectorRecord
from app.knowledge.vector_store import ChromaKnowledgeVectorStore


class FakeCollection:
    def __init__(self) -> None:
        self.upserts = []
        self.deletes = []

    def upsert(self, **kwargs) -> None:
        self.upserts.append(kwargs)

    def count(self) -> int:
        return 1

    def query(self, **kwargs):
        assert kwargs["where"] == {"knowledge_space": "access_and_security"}
        return {"ids": [["chunk-1"]], "distances": [[0.125]]}

    def delete(self, **kwargs) -> None:
        self.deletes.append(kwargs)


def test_chroma_adapter_uses_minimal_metadata_and_cosine_distance(tmp_path) -> None:
    collection = FakeCollection()
    client = SimpleNamespace(
        get_or_create_collection=lambda **kwargs: collection,
    )
    store = ChromaKnowledgeVectorStore(tmp_path, client=client)
    store.upsert(
        [
            VectorRecord(
                chunk_id="chunk-1",
                document_id="document-1",
                knowledge_space="access_and_security",
                content="VPN policy",
                embedding=(1.0, 0.0, 0.0),
            )
        ]
    )

    assert collection.upserts[0]["ids"] == ["chunk-1"]
    assert collection.upserts[0]["metadatas"] == [
        {
            "document_id": "document-1",
            "knowledge_space": "access_and_security",
        }
    ]
    matches = store.query(
        [1.0, 0.0, 0.0],
        knowledge_space="access_and_security",
        limit=5,
    )
    assert matches[0].chunk_id == "chunk-1"
    assert matches[0].distance == 0.125

    store.delete_document("document-1")
    assert collection.deletes == [{"where": {"document_id": "document-1"}}]


@pytest.mark.filterwarnings(
    "ignore:.*asyncio.iscoroutinefunction.*:DeprecationWarning"
)
def test_real_chroma_persists_and_filters_records(tmp_path) -> None:
    path = tmp_path / "real-chroma"
    first = ChromaKnowledgeVectorStore(path)
    first.upsert(
        [
            VectorRecord(
                chunk_id="access-chunk",
                document_id="access-document",
                knowledge_space="access_and_security",
                content="temporary CRM access",
                embedding=(1.0, 0.0, 0.0),
            ),
            VectorRecord(
                chunk_id="other-chunk",
                document_id="other-document",
                knowledge_space="employee_services",
                content="employee benefits",
                embedding=(1.0, 0.0, 0.0),
            ),
        ]
    )

    reopened = ChromaKnowledgeVectorStore(path)
    matches = reopened.query(
        [1.0, 0.0, 0.0],
        knowledge_space="access_and_security",
        limit=5,
    )

    assert [match.chunk_id for match in matches] == ["access-chunk"]
