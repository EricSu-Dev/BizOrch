from collections.abc import Sequence
from math import sqrt

from app.knowledge.contracts import VectorMatch, VectorRecord


class FakeEmbeddings:
    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return [self._vector(text) for text in texts]

    def embed_query(self, text: str) -> list[float]:
        return self._vector(text)

    @staticmethod
    def _vector(text: str) -> list[float]:
        lowered = text.lower()
        return [
            float("vpn" in lowered or "远程" in text),
            float("财务" in text or "finance" in lowered),
            0.1,
        ]


class InMemoryVectorStore:
    def __init__(self) -> None:
        self.records: dict[str, VectorRecord] = {}

    def upsert(self, records: Sequence[VectorRecord]) -> None:
        self.records.update({record.chunk_id: record for record in records})

    def query(
        self,
        embedding: Sequence[float],
        *,
        knowledge_space: str,
        limit: int,
    ) -> tuple[VectorMatch, ...]:
        matches = [
            VectorMatch(
                chunk_id=record.chunk_id,
                distance=1.0 - self._cosine(embedding, record.embedding),
            )
            for record in self.records.values()
            if record.knowledge_space == knowledge_space
        ]
        return tuple(sorted(matches, key=lambda match: match.distance)[:limit])

    def delete_document(self, document_id: str) -> None:
        self.records = {
            chunk_id: record
            for chunk_id, record in self.records.items()
            if record.document_id != document_id
        }

    @staticmethod
    def _cosine(left: Sequence[float], right: Sequence[float]) -> float:
        dot = sum(a * b for a, b in zip(left, right, strict=True))
        left_norm = sqrt(sum(value * value for value in left))
        right_norm = sqrt(sum(value * value for value in right))
        return dot / (left_norm * right_norm) if left_norm and right_norm else 0.0
