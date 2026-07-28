"""LangChain-compatible adapter for DashScope remote embeddings."""

from typing import Any, Protocol

from langchain_core.embeddings import Embeddings


class EmbeddingConfigurationError(RuntimeError):
    """Raised when remote embedding dependencies or credentials are unavailable."""


class EmbeddingResponseError(RuntimeError):
    """Raised when the provider returns malformed or wrong-dimension vectors."""


class EmbeddingUsageObserver(Protocol):
    def before_embedding_call(self, *, text_count: int) -> None: ...

    def after_embedding_call(self, *, input_tokens: int) -> None: ...


class DashScopeEmbeddings(Embeddings):
    """Call text-embedding-v4 in batches without loading a local model."""

    MODEL = "text-embedding-v4"
    DIMENSIONS = 1024
    BATCH_SIZE = 10
    BASE_URL = "https://dashscope.aliyuncs.com/compatible-mode/v1"

    def __init__(
        self,
        api_key: str | None,
        *,
        client: Any | None = None,
        usage_observer: EmbeddingUsageObserver | None = None,
        request_timeout_seconds: float = 60.0,
    ) -> None:
        self._api_key = api_key.strip() if api_key else None
        self._injected_client = client
        self._resolved_client: Any | None = None
        self._usage_observer = usage_observer
        self._request_timeout_seconds = request_timeout_seconds
        if request_timeout_seconds <= 0:
            raise ValueError("request_timeout_seconds must be positive")

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        vectors: list[list[float]] = []
        for start in range(0, len(texts), self.BATCH_SIZE):
            batch = texts[start : start + self.BATCH_SIZE]
            if self._usage_observer is not None:
                self._usage_observer.before_embedding_call(text_count=len(batch))
            response = self._client().embeddings.create(
                model=self.MODEL,
                input=batch,
                dimensions=self.DIMENSIONS,
                timeout=self._request_timeout_seconds,
            )
            if self._usage_observer is not None:
                usage = getattr(response, "usage", None)
                self._usage_observer.after_embedding_call(
                    input_tokens=max(
                        0,
                        int(
                            getattr(usage, "prompt_tokens", None)
                            or getattr(usage, "total_tokens", 0)
                            or 0
                        ),
                    )
                )
            data = sorted(response.data, key=lambda item: item.index)
            batch_vectors = [list(item.embedding) for item in data]
            if len(batch_vectors) != len(batch):
                raise EmbeddingResponseError("embedding response count mismatch")
            for vector in batch_vectors:
                if len(vector) != self.DIMENSIONS:
                    raise EmbeddingResponseError("embedding dimension mismatch")
            vectors.extend(batch_vectors)
        return vectors

    def embed_query(self, text: str) -> list[float]:
        normalized = text.strip()
        if not normalized:
            raise ValueError("embedding query must not be blank")
        return self.embed_documents([normalized])[0]

    def _client(self):
        if self._injected_client is not None:
            return self._injected_client
        if self._resolved_client is not None:
            return self._resolved_client
        if not self._api_key:
            raise EmbeddingConfigurationError("DASHSCOPE_API_KEY is not configured")
        try:
            from openai import OpenAI
        except ImportError as exc:
            raise EmbeddingConfigurationError(
                "openai dependency is unavailable"
            ) from exc
        self._resolved_client = OpenAI(
            api_key=self._api_key,
            base_url=self.BASE_URL,
        )
        return self._resolved_client
