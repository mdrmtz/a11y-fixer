from __future__ import annotations

import time

import pytest

from a11y_fixer.domain import tot_search
from a11y_fixer.domain.tot_search import GenerateFn, ScoreFn, ToTConfig, dfs_search


def _make_generate(children_by_candidate: dict[str, list[str]]) -> GenerateFn:
    """A deterministic generator: candidate -> list of pre-scripted children."""

    def generate(parent: str, k: int, temperature: float, negatives: list[str]) -> list[str]:  # noqa: ARG001
        return children_by_candidate.get(parent, [])[:k]

    return generate


def _make_score(scores: dict[str, float]) -> ScoreFn:
    def score(candidate: str) -> float:
        return scores[candidate]

    return score


def test_root_pruned_immediately_never_expands() -> None:
    generate = _make_generate({})
    score = _make_score({"root": 1.0})

    result = dfs_search("root", generate, score, ToTConfig(prune_floor=5.0))

    assert result.node_evals == 1
    assert result.satisfied is False
    assert result.root is not None
    assert result.root.pruned is True
    assert result.root.children == []


def test_follows_best_surviving_child_down_to_max_depth() -> None:
    generate = _make_generate(
        {
            "root": ["a-low", "a-high", "a-mid"],
            "a-high": ["b-low", "b-high"],
            "b-high": ["c-final"],
        }
    )
    score = _make_score(
        {
            "root": 6.0,
            "a-low": 6.0,
            "a-high": 9.0,
            "a-mid": 7.0,
            "b-low": 6.0,
            "b-high": 8.0,
            "c-final": 9.5,
        }
    )

    result = dfs_search("root", generate, score, ToTConfig(max_depth=3, min_siblings=3, prune_floor=5.0))

    assert result.satisfied is True
    assert result.best_node is not None
    assert result.best_node.candidate == "c-final"
    # root(1) + 3 siblings + best child expanded(2 siblings) + best-of-those expanded(1 sibling) = 7
    assert result.node_evals == 7


def test_global_node_cap_is_enforced() -> None:
    generate = _make_generate(
        {
            "root": ["a1", "a2", "a3"],
            "a1": ["b1", "b2", "b3"],
            "a2": ["b4", "b5", "b6"],
            "a3": ["b7", "b8", "b9"],
        }
    )
    score = _make_score({c: 10.0 for c in ["root", "a1", "a2", "a3", "b1", "b2", "b3", "b4", "b5", "b6", "b7", "b8", "b9"]})

    result = dfs_search("root", generate, score, ToTConfig(max_depth=3, min_siblings=3, global_node_cap=5))

    assert result.node_evals <= 5


def test_depth_cap_stops_expansion() -> None:
    generate = _make_generate({"root": ["a1"], "a1": ["b1"], "b1": ["c1"]})
    score = _make_score({"root": 8.0, "a1": 10.0, "b1": 10.0, "c1": 10.0})

    result = dfs_search("root", generate, score, ToTConfig(max_depth=1, min_siblings=1))

    # root(depth 0) expands once to a1(depth 1); depth 1 >= max_depth(1) stops further expansion
    assert result.node_evals == 2
    assert result.best_node is not None
    assert result.best_node.candidate == "a1"


def test_sibling_inflation_triggers_on_total_exhaustion() -> None:
    calls: list[tuple[int, float, list[str]]] = []

    def generate(parent: str, k: int, temperature: float, negatives: list[str]) -> list[str]:
        calls.append((k, temperature, negatives))
        if temperature == 0.2:  # noqa: PLR2004 - initial attempt: all bad
            return ["bad1", "bad2", "bad3"][:k]
        return ["good1", "good2", "good3", "good4", "good5"][:k]  # inflated attempt: recovers

    score = _make_score(
        {
            "root": 6.0,
            "bad1": 1.0,
            "bad2": 1.0,
            "bad3": 1.0,
            "good1": 9.0,
            "good2": 3.0,
            "good3": 2.0,
            "good4": 2.0,
            "good5": 2.0,
        }
    )

    result = dfs_search(
        "root",
        generate,
        score,
        ToTConfig(max_depth=1, min_siblings=3, max_siblings=5, base_temperature=0.2, max_temperature=0.6, prune_floor=5.0),
    )

    assert len(calls) == 2
    initial_call, inflated_call = calls
    assert initial_call == (3, 0.2, [])
    assert inflated_call[0] == 5
    assert inflated_call[1] == 0.6
    assert len(inflated_call[2]) == 3  # one negative constraint per pruned sibling
    assert result.satisfied is True
    assert result.best_node is not None
    assert result.best_node.candidate == "good1"


def test_candidate_timeout_scores_zero_and_is_pruned() -> None:
    def slow_score(_candidate: str) -> float:
        time.sleep(0.2)
        return 10.0

    result = dfs_search("root", _make_generate({}), slow_score, ToTConfig(candidate_timeout_seconds=0.05))

    assert result.root is not None
    assert result.root.timed_out is True
    assert result.root.score == 0.0
    assert result.root.pruned is True


def test_score_at_exactly_prune_floor_is_pruned() -> None:
    result = dfs_search("root", _make_generate({}), _make_score({"root": 5.0}), ToTConfig(prune_floor=5.0))
    assert result.root is not None
    assert result.root.pruned is True


@pytest.mark.parametrize("field", ["max_depth", "min_siblings", "max_siblings", "global_node_cap"])
def test_config_defaults_match_plan(field: str) -> None:
    defaults = {
        "max_depth": tot_search.DEFAULT_MAX_DEPTH,
        "min_siblings": tot_search.DEFAULT_MIN_SIBLINGS,
        "max_siblings": tot_search.DEFAULT_MAX_SIBLINGS,
        "global_node_cap": tot_search.DEFAULT_GLOBAL_NODE_CAP,
    }
    expected = {"max_depth": 3, "min_siblings": 3, "max_siblings": 5, "global_node_cap": 15}
    assert defaults[field] == expected[field]
