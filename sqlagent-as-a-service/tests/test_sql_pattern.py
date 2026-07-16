"""Multi-tag SQL pattern classifier (Phase 5) — the fix for the task's motivating bug:
a plain aggregation ("average margin by segment") must NOT be tagged the same as a
policy-violation comparison ("deals below policy minimum margin"), even though both
questions share vocabulary (customer, segment, margin)."""

from sql_agent.memory.sql_pattern import classify_sql, shape_phrase, sql_pattern


def test_plain_average_is_aggregation_only():
    sql = ("SELECT customer_segment, ROUND(AVG(expected_margin_pct), 2) AS avg_margin "
           "FROM fab_semantic.pricing_recommendation_view GROUP BY customer_segment;")
    shape = classify_sql(sql)
    assert shape["patterns"] == ["aggregation"]
    assert "comparison" not in shape["patterns"]
    assert "policy_violation" not in shape["patterns"]


def test_column_vs_column_comparison_is_tagged_comparison_and_policy_violation():
    """The task's exact motivating example: comparing expected_margin_pct against the
    POLICY minimum, grouped by segment/risk band, must be distinguishable from a plain
    average — this is the whole point of the redesign."""
    sql = ("SELECT customer_segment, risk_category, COUNT(*) AS violations "
           "FROM fab_semantic.pricing_recommendation_view "
           "WHERE expected_margin_pct < policy_min_expected_margin_pct "
           "GROUP BY customer_segment, risk_category ORDER BY violations DESC;")
    shape = classify_sql(sql)
    assert "comparison" in shape["patterns"]
    assert "policy_violation" in shape["patterns"]
    assert "aggregation" in shape["patterns"]


def test_column_vs_literal_inequality_is_threshold_not_comparison():
    sql = "SELECT customer_id FROM fab_curated.customer_master WHERE annual_revenue_aed > 10000000;"
    shape = classify_sql(sql)
    assert "threshold" in shape["patterns"]
    assert "comparison" not in shape["patterns"]


def test_boolean_flag_filter_is_policy_violation_even_with_equality():
    """margin_below_minimum = 1 is an EQUALITY filter, not an inequality — policy_violation
    must still fire because the COLUMN NAME signals a policy check, not the operator."""
    sql = ("SELECT region, COUNT(*) FROM fab_semantic.margin_analysis "
           "WHERE margin_below_minimum = 1 GROUP BY region;")
    shape = classify_sql(sql)
    assert "policy_violation" in shape["patterns"]
    assert "threshold" not in shape["patterns"]  # EQ, not an inequality
    assert "comparison" not in shape["patterns"]  # col vs literal, not col vs col


def test_join_tag_and_join_extraction():
    sql = ("SELECT cm.region, COUNT(*) FROM fab_semantic.margin_analysis ma "
           "JOIN fab_curated.customer_master cm ON ma.customer_id = cm.customer_id "
           "GROUP BY cm.region;")
    shape = classify_sql(sql)
    assert "join" in shape["patterns"]
    assert shape["joins"] == ["margin_analysis.customer_id = customer_master.customer_id"]


def test_top_n_vs_bottom_n_by_sort_direction():
    top = classify_sql("SELECT a FROM t ORDER BY v DESC LIMIT 5;")
    bottom = classify_sql("SELECT a FROM t ORDER BY v ASC LIMIT 5;")
    assert "top_n" in top["patterns"] and "bottom_n" not in top["patterns"]
    assert "bottom_n" in bottom["patterns"] and "top_n" not in bottom["patterns"]


def test_structural_tags_window_cte_case_exists():
    sql = ("WITH ranked AS (SELECT a, ROW_NUMBER() OVER (ORDER BY a) AS rn FROM t) "
           "SELECT CASE WHEN rn = 1 THEN 'first' ELSE 'other' END AS bucket FROM ranked "
           "WHERE EXISTS (SELECT 1 FROM u WHERE u.id = ranked.a);")
    shape = classify_sql(sql)
    for tag in ("cte", "window_function", "case_when", "exists"):
        assert tag in shape["patterns"], f"missing {tag} in {shape['patterns']}"


def test_trend_requires_a_time_hint_group_column():
    sql = "SELECT deal_month, SUM(v) FROM t GROUP BY deal_month;"
    assert "trend" in classify_sql(sql)["patterns"]
    sql_no_time = "SELECT customer_segment, SUM(v) FROM t GROUP BY customer_segment;"
    assert "trend" not in classify_sql(sql_no_time)["patterns"]


def test_classify_sql_none_and_unparseable_never_raises():
    assert classify_sql(None) is None
    assert classify_sql("") is None
    assert classify_sql("not ( valid sql") is None


def test_sql_pattern_primary_bucket_prioritises_policy_violation_over_aggregation():
    sql = ("SELECT customer_segment, COUNT(*) FROM fab_semantic.margin_analysis "
           "WHERE margin_below_minimum = 1 GROUP BY customer_segment;")
    assert sql_pattern(sql) == "policy_violation"


def test_sql_pattern_lookup_fallback():
    assert sql_pattern("SELECT a, b FROM t WHERE a = 1;") == "lookup"


def test_shape_phrase_includes_patterns_and_columns():
    sql = "SELECT customer_segment FROM t WHERE customer_segment = 'SME' ORDER BY a DESC LIMIT 10;"
    phrase = shape_phrase(sql)
    assert "ranking" in phrase
    assert "customer_segment" in phrase
