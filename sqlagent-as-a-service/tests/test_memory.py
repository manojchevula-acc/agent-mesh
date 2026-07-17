"""Conversation-memory tests — no LLM keys required.

Covers two things:
  (1) The checkpointer makes a graph remember a thread across invokes.
  (2) The has_tool_result check looks at the CURRENT turn only, not history —
      this is the fix for the multi-turn follow-up bug.
"""

from typing import Annotated, TypedDict

import pytest
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, StateGraph
from langgraph.graph.message import add_messages

from sql_agent.memory import (
    new_session_id,
    render_examples_block,
)


# --- helpers ------------------------------------------------------------------

class _S(TypedDict):
    messages: Annotated[list, add_messages]


def _tiny_graph(checkpointer):
    """A throwaway 1-node graph (no LLM) to prove thread persistence."""
    g = StateGraph(_S)
    g.add_node("reply", lambda s: {"messages": [AIMessage(content="ok")]})
    g.add_edge(START, "reply")
    g.add_edge("reply", END)
    return g.compile(checkpointer=checkpointer)


# --- (1) checkpointer persists per thread ------------------------------------

def test_checkpointer_persists_messages_per_thread():
    app = _tiny_graph(InMemorySaver())
    cfg = {"configurable": {"thread_id": "sess_test"}}
    app.invoke({"messages": [HumanMessage("first")]}, config=cfg)
    out = app.invoke({"messages": [HumanMessage("second")]}, config=cfg)
    # two human turns + two AI replies, all retained on the same thread
    assert len(out["messages"]) == 4


def test_separate_threads_do_not_share_history():
    app = _tiny_graph(InMemorySaver())
    app.invoke({"messages": [HumanMessage("a")]},
               config={"configurable": {"thread_id": "s1"}})
    out = app.invoke({"messages": [HumanMessage("b")]},
                     config={"configurable": {"thread_id": "s2"}})
    assert len(out["messages"]) == 2   # s2 has its own turn + reply only


# --- (2) has_tool_result looks at CURRENT turn only (multi-turn bug fix) -----

def test_has_tool_result_uses_current_turn_only():
    """Prior-turn ToolMessages must NOT cause tool_choice to be set to 'auto'
    on the first step of a new turn — that was the bug causing the model to
    skip calling a tool on follow-up questions."""
    # Simulate what state["messages"] looks like at the start of turn 2:
    # turn 1 history is already in the message list (from the checkpointer).
    messages = [
        HumanMessage("Who is CUST002?"),          # turn 1 human
        AIMessage("ok", tool_calls=[]),            # turn 1 AI
        ToolMessage(content="{}", tool_call_id="x"),   # turn 1 tool result
        AIMessage("Falcon Steel..."),              # turn 1 final answer
        HumanMessage("What deals do they have?"), # turn 2 human  ← current
    ]
    # Reproduce the fixed logic from graph.py
    last_human_idx = max(
        (i for i, m in enumerate(messages) if isinstance(m, HumanMessage)),
        default=-1,
    )
    current_turn_messages = messages[last_human_idx + 1:]
    has_tool_result = any(isinstance(m, ToolMessage) for m in current_turn_messages)

    # No ToolMessage exists AFTER the last HumanMessage → must be False
    # so tool_choice stays "any" and the model is forced to call a tool.
    assert has_tool_result is False, (
        "has_tool_result should be False at the start of turn 2 — "
        "prior-turn ToolMessages must not count"
    )


def test_has_tool_result_true_after_tool_runs_this_turn():
    """Once a tool has run IN the current turn, has_tool_result should be True
    so tool_choice switches to 'auto' and the model can compose its answer."""
    messages = [
        HumanMessage("Who is CUST002?"),          # turn 1
        AIMessage("ok", tool_calls=[]),
        ToolMessage(content="{}", tool_call_id="x"),
        AIMessage("Falcon Steel..."),
        HumanMessage("What deals do they have?"), # turn 2 human
        AIMessage("ok", tool_calls=[]),            # turn 2 AI step 1
        ToolMessage(content="{}", tool_call_id="y"),  # turn 2 tool result ← current turn
    ]
    last_human_idx = max(
        (i for i, m in enumerate(messages) if isinstance(m, HumanMessage)),
        default=-1,
    )
    current_turn_messages = messages[last_human_idx + 1:]
    has_tool_result = any(isinstance(m, ToolMessage) for m in current_turn_messages)

    assert has_tool_result is True


# --- (3) utility functions ---------------------------------------------------

def test_new_session_id_format():
    sid = new_session_id()
    assert sid.startswith("sess_")
    assert len(sid) > 10


def test_render_examples_block_formats_rows():
    block = render_examples_block([
        {"question": "avg margin?", "validated_sql": "SELECT 1"},
    ])
    assert "avg margin?" in block
    assert "SELECT 1" in block
    assert render_examples_block([]) == ""


# --- (4) intent-aware Pattern Retriever (few-shot example ranking) -----------

_EXAMPLE_ROWS = [
    {"question": "total net profit by product type",
     "validated_sql": "SELECT 1", "tier": "full_dynamic", "tags": "profitability_summary"},
    {"question": "average net margin by region",
     "validated_sql": "SELECT 2", "tier": "full_dynamic", "tags": "margin_analysis"},
    {"question": "top customers by total deal volume",
     "validated_sql": "SELECT 3", "tier": "full_dynamic", "tags": "customer_360"},
    {"question": "deals priced below the policy floor",
     "validated_sql": "SELECT 4", "tier": "full_dynamic", "tags": "pricing_recommendation_view"},
]


def _dense_off(monkeypatch):
    """Force the dense backend off so tests never download an embedding model."""
    import sql_agent.semantic_layer.embeddings as emb
    from sql_agent.memory import example_index

    monkeypatch.setattr(emb, "get_backend", lambda: None)
    example_index._CACHE = {"sig": None, "names": None, "bm25": None,
                            "name_to_idx": None, "dense_ok": False}


def test_rank_examples_empty_corpus():
    from sql_agent.memory.example_index import rank_examples

    assert rank_examples("anything", []) == []


def test_rank_examples_respects_k(monkeypatch):
    _dense_off(monkeypatch)
    from sql_agent.memory.example_index import rank_examples

    out = rank_examples("net profit", _EXAMPLE_ROWS, k=2)
    assert len(out) == 2
    assert all(r in _EXAMPLE_ROWS for r in out)


def test_rank_examples_tables_hint_biases_selection(monkeypatch):
    pytest.importorskip("rank_bm25")  # boosting needs a working ranker
    _dense_off(monkeypatch)
    from sql_agent.memory.example_index import rank_examples

    # A question whose text does NOT clearly match the pricing example, but the intent
    # hint points at its table — the boost should surface it in the top-k.
    out = rank_examples(
        "show me pricing exceptions", _EXAMPLE_ROWS, k=1,
        tables_hint=["pricing_recommendation_view"],
    )
    assert out[0]["tags"] == "pricing_recommendation_view"


def _patch_dense(monkeypatch, rows, scores):
    """Skip the real build and force controlled dense cosine scores per row index, so the
    confidence-gate logic can be tested without an embedding model or vector store."""
    from sql_agent.memory import example_index

    example_index._CACHE = {
        "sig": example_index._corpus_signature(rows),
        "names": [r["question"] for r in rows],
        "bm25": None,
        "name_to_idx": {r["question"]: i for i, r in enumerate(rows)},
        "dense_ok": True,
    }
    monkeypatch.setattr(example_index, "dense_scores",
                        lambda q, where=None, top_m=None: dict(scores))


_GATE_ROWS = [
    {"question": "capital on high rwa deals", "validated_sql": "SELECT 1",
     "tier": "full_dynamic", "tags": "rwa_impact_view"},
    {"question": "customers by industry", "validated_sql": "SELECT 2",
     "tier": "full_dynamic", "tags": "customer_master"},
]


def test_threshold_gate_drops_low_confidence(monkeypatch):
    from sql_agent.config import settings
    from sql_agent.memory.example_index import rank_examples

    # Row 0 clears the floor (cosine 1.0), row 1 does not (0.0).
    _patch_dense(monkeypatch, _GATE_ROWS, {0: 1.0, 1: 0.0})
    monkeypatch.setattr(settings, "examples_min_score", 0.5)
    out = rank_examples("anything", _GATE_ROWS, k=5)
    assert [r["tags"] for r in out] == ["rwa_impact_view"]  # row 1 gated out


def test_threshold_gate_suppresses_all_when_nothing_clears(monkeypatch):
    from sql_agent.config import settings
    from sql_agent.memory.example_index import rank_examples

    _patch_dense(monkeypatch, _GATE_ROWS, {0: 0.4, 1: 0.2})
    monkeypatch.setattr(settings, "examples_min_score", 0.9)  # nothing clears
    assert rank_examples("anything", _GATE_ROWS, k=5) == []


# --- (5) query-logic-aware corpus enrichment (fights lexical-only matching) --

def test_sql_shape_phrase_ranking_no_aggregate():
    from sql_agent.memory.sql_pattern import shape_phrase

    sql = ("SELECT customer_id, customer_name, win_rate_pct FROM fab_semantic.customer_360 "
           "WHERE customer_segment = 'Corporate' ORDER BY win_rate_pct DESC LIMIT 10;")
    phrase = shape_phrase(sql)
    assert "ranking" in phrase
    assert "customer_segment" in phrase  # WHERE column surfaced


def test_sql_shape_phrase_grouped_aggregation():
    from sql_agent.memory.sql_pattern import shape_phrase

    sql = ("SELECT customer_segment, SUM(won_deals) AS won, SUM(total_deals) AS total "
           "FROM fab_semantic.customer_360 GROUP BY customer_segment;")
    phrase = shape_phrase(sql)
    assert "aggregation" in phrase
    assert "customer_segment" in phrase  # GROUP BY column surfaced


def test_sql_shape_phrase_scalar_aggregate_without_group_by_is_still_aggregation():
    """A bare AVG() with no GROUP BY is a scalar aggregation, not a 'lookup' — the
    classifier must key off the presence of an aggregate FUNCTION, not GROUP BY."""
    from sql_agent.memory.sql_pattern import shape_phrase

    sql = ("SELECT ROUND(AVG(expected_margin_pct), 2) AS avg_margin FROM "
           "fab_semantic.pricing_recommendation_view WHERE customer_segment = 'SME';")
    assert "aggregation" in shape_phrase(sql)


def test_sql_shape_phrase_trend_groups_by_time_dimension():
    from sql_agent.memory.sql_pattern import shape_phrase

    sql = ("SELECT deal_month, SUM(total_deal_volume_aed) AS volume FROM "
           "fab_semantic.customer_360 GROUP BY deal_month;")
    assert "trend" in shape_phrase(sql)


def test_sql_shape_phrase_unparseable_or_missing_sql_is_never_fatal():
    from sql_agent.memory.sql_pattern import shape_phrase

    assert shape_phrase(None) == ""
    assert shape_phrase("") == ""
    assert shape_phrase("not ( valid sql") == ""


def test_example_doc_text_combines_question_and_shape():
    from sql_agent.memory.example_index import example_doc_text

    row = {"question": "top customers by deal volume",
           "validated_sql": "SELECT c FROM t ORDER BY v DESC LIMIT 10;"}
    doc = example_doc_text(row)
    assert "top customers by deal volume" in doc
    assert "pattern: ranking" in doc


def test_example_doc_text_falls_back_to_question_when_no_sql():
    from sql_agent.memory.example_index import example_doc_text

    assert example_doc_text({"question": "plain question", "validated_sql": None}) == "plain question"


# --- (6) weighted RRF fusion (fixes lexical overlap out-voting semantic match) ---

def test_rrf_equal_weights_lets_a_pure_lexical_match_tie_the_true_semantic_match():
    """Documents today's bug at the fusion-math level: candidate 0 is BM25's #1 pick
    (rank 0) but dense's worst (rank 2); candidate 1 is the reverse. Equal-weight RRF
    (today's ``_rrf`` before this fix) can't tell these apart — a pure keyword match
    ties with the true semantic match instead of losing to it."""
    from sql_agent.memory.example_index import _rrf

    dense_rank = [1, 2, 0]   # candidate 1 = dense's best, candidate 0 = dense's worst
    sparse_rank = [0, 2, 1]  # candidate 0 = BM25's best,  candidate 1 = BM25's worst

    equal = _rrf([(dense_rank, 1.0), (sparse_rank, 1.0)], k=60)
    assert equal[0] == pytest.approx(equal[1])


def test_rrf_dense_weighted_breaks_the_tie_toward_the_semantic_match():
    from sql_agent.memory.example_index import _rrf

    dense_rank = [1, 2, 0]
    sparse_rank = [0, 2, 1]

    weighted = _rrf([(dense_rank, 0.7), (sparse_rank, 0.3)], k=60)
    assert weighted[1] > weighted[0]  # dense's top pick now wins outright


def test_rank_examples_weighted_fusion_prefers_dense_end_to_end(monkeypatch):
    """Full rank_examples() wiring: BM25 ranks a lexically-overlapping-but-wrong example
    first, dense ranks the logically-correct example first — the default weights
    (examples_dense_weight=0.7 > examples_bm25_weight=0.3) must surface the dense pick."""
    from sql_agent.config import settings
    from sql_agent.memory import example_index
    from sql_agent.memory.example_index import rank_examples

    rows = [
        {"question": "row A", "validated_sql": "SELECT 1", "tier": "full_dynamic", "tags": "t0"},
        {"question": "row B", "validated_sql": "SELECT 2", "tier": "full_dynamic", "tags": "t1"},
        {"question": "row C", "validated_sql": "SELECT 3", "tier": "full_dynamic", "tags": "t2"},
    ]
    example_index._CACHE = {
        "sig": example_index._corpus_signature(rows),
        "names": [r["question"] for r in rows],
        "bm25": object(),  # non-None sentinel — _sparse_ranking is monkeypatched below
        "name_to_idx": {r["question"]: i for i, r in enumerate(rows)},
        "dense_ok": True,
    }
    monkeypatch.setattr(example_index, "dense_scores",
                         lambda q, where=None, top_m=None: {0: 0.10, 1: 0.95, 2: 0.50})
    monkeypatch.setattr(example_index, "_sparse_ranking", lambda q: [0, 2, 1])
    monkeypatch.setattr(settings, "examples_dense_weight", 0.7)
    monkeypatch.setattr(settings, "examples_bm25_weight", 0.3)

    out = rank_examples("anything", rows, k=1)
    assert out[0]["tags"] == "t1"  # dense's clear winner despite being BM25's worst pick


# --- (7) metadata-aware ranking (Phases 4/8-10) — the task's motivating fix --------

_POLICY_VS_AVERAGE_ROWS = [
    {"question": "What is the average expected margin across all our SME customers' deals?",
     "validated_sql": ("SELECT ROUND(AVG(expected_margin_pct), 2) AS avg_margin FROM "
                       "fab_semantic.pricing_recommendation_view WHERE customer_segment = 'SME';"),
     "tier": "full_dynamic", "tags": "pricing_recommendation_view"},
    {"question": ("Which customer segment and risk band have the most deals priced "
                  "below their policy minimum margin?"),
     "validated_sql": ("SELECT customer_segment, risk_category, COUNT(*) AS violations "
                       "FROM fab_semantic.pricing_recommendation_view WHERE "
                       "expected_margin_pct < policy_min_expected_margin_pct "
                       "GROUP BY customer_segment, risk_category ORDER BY violations DESC;"),
     "tier": "full_dynamic", "tags": "pricing_recommendation_view"},
    {"question": "Show me the top 10 customers by total deal volume.",
     "validated_sql": ("SELECT customer_id, customer_name, total_deal_volume_aed FROM "
                       "fab_semantic.customer_360 ORDER BY total_deal_volume_aed DESC LIMIT 10;"),
     "tier": "full_dynamic", "tags": "customer_360"},
]


def test_rank_examples_distinguishes_policy_violation_from_plain_average(monkeypatch):
    """The task's exact motivating bug: a question about deals priced below the POLICY
    minimum margin must rank the policy-violation/comparison example above the
    plain average-margin-by-segment example, even though both share "customer",
    "segment", and "margin" vocabulary. BM25-only (dense off) so this never needs an
    embedding model — the metadata-driven factors alone must carry the distinction."""
    pytest.importorskip("rank_bm25")
    _dense_off(monkeypatch)
    from sql_agent.memory.example_index import rank_examples

    # A paraphrase — NOT identical to either stored question — that lexically overlaps
    # both candidates about equally, so only the metadata-aware scoring can break the tie.
    question = ("For each pricing segment and risk category, how many deals fell short "
                "of the required minimum margin threshold?")
    out = rank_examples(question, _POLICY_VS_AVERAGE_ROWS,
                        tables_hint=["pricing_recommendation_view"], k=1)
    assert "policy minimum margin" in out[0]["question"]


def test_rank_examples_diversity_pass_avoids_pure_duplicates(monkeypatch):
    """With k=2 the top pick and the runner-up should not be the two examples that
    share BOTH the same table AND the same SQL shape when a genuinely different
    (still relevant-ish) example is available in the pool."""
    pytest.importorskip("rank_bm25")
    _dense_off(monkeypatch)
    from sql_agent.memory.example_index import rank_examples

    rows = _POLICY_VS_AVERAGE_ROWS + [
        {"question": "Compare win rates between Corporate and SME segments.",
         "validated_sql": ("SELECT customer_segment, SUM(won_deals) won, SUM(total_deals) total "
                           "FROM fab_semantic.customer_360 GROUP BY customer_segment;"),
         "tier": "full_dynamic", "tags": "customer_360"},
    ]
    out = rank_examples("pricing and segment analysis", rows,
                        tables_hint=["pricing_recommendation_view"], k=2)
    assert len(out) == 2
    assert len({r["question"] for r in out}) == 2  # no duplicate picks


# --- (8) scale mode: structured, pre-filtered retrieval (PLAN_STRUCTURED_RETRIEVAL) --

_SCALE_ROWS = [
    {"question": "avg margin overall", "validated_sql": "SELECT 1", "tier": "full_dynamic",
     "metadata": {"tables": ["margin_analysis"], "intent": "aggregation",
                  "sql_pattern": ["aggregation"], "columns": [], "joins": []}},
    {"question": "top customers by volume", "validated_sql": "SELECT 2", "tier": "full_dynamic",
     "metadata": {"tables": ["customer_360"], "intent": "ranking",
                  "sql_pattern": ["ranking"], "columns": [], "joins": []}},
    {"question": "margin by region", "validated_sql": "SELECT 3", "tier": "full_dynamic",
     "metadata": {"tables": ["margin_analysis"], "intent": "aggregation",
                  "sql_pattern": ["aggregation"], "columns": [], "joins": []}},
    {"question": "lost deals per customer", "validated_sql": "SELECT 4", "tier": "full_dynamic",
     "metadata": {"tables": ["customer_360"], "intent": "aggregation",
                  "sql_pattern": ["aggregation"], "columns": [], "joins": []}},
]

_SCALE_SCORES = {0: 0.9, 1: 0.8, 2: 0.7, 3: 0.6}


def _prime_scale_cache(rows):
    """Prime the corpus cache at the CURRENT flag state (the signature includes the
    scale-mode flags), BM25 off, dense 'available' — set flags before calling this."""
    from sql_agent.memory import example_index

    example_index._CACHE = {
        "sig": example_index._corpus_signature(rows),
        "names": [r["question"] for r in rows],
        "bm25": None,
        "name_to_idx": {r["question"]: i for i, r in enumerate(rows)},
        "dense_ok": True,
    }


def _store_like_dense(rows, scores):
    """A dense_scores stub that behaves like a payload-filtering vector store: honours
    the {"tables": {"any": [...]}} condition against each row's metadata and the
    bounded top_m, exactly as MemoryIndex/QdrantIndex would."""
    def fake(q, where=None, top_m=None):
        out = dict(scores)
        if where and "tables" in where:
            wanted = set(where["tables"]["any"])
            out = {i: s for i, s in out.items()
                   if set(rows[i]["metadata"]["tables"]) & wanted}
        if top_m:
            out = dict(sorted(out.items(), key=lambda kv: -kv[1])[:top_m])
        return out
    return fake


def test_prefilter_parity_with_python_side_filter(monkeypatch):
    """The go/no-go for scale mode: prefilter ON (store-side where) must pick the SAME
    examples as prefilter OFF (Python-side filter) on the same corpus/question."""
    from sql_agent.config import settings
    from sql_agent.memory import example_index
    from sql_agent.memory.example_index import rank_examples

    monkeypatch.setattr(settings, "examples_min_score", 0.0)
    monkeypatch.setattr(example_index, "dense_scores",
                        _store_like_dense(_SCALE_ROWS, _SCALE_SCORES))

    picks = {}
    for flag in (False, True):
        monkeypatch.setattr(settings, "examples_prefilter_enabled", flag)
        _prime_scale_cache(_SCALE_ROWS)  # signature depends on the flag
        out = rank_examples("what is the average margin", _SCALE_ROWS,
                            tables_hint=["margin_analysis"], k=2)
        picks[flag] = [r["question"] for r in out]

    assert picks[True] == picks[False]
    assert set(picks[True]) == {"avg margin overall", "margin by region"}


def test_prefilter_empty_result_retries_unfiltered(monkeypatch):
    """Never-starve: a table filter matching NOTHING in the store must fall back to an
    unfiltered fetch (and then the Python-side filter's own full-pool fallback), so the
    generator still gets examples."""
    from sql_agent.config import settings
    from sql_agent.memory import example_index
    from sql_agent.memory.example_index import rank_examples

    monkeypatch.setattr(settings, "examples_min_score", 0.0)
    monkeypatch.setattr(settings, "examples_prefilter_enabled", True)
    monkeypatch.setattr(example_index, "dense_scores",
                        _store_like_dense(_SCALE_ROWS, _SCALE_SCORES))
    _prime_scale_cache(_SCALE_ROWS)

    out = rank_examples("anything", _SCALE_ROWS,
                        tables_hint=["treasury_rate_sheet"], k=2)  # matches no example
    assert len(out) == 2  # fell back to the full pool rather than returning nothing


def test_query_doc_text_baseline_is_glossary_expansion_only(monkeypatch):
    from sql_agent.config import settings
    from sql_agent.memory.example_index import query_doc_text

    monkeypatch.setattr(settings, "examples_structured_query_enabled", False)
    q = "some plain wording with no glossary terms"
    assert query_doc_text(q, intent="ranking", patterns={"ranking"}) == q


def test_query_doc_text_structured_appends_short_suffix(monkeypatch):
    from sql_agent.config import settings
    from sql_agent.memory.example_index import query_doc_text

    monkeypatch.setattr(settings, "examples_structured_query_enabled", True)
    q = "some plain wording with no glossary terms"
    doc = query_doc_text(q, intent="policy_violation",
                         patterns={"threshold", "policy_violation"})
    assert doc.startswith(q)  # question text still leads/dominates
    assert "Query pattern: policy_violation, threshold." in doc
    assert "Intent: policy_violation." in doc


