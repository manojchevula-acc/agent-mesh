"""Payload-filter (`where`) semantics on the vector index — the enabler for
pre-filtered example retrieval (docs/PLAN_STRUCTURED_RETRIEVAL.md).

Convention under test (see VectorIndex.search docstring):
  scalar value      -> equality        {"intent": "policy_violation"}
  {"any": [...]}    -> list overlap    {"tables": {"any": ["margin_analysis"]}}

Only the in-process MemoryIndex is exercised (no server); QdrantIndex builds the
equivalent MatchAny/MatchValue filter from the same convention.
"""

import numpy as np

from sql_agent.semantic_layer.vector_index import MemoryIndex, _match


def _index():
    idx = MemoryIndex()
    names = ["ex-margin", "ex-customer", "ex-both"]
    vectors = np.eye(3)  # orthogonal unit vectors; query picks winners deterministically
    payloads = {
        "ex-margin": {"tables": ["margin_analysis"], "intent": "aggregation"},
        "ex-customer": {"tables": ["customer_master"], "intent": "ranking"},
        "ex-both": {"tables": ["margin_analysis", "customer_master"],
                    "intent": "policy_violation"},
    }
    idx.build(names, vectors, payloads)
    return idx


def test_no_filter_returns_everything():
    # Distinct per-row scores (1.0 / 0.5 / 0.25 against the identity vectors) so the
    # expected order is unambiguous — equal scores would tie-break arbitrarily.
    hits = _index().search(np.array([1.0, 0.5, 0.25]), k=3)
    assert [n for n, _ in hits] == ["ex-margin", "ex-customer", "ex-both"]


def test_any_of_list_overlap_filters():
    hits = _index().search(np.array([1.0, 1.0, 1.0]), k=3,
                           where={"tables": {"any": ["margin_analysis"]}})
    assert {n for n, _ in hits} == {"ex-margin", "ex-both"}


def test_any_of_no_overlap_returns_empty():
    hits = _index().search(np.array([1.0, 1.0, 1.0]), k=3,
                           where={"tables": {"any": ["treasury_rate_sheet"]}})
    assert hits == []


def test_scalar_condition_stays_equality():
    hits = _index().search(np.array([1.0, 1.0, 1.0]), k=3,
                           where={"intent": "policy_violation"})
    assert [n for n, _ in hits] == ["ex-both"]


def test_combined_conditions_are_anded():
    hits = _index().search(np.array([1.0, 1.0, 1.0]), k=3,
                           where={"tables": {"any": ["margin_analysis"]},
                                  "intent": "aggregation"})
    assert [n for n, _ in hits] == ["ex-margin"]


def test_match_helper_handles_scalar_payload_against_any():
    # A scalar payload value still matches an {"any": ...} condition containing it —
    # defensive, in case an older payload stored a single table as a string.
    assert _match({"tables": "margin_analysis"},
                  {"tables": {"any": ["margin_analysis", "x"]}})
    assert not _match({"tables": "customer_master"},
                      {"tables": {"any": ["margin_analysis"]}})
