"""Deterministic paragraph-aware text chunking."""

import re


class KnowledgeTextChunker:
    """Split normalized text while retaining overlap for retrieval context."""

    def __init__(self, *, chunk_size: int = 800, chunk_overlap: int = 120) -> None:
        if chunk_size < 100:
            raise ValueError("chunk_size must be at least 100")
        if chunk_overlap < 0 or chunk_overlap >= chunk_size:
            raise ValueError("chunk_overlap must be between 0 and chunk_size")
        self.chunk_size = chunk_size
        self.chunk_overlap = chunk_overlap

    @property
    def index_version(self) -> str:
        return f"te4-d1024-c{self.chunk_size}-o{self.chunk_overlap}-v1"

    def split(self, text: str) -> tuple[str, ...]:
        normalized = re.sub(r"[\t ]+", " ", text.replace("\r\n", "\n"))
        normalized = re.sub(r"\n{3,}", "\n\n", normalized).strip()
        if not normalized:
            raise ValueError("knowledge content must not be blank")

        chunks: list[str] = []
        start = 0
        while start < len(normalized):
            hard_end = min(start + self.chunk_size, len(normalized))
            end = hard_end
            if hard_end < len(normalized):
                search_start = start + self.chunk_size // 2
                candidates = [
                    normalized.rfind(separator, search_start, hard_end)
                    for separator in ("\n\n", "\n", "。", ". ", "；", "; ")
                ]
                boundary = max(candidates)
                if boundary > start:
                    end = boundary + 1
            chunk = normalized[start:end].strip()
            if chunk:
                chunks.append(chunk)
            if end >= len(normalized):
                break
            next_start = max(end - self.chunk_overlap, start + 1)
            start = next_start
        return tuple(chunks)
