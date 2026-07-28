"""Lightweight deterministic BM25 scoring for hybrid retrieval."""

from collections import Counter
from collections.abc import Sequence
from math import log
import re


_TOKEN_PATTERN = re.compile(r"[a-zA-Z0-9_]+|[\u4e00-\u9fff]")


def tokenize(text: str) -> list[str]:
    """Tokenize ASCII terms and individual CJK characters without a large model."""
    return [match.group(0).lower() for match in _TOKEN_PATTERN.finditer(text)]


def normalized_bm25_scores(
    query: str,
    documents: Sequence[str],
    *,
    k1: float = 1.5,
    b: float = 0.75,
) -> tuple[float, ...]:
    """Return zero-to-one BM25 scores in the same order as documents."""
    if not documents:
        return ()
    query_terms = set(tokenize(query))
    tokenized = [tokenize(document) for document in documents]
    if not query_terms or not any(tokenized):
        return tuple(0.0 for _ in documents)

    document_count = len(tokenized)
    average_length = sum(len(tokens) for tokens in tokenized) / document_count
    document_frequency = {
        term: sum(1 for tokens in tokenized if term in set(tokens))
        for term in query_terms
    }
    raw_scores: list[float] = []
    for tokens in tokenized:
        frequencies = Counter(tokens)
        score = 0.0
        for term in query_terms:
            frequency = frequencies[term]
            if frequency == 0:
                continue
            frequency_docs = document_frequency[term]
            inverse_frequency = log(
                1 + (document_count - frequency_docs + 0.5) / (frequency_docs + 0.5)
            )
            length_ratio = len(tokens) / average_length if average_length else 0
            denominator = frequency + k1 * (1 - b + b * length_ratio)
            score += inverse_frequency * frequency * (k1 + 1) / denominator
        raw_scores.append(score)
    maximum = max(raw_scores, default=0.0)
    if maximum <= 0:
        return tuple(0.0 for _ in raw_scores)
    return tuple(score / maximum for score in raw_scores)
