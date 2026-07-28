from types import SimpleNamespace

import pytest

from app.knowledge.embeddings import (
    DashScopeEmbeddings,
    EmbeddingConfigurationError,
    EmbeddingResponseError,
)


class FakeEmbeddingEndpoint:
    def __init__(self, dimension: int = 1024) -> None:
        self.dimension = dimension
        self.calls: list[dict[str, object]] = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        inputs = kwargs["input"]
        return SimpleNamespace(
            data=[
                SimpleNamespace(index=index, embedding=[float(index)] * self.dimension)
                for index, _ in enumerate(inputs)
            ]
        )


def test_dashscope_embeddings_batches_at_ten_and_preserves_count() -> None:
    endpoint = FakeEmbeddingEndpoint()
    client = SimpleNamespace(embeddings=endpoint)
    embeddings = DashScopeEmbeddings("unused", client=client)

    vectors = embeddings.embed_documents([f"text-{index}" for index in range(11)])

    assert len(vectors) == 11
    assert [len(call["input"]) for call in endpoint.calls] == [10, 1]
    assert all(call["model"] == "text-embedding-v4" for call in endpoint.calls)
    assert all(call["dimensions"] == 1024 for call in endpoint.calls)


def test_dashscope_embeddings_rejects_wrong_dimension() -> None:
    client = SimpleNamespace(embeddings=FakeEmbeddingEndpoint(dimension=3))
    with pytest.raises(EmbeddingResponseError):
        DashScopeEmbeddings("unused", client=client).embed_query("policy")


def test_dashscope_embeddings_requires_key_only_when_called() -> None:
    embeddings = DashScopeEmbeddings(None)
    with pytest.raises(EmbeddingConfigurationError):
        embeddings.embed_query("policy")
