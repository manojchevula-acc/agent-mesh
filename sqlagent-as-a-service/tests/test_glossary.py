"""Business glossary module (Phase 7) — YAML-backed term -> column mapping."""

from sql_agent.semantic_layer.catalog import glossary_expand as catalog_glossary_expand
from sql_agent.semantic_layer.glossary import glossary_expand, matched_terms, render_glossary_block


def test_glossary_expand_appends_mapped_columns():
    out = glossary_expand("What is the policy margin for SME customers?")
    assert "min_expected_margin_pct" in out
    assert "policy_min_expected_margin_pct" in out
    assert "What is the policy margin for SME customers?" in out  # original text kept


def test_glossary_expand_is_noop_for_unknown_terms():
    assert glossary_expand("banana") == "banana"


def test_catalog_reexports_glossary_expand_unchanged():
    """sql_agent/semantic_layer/catalog.py must stay a drop-in for existing callers
    (selector.py, memory/example_index.py) after the glossary module extraction."""
    q = "What is the risk band for this customer?"
    assert catalog_glossary_expand(q) == glossary_expand(q)


def test_matched_terms_returns_canonical_terms_not_columns():
    terms = matched_terms("Which deals are below their policy minimum margin?")
    assert "policy minimum" in terms
    assert "min_expected_margin_pct" not in terms  # canonical term, not the column


def test_matched_terms_prefers_longer_term_first():
    """"policy margin" should be reported once, not also duplicated via "margin"."""
    terms = matched_terms("what is the policy margin here?")
    assert terms.count("policy margin") == 1


def test_matched_terms_empty_for_no_hits():
    assert matched_terms("xyzzy plugh") == []


def test_render_glossary_block_restricts_to_given_terms():
    block = render_glossary_block(["risk band"])
    assert "risk band" in block
    assert "risk_category" in block
    assert "customer segment" not in block


def test_render_glossary_block_empty_for_no_terms():
    assert render_glossary_block([]) == ""
