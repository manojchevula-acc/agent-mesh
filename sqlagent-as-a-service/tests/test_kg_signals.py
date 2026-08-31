"""Fused ranking: every signal contributes, none gates, and the cut keeps what matters.

This is the regression guard for the design's central retrieval decision. An earlier draft
ran a first-match-wins ladder with regex first; regex matches no glossary term at all on 46%
of gold_dynamic questions, so as a gate it would drop the right table for nearly half of all
traffic.
"""

import pytest

from sql_agent.config import settings
from sql_agent.kg import retrieval
from tests.kg_fixtures import build_client


@pytest.fixture(autouse=True)
def _kg_on(monkeypatch):
    client = build_client()
    monkeypatch.setattr(retrieval, "get_kg_client", lambda: client)
    monkeypatch.setattr(settings, "kg_enabled", True)
    monkeypatch.setattr(settings, "kg_retrieval_strategy", "auto")
    monkeypatch.setattr(settings, "kg_candidate_top_k", 12)
    # Offline + deterministic: no embedding model, no vector store, no selector ranking.
    monkeypatch.setattr(retrieval, "_node_hits", lambda q: {})
    monkeypatch.setattr(retrieval, "_ranked_tables", lambda q, h: [])
    retrieval._literal_maps.cache_clear()
    retrieval._lexical_triggers.cache_clear()
    yield
    retrieval._literal_maps.cache_clear()
    retrieval._lexical_triggers.cache_clear()


def _semantic(monkeypatch, hits: dict):
    """Stub the embedding path with fixed prefixed hits."""
    monkeypatch.setattr(retrieval, "_node_hits", lambda q: hits)


# --- no signal is a gate -------------------------------------------------------------


def test_exact_literal_finds_a_table_the_semantic_path_missed():
    result = retrieval.lookup("How many deals have a product_type of Loan?")
    assert "product_master" in result.tables
    assert retrieval.SIGNAL_EXACT in result.attribution["product_master"]


def test_paraphrase_with_zero_regex_hits_still_resolves(monkeypatch):
    """The objection that killed the first-match-wins ladder: no glossary term appears
    verbatim, so the semantic path must carry it alone."""
    _semantic(monkeypatch, {"term::gearing": 0.63})
    result = retrieval.lookup("which customers are the most levered right now")
    assert "gearing" in result.terms
    assert "customer_master.debt_to_equity_ratio" in result.columns
    assert retrieval.SIGNAL_SEMANTIC in result.attribution["customer_master"]


def test_lexical_scenario_match_finds_a_table_with_no_column_or_term_hit():
    """S4 — the strongest measured signal. "deals" reaches historical_deals through the
    table NAME alone, with no glossary term and no column name in the question."""
    result = retrieval.lookup("how many deals were booked each quarter")
    assert "historical_deals" in result.tables
    assert retrieval.SIGNAL_LEXICAL in result.attribution["historical_deals"]


def test_no_signal_can_exclude_what_another_found(monkeypatch):
    _semantic(monkeypatch, {"term::policy minimum": 0.52})
    result = retrieval.lookup(
        "policy minimum for a Corporate customer with product_type Loan")
    assert {"pricing_policy", "customer_master", "product_master"} <= set(result.tables)


def test_template_and_term_signals_compose():
    """A template says which TABLES an entity question spans; it says nothing about which
    column a business term means. Both must run."""
    result = retrieval.lookup("what is the policy minimum for CUST002")
    assert result.template == "get_customer_metadata"
    assert result.params == {"customer_id": "CUST002"}
    assert "policy minimum" in result.terms
    assert any(retrieval.SIGNAL_TEMPLATE in sigs
               for sigs in result.attribution.values())


# --- fusion and the cut ----------------------------------------------------------------


def test_results_are_ordered_by_fused_score():
    result = retrieval.lookup("margin analysis for deals below the policy minimum")
    scores = [result.scores[t] for t in result.tables if t in result.scores]
    assert scores == sorted(scores, reverse=True)


def test_lexical_outranks_a_bare_coverage_hit():
    """Weighting sanity: a table matched by NAME/purpose should outrank one that merely
    shares a common column, which is what the measured weights encode."""
    result = retrieval.lookup("how many deals were booked each quarter")
    assert result.tables[0] == "historical_deals"


def test_top_k_cut_is_applied(monkeypatch):
    monkeypatch.setattr(settings, "kg_candidate_top_k", 2)
    monkeypatch.setattr(settings, "kg_join_closure_enabled", False)
    result = retrieval.lookup(
        "policy minimum margin for Corporate customers by risk band")
    assert len(result.tables) <= 2


def test_zero_top_k_means_uncapped(monkeypatch):
    monkeypatch.setattr(settings, "kg_join_closure_enabled", False)
    monkeypatch.setattr(settings, "kg_candidate_top_k", 2)
    capped = retrieval.lookup("policy minimum margin for Corporate customers by risk band")
    monkeypatch.setattr(settings, "kg_candidate_top_k", 0)
    uncapped = retrieval.lookup("policy minimum margin for Corporate customers by risk band")
    assert len(uncapped.tables) > len(capped.tables)


def test_closure_can_add_tables_beyond_the_cut(monkeypatch):
    """The cut bounds the SCORED candidates; join closure and bridge tables are added
    afterwards because a join the plan needs must never be missing from the render set."""
    monkeypatch.setattr(settings, "kg_candidate_top_k", 1)
    monkeypatch.setattr(settings, "kg_join_closure_enabled", True)
    result = retrieval.lookup("policy minimum margin by customer segment")
    assert len(result.tables) > 1
    assert any(retrieval.SIGNAL_CLOSURE in sigs for sigs in result.attribution.values())


# --- degradation -----------------------------------------------------------------------


def test_lookup_is_empty_when_the_graph_is_unavailable(monkeypatch):
    monkeypatch.setattr(retrieval, "get_kg_client", lambda: None)
    assert retrieval.lookup("anything at all").is_empty


def test_lookup_survives_an_internal_failure(monkeypatch):
    """Grounding must never break the turn — a KG that can fail a banking query is worse
    than no KG at all."""
    def boom(*_a, **_k):
        raise RuntimeError("index corrupt")

    monkeypatch.setattr(retrieval, "_node_hits", boom)
    assert retrieval.lookup("average margin by segment").is_empty


def test_a_failing_s5_ranking_does_not_fail_the_lookup(monkeypatch):
    """S5 is one signal among five; selector being unavailable must degrade, not abort."""
    def boom(*_a, **_k):
        raise RuntimeError("selector unavailable")

    monkeypatch.setattr("sql_agent.semantic_layer.selector.ranked_core", boom)
    result = retrieval.lookup("how many deals were booked each quarter")
    assert "historical_deals" in result.tables      # S4 still carries it
