"""Tree-of-Thought depth-first search for candidate accessibility fixes.

Depth T=3, k=3->5 adaptive sibling inflation on exhaustion (contrastive
negative constraints + temperature 0.2->0.6), a global cap of 15 node
evaluations, a 45s/candidate timeout, and pruning at composite score <=5.

`generate` and `score` are injected callables so this module has no LLM or
network dependency of its own - it is a pure function over plain data,
buildable and unit-testable before any real tool exists.
"""

from __future__ import annotations

import concurrent.futures
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

DEFAULT_MAX_DEPTH = 3
DEFAULT_MIN_SIBLINGS = 3
DEFAULT_MAX_SIBLINGS = 5
DEFAULT_GLOBAL_NODE_CAP = 15
DEFAULT_PRUNE_FLOOR = 5.0
DEFAULT_BASE_TEMPERATURE = 0.2
DEFAULT_MAX_TEMPERATURE = 0.6
DEFAULT_CANDIDATE_TIMEOUT_SECONDS = 45.0

# (parent_candidate_or_None, k, temperature, negative_constraints) -> list[candidate]
GenerateFn = Callable[[Any, int, float, list[str]], list[Any]]
# candidate -> composite score
ScoreFn = Callable[[Any], float]


@dataclass(frozen=True)
class ToTConfig:
    max_depth: int = DEFAULT_MAX_DEPTH
    min_siblings: int = DEFAULT_MIN_SIBLINGS
    max_siblings: int = DEFAULT_MAX_SIBLINGS
    global_node_cap: int = DEFAULT_GLOBAL_NODE_CAP
    prune_floor: float = DEFAULT_PRUNE_FLOOR
    base_temperature: float = DEFAULT_BASE_TEMPERATURE
    max_temperature: float = DEFAULT_MAX_TEMPERATURE
    candidate_timeout_seconds: float = DEFAULT_CANDIDATE_TIMEOUT_SECONDS


@dataclass
class ToTNode:
    node_id: str
    parent_id: str | None
    depth: int
    candidate: Any
    score: float
    pruned: bool
    timed_out: bool = False
    children: list[ToTNode] = field(default_factory=list)


@dataclass
class ToTResult:
    root: ToTNode | None
    best_node: ToTNode | None
    node_evals: int
    satisfied: bool


class _NodeIdGenerator:
    def __init__(self) -> None:
        self._counter = 0

    def next_id(self) -> str:
        self._counter += 1
        return f"n{self._counter}"


def _score_with_timeout(
    score: ScoreFn, candidate: Any, timeout_seconds: float
) -> tuple[float, bool]:
    """Run `score(candidate)` under a wall-clock timeout.

    A slow real evaluation (e.g. `ng build`) must not hang the search forever.
    A timeout scores as `0.0` (always pruned) and is flagged via `timed_out`.
    """
    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
        future = executor.submit(score, candidate)
        try:
            return future.result(timeout=timeout_seconds), False
        except concurrent.futures.TimeoutError:
            return 0.0, True


def dfs_search(
    root_candidate: Any,
    generate: GenerateFn,
    score: ScoreFn,
    config: ToTConfig | None = None,
) -> ToTResult:
    """Depth-first Tree-of-Thought search for the best candidate fix.

    The root candidate is scored first - a node is pruned (and never
    expanded) the moment its own score falls at or below `prune_floor`, so
    real scores must be computed before this call, not hardcoded to 0.
    At each expanded node, `min_siblings` children are generated; if every
    one of them is pruned, a single inflation retry generates
    `max_siblings` children at `max_temperature`, seeded with negative
    constraints describing the failed attempts. Recursion always follows
    the single best surviving child (a global eval budget this small
    cannot afford multi-branch backtracking).
    """
    cfg = config or ToTConfig()
    ids = _NodeIdGenerator()
    evals = 0
    best: ToTNode | None = None

    def evaluate(candidate: Any, parent_id: str | None, depth: int) -> ToTNode:
        nonlocal evals, best
        node_score, timed_out = _score_with_timeout(
            score, candidate, cfg.candidate_timeout_seconds
        )
        evals += 1
        node = ToTNode(
            node_id=ids.next_id(),
            parent_id=parent_id,
            depth=depth,
            candidate=candidate,
            score=node_score,
            pruned=node_score <= cfg.prune_floor,
            timed_out=timed_out,
        )
        if best is None or node.score > best.score:
            best = node
        return node

    def generate_and_score(
        parent: ToTNode, k: int, temperature: float, negatives: list[str]
    ) -> list[ToTNode]:
        remaining = cfg.global_node_cap - evals
        if remaining <= 0:
            return []
        candidates = generate(
            parent.candidate, min(k, remaining), temperature, negatives
        )
        nodes: list[ToTNode] = []
        for candidate in candidates:
            if evals >= cfg.global_node_cap:
                break
            nodes.append(evaluate(candidate, parent.node_id, parent.depth + 1))
        return nodes

    def expand(node: ToTNode) -> None:
        if node.pruned or node.depth >= cfg.max_depth or evals >= cfg.global_node_cap:
            return

        children = generate_and_score(node, cfg.min_siblings, cfg.base_temperature, [])
        if children and all(child.pruned for child in children):
            negatives = [
                f"candidate scored {child.score}: {child.candidate!r}"
                for child in children
            ]
            children = children + generate_and_score(
                node, cfg.max_siblings, cfg.max_temperature, negatives
            )

        node.children = children
        surviving = [c for c in children if not c.pruned]
        if not surviving:
            return
        expand(max(surviving, key=lambda c: c.score))

    root = evaluate(root_candidate, None, depth=0)
    expand(root)

    satisfied = best is not None and not best.pruned
    return ToTResult(root=root, best_node=best, node_evals=evals, satisfied=satisfied)
