"""Few-shot example metadata generation utility (Phase 3) — combines the sql_pattern /
glossary / intent_tagger classifiers into the full stored metadata schema."""

from sql_agent.memory.example_metadata import generate


def test_generate_full_schema_for_policy_violation_example():
    question = ("Which customer segment and risk band have the most deals priced "
                "below their policy minimum margin?")
    sql = ("SELECT customer_segment, risk_category, COUNT(*) AS violations "
           "FROM fab_semantic.pricing_recommendation_view "
           "WHERE expected_margin_pct < policy_min_expected_margin_pct "
           "GROUP BY customer_segment, risk_category ORDER BY violations DESC;")
    meta = generate(question, sql)

    assert set(meta) == {"tables", "columns", "joins", "intent", "sql_pattern",
                         "aggregations", "filters", "business_terms", "complexity"}
    assert "pricing_recommendation_view" in meta["tables"]
    assert "expected_margin_pct" in meta["columns"]
    assert meta["intent"] == "policy_violation"
    assert "policy_violation" in meta["sql_pattern"]
    assert "comparison" in meta["sql_pattern"]
    assert "COUNT" in meta["aggregations"]
    assert "policy minimum" in meta["business_terms"]


def test_generate_distinguishes_plain_average_from_policy_violation():
    """The two motivating examples from the task brief must land on DIFFERENT
    sql_pattern/intent metadata despite sharing "customer segment"/"margin" wording."""
    avg_meta = generate(
        "What is the average margin by customer segment?",
        "SELECT customer_segment, AVG(expected_margin_pct) FROM t GROUP BY customer_segment;",
    )
    policy_meta = generate(
        "Which customer segment and risk band have the most deals below policy minimum margin?",
        "SELECT customer_segment, risk_category, COUNT(*) FROM t "
        "WHERE expected_margin_pct < policy_min_expected_margin_pct "
        "GROUP BY customer_segment, risk_category;",
    )
    assert avg_meta["intent"] != policy_meta["intent"]
    assert set(avg_meta["sql_pattern"]).isdisjoint({"policy_violation", "comparison"})
    assert "policy_violation" in policy_meta["sql_pattern"]


def test_generate_degrades_gracefully_with_no_sql():
    """A live question has no SQL yet — generate() must still produce intent and
    business_terms from the question text alone, with everything SQL-derived empty."""
    meta = generate("What is the policy margin for SME customers?", None)
    assert meta["tables"] == []
    assert meta["columns"] == []
    assert meta["sql_pattern"] == []
    assert meta["intent"] != ""
    assert "policy margin" in meta["business_terms"]
    assert meta["complexity"] == "low"


def test_complexity_scales_with_structural_richness():
    simple = generate("q", "SELECT a FROM t;")
    complex_sql = generate(
        "q",
        "WITH x AS (SELECT a FROM t1 JOIN t2 ON t1.id = t2.id) "
        "SELECT a, COUNT(*), SUM(b) FROM x GROUP BY a;",
    )
    order = {"low": 0, "medium": 1, "high": 2}
    assert order[complex_sql["complexity"]] > order[simple["complexity"]]
