"""MMR (Maximal Marginal Relevance) semantic-search fallback for wiki lesson
retrieval. The selection loop is ported near-verbatim from Module-03's
`hybrid_retrieval_demo.py` (`StatelessSemanticSearch.retrieve_mmr`),
generalized from its hardcoded five-document mock corpus to arbitrary text
chunks. Embeddings use a lightweight, dependency-free hashing-trick vector
rather than a real embedding API - appropriate for what is explicitly a
fallback over a small local wiki, not a production RAG index.
"""

from __future__ import annotations

import math
import re
import zlib
from dataclasses import dataclass

DEFAULT_LAMBDA = 0.5
DEFAULT_TOP_K = 3
DEFAULT_VECTOR_DIM = 256

_TOKEN_RE = re.compile(r"[a-z0-9]+")


def _tokenize(text: str) -> list[str]:
    return _TOKEN_RE.findall(text.lower())


def embed(text: str, dim: int = DEFAULT_VECTOR_DIM) -> list[float]:
    """A deterministic, dependency-free hashing-trick bag-of-words vector.

    Not a semantic embedding model - a stand-in so this fallback path has no
    network/API dependency of its own. Uses `zlib.crc32`, not the builtin
    `hash()`: Python randomizes `hash()` for strings per-process
    (`PYTHONHASHSEED`) unless disabled, so it is deterministic only within a
    single run, not across separate invocations.
    """
    vector = [0.0] * dim
    for token in _tokenize(text):
        vector[zlib.crc32(token.encode("utf-8")) % dim] += 1.0
    return vector


def _cosine_similarity(vec_a: list[float], vec_b: list[float]) -> float:
    dot = sum(a * b for a, b in zip(vec_a, vec_b, strict=True))
    norm_a = math.sqrt(sum(a * a for a in vec_a))
    norm_b = math.sqrt(sum(b * b for b in vec_b))
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return dot / (norm_a * norm_b)


@dataclass(frozen=True)
class Chunk:
    id: str
    text: str
    vector: list[float]


def retrieve_mmr(
    query: str,
    chunks: list[Chunk],
    top_k: int = DEFAULT_TOP_K,
    lmbda: float = DEFAULT_LAMBDA,
) -> list[Chunk]:
    """Maximal Marginal Relevance reranking over `chunks`.

    Stage 1: rank all chunks by cosine similarity to the query.
    Stage 2: greedily select `top_k`, trading query relevance against
    redundancy with chunks already selected:
    `MMR = lambda * Sim(query, doc) - (1 - lambda) * max_sim(doc, selected)`.
    """
    if not chunks:
        return []
    query_vector = embed(query, dim=len(chunks[0].vector))

    candidates = [(chunk, _cosine_similarity(query_vector, chunk.vector)) for chunk in chunks]
    candidates.sort(key=lambda pair: pair[1], reverse=True)

    selected: list[tuple[Chunk, float]] = []
    unselected = list(range(len(candidates)))

    while len(selected) < top_k and unselected:
        best_score = -float("inf")
        best_idx = -1
        for idx in unselected:
            chunk, query_sim = candidates[idx]
            if not selected:
                mmr_score = query_sim
            else:
                max_sim_selected = max(_cosine_similarity(chunk.vector, sel.vector) for sel, _ in selected)
                mmr_score = lmbda * query_sim - (1 - lmbda) * max_sim_selected
            if mmr_score > best_score:
                best_score = mmr_score
                best_idx = idx
        selected.append(candidates[best_idx])
        unselected.remove(best_idx)

    return [chunk for chunk, _ in selected]
