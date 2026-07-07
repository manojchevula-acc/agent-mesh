"""Deterministic join-path resolution for the dynamic tier (Component B / §5.3).

Given the planner's chosen tables (and optionally the pairs it wants connected), compute
the MINIMAL join path from the declared relationship graph — the smallest set of edges
that connects those tables, pulling in a BRIDGE table only when two chosen tables are not
directly related. The LLM never chooses join KEYS; it only names which tables to connect.

Returns:
  clauses       — human-readable "A JOIN B ON (rule)" lines to render into the prompt,
  allowed_pairs — the equi-join table pairs the validator's check #7 enforces,
  used_tables   — chosen tables PLUS any bridge tables, so the render includes them all.
"""

from __future__ import annotations

from collections import deque

from sql_agent.semantic_layer.loader import relationship_graph


def _shortest_path(graph: dict, src: str, dst: str) -> list[str]:
    """BFS shortest path src->dst over the undirected relationship graph, or []."""
    if src == dst:
        return [src]
    seen = {src}
    queue: deque[list[str]] = deque([[src]])
    while queue:
        path = queue.popleft()
        for nbr in graph.get(path[-1], {}):
            if nbr in seen:
                continue
            if nbr == dst:
                return path + [nbr]
            seen.add(nbr)
            queue.append(path + [nbr])
    return []


def resolve_joins(
    tables: set[str], wanted_pairs: list[tuple[str, str]] | None = None
) -> tuple[list[str], set[frozenset[str]], set[str]]:
    graph = relationship_graph()

    # Honour the planner's pairs if given; otherwise connect every chosen table pairwise
    # so the result is a single joined set rather than a cartesian product.
    if wanted_pairs:
        pairs = [tuple(p) for p in wanted_pairs]
    else:
        ordered = sorted(tables)
        pairs = [(a, b) for i, a in enumerate(ordered) for b in ordered[i + 1:]]

    clauses: list[str] = []
    allowed_pairs: set[frozenset[str]] = set()
    used_tables: set[str] = set(tables)
    emitted: set[frozenset[str]] = set()

    for a, b in pairs:
        path = _shortest_path(graph, a, b)  # may traverse bridge tables
        for x, y in zip(path, path[1:]):
            key = frozenset((x, y))
            if key in emitted:
                continue
            rule = graph[x][y]
            clauses.append(f"{x} JOIN {y} ON ({rule})")
            allowed_pairs.add(key)
            emitted.add(key)
            used_tables.update((x, y))  # bridge tables enter the render set

    return clauses, allowed_pairs, used_tables
