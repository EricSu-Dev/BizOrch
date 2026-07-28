"""Persistent Chroma vector index adapter."""

from collections.abc import Sequence
from pathlib import Path
from typing import Any

from app.knowledge.contracts import VectorMatch, VectorRecord


class VectorStoreConfigurationError(RuntimeError):
    """Raised when Chroma cannot be loaded or configured."""


class ChromaKnowledgeVectorStore:
    """Store only reconstructable vectors and minimal lookup metadata."""

    COLLECTION_NAME = "bizorch-te4-d1024-c800-o120-v1"

    def __init__(self, path: Path, *, client: Any | None = None) -> None:
        self._path = Path(path)
        self._injected_client = client
        self._resolved_client: Any | None = None
        self._collection: Any | None = None

    def upsert(self, records: Sequence[VectorRecord]) -> None:
        if not records:
            return
        self._get_collection().upsert(
            ids=[record.chunk_id for record in records],
            embeddings=[list(record.embedding) for record in records],
            documents=[record.content for record in records],
            metadatas=[
                {
                    "document_id": record.document_id,
                    "knowledge_space": record.knowledge_space,
                }
                for record in records
            ],
        )

    def query(
        self,
        embedding: Sequence[float],
        *,
        knowledge_space: str,
        limit: int,
    ) -> tuple[VectorMatch, ...]:
        collection = self._get_collection()
        if collection.count() == 0:
            return ()
        result = collection.query(
            query_embeddings=[list(embedding)],
            n_results=limit,
            where={"knowledge_space": knowledge_space},
            include=["distances"],
        )
        ids = result.get("ids", [[]])[0]
        distances = result.get("distances", [[]])[0]
        return tuple(
            VectorMatch(chunk_id=chunk_id, distance=float(distance))
            for chunk_id, distance in zip(ids, distances, strict=True)
        )

    def delete_document(self, document_id: str) -> None:
        self._get_collection().delete(where={"document_id": document_id})

    def _get_collection(self):
        if self._collection is None:
            self._collection = self._client().get_or_create_collection(
                name=self.COLLECTION_NAME,
                metadata={
                    "hnsw:space": "cosine",
                    "index_version": "te4-d1024-c800-o120-v1",
                },
            )
        return self._collection

    def _client(self):
        if self._injected_client is not None:
            return self._injected_client
        if self._resolved_client is not None:
            return self._resolved_client
        try:
            import chromadb
        except ImportError as exc:
            raise VectorStoreConfigurationError(
                "chromadb dependency is unavailable"
            ) from exc
        self._path.mkdir(parents=True, exist_ok=True)
        self._resolved_client = chromadb.PersistentClient(path=str(self._path))
        return self._resolved_client
