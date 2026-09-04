from __future__ import annotations

from a11y_fixer.adapters.retrieval.semantic_search import Chunk, embed, retrieve_mmr


def test_embed_is_deterministic() -> None:
    assert embed("keyboard focus trap") == embed("keyboard focus trap")


def test_embed_differs_for_different_text() -> None:
    assert embed("keyboard focus trap") != embed("color contrast ratio")


def test_retrieve_mmr_on_empty_chunks_returns_empty() -> None:
    assert retrieve_mmr("query", []) == []


def test_retrieve_mmr_returns_at_most_available_chunks() -> None:
    chunks = [Chunk(id="a", text="alpha beta", vector=embed("alpha beta"))]
    result = retrieve_mmr("alpha", chunks, top_k=3)
    assert len(result) == 1


def test_retrieve_mmr_prefers_diversity_over_pure_similarity() -> None:
    # a/b are near-duplicates and both highly relevant to the query; c is only
    # weakly relevant but shares nothing with a/b. Once a is picked, MMR's
    # redundancy penalty should make c's (undiminished) relevance beat b's
    # (heavily redundant) relevance for the second pick.
    query = "keyboard focus trap"
    chunk_a = Chunk(id="a", text="keyboard focus trap accessible dialog", vector=embed("keyboard focus trap accessible dialog"))
    chunk_b = Chunk(id="b", text="keyboard focus trap widget dialog", vector=embed("keyboard focus trap widget dialog"))
    chunk_c = Chunk(id="c", text="focus order heading structure", vector=embed("focus order heading structure"))

    result = retrieve_mmr(query, [chunk_a, chunk_b, chunk_c], top_k=2, lmbda=0.5)

    assert len(result) == 2
    selected_ids = {chunk.id for chunk in result}
    assert selected_ids == {"a", "c"}


def test_retrieve_mmr_lambda_one_ignores_redundancy_penalty() -> None:
    # lambda=1.0 degenerates to pure similarity ranking (no diversity penalty)
    query = "keyboard focus trap"
    chunk_a = Chunk(id="a", text="keyboard focus trap", vector=embed("keyboard focus trap"))
    chunk_b = Chunk(id="b", text="keyboard focus trap", vector=embed("keyboard focus trap"))
    chunk_c = Chunk(id="c", text="totally unrelated topic", vector=embed("totally unrelated topic"))

    result = retrieve_mmr(query, [chunk_a, chunk_b, chunk_c], top_k=2, lmbda=1.0)

    selected_ids = {chunk.id for chunk in result}
    assert selected_ids == {"a", "b"}
