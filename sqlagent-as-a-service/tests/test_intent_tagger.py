"""Rule-based intent tagger (Phase 6) — must run identically on question text alone
(live question, no SQL yet) and on a curated example's question+SQL."""

from sql_agent.routing.intent_tagger import expected_patterns, tag_intent


def test_policy_violation_beats_generic_pricing_vocabulary():
    q = ("Which customer segment and risk band have the most deals priced below "
         "their policy minimum margin?")
    assert tag_intent(q) == "policy_violation"


def test_plain_average_is_aggregation_not_customer_analysis():
    """The task's other motivating example: superficially about "customer segment"
    and "margin" like the policy-violation question, but must land in a DIFFERENT
    bucket since it's a plain average, not a policy check."""
    assert tag_intent("What is the average margin by customer segment?") == "aggregation"


def test_ranking_keywords():
    assert tag_intent("Show me the top 10 customers by total deal volume.") == "ranking"


def test_risk_analysis_keywords():
    assert tag_intent("How much capital is tied up in our riskiest deals?") == "risk_analysis"


def test_discount_does_not_false_positive_on_count_substring():
    """Regression: "discount" contains "count" as a substring — the word-boundary fix
    in _matches must stop that from firing the aggregation rule."""
    assert tag_intent("Which customers require approval for their relationship discount?") \
        == "pricing_analysis"


def test_trend_not_confused_with_threshold_on_bare_over():
    """Regression: bare "over" (as in "over time") must not fire the threshold rule
    ahead of the more specific trend signal."""
    assert tag_intent("Show the monthly count and volume of won deals over time.") == "trend"


def test_default_fallback_for_unmatched_text():
    assert tag_intent("xyzzy plugh") == "customer_analysis"


def test_sql_hint_corroborates_intent_when_question_wording_is_ambiguous():
    """A vague question paired with SQL that structurally IS a policy-violation check
    (col-vs-col margin comparison) should still land on policy_violation via the SQL
    hint, even without an explicit "policy" keyword in the question."""
    q = "Show me the outliers by segment and risk band."
    sql = ("SELECT customer_segment, risk_category, COUNT(*) FROM t "
           "WHERE expected_margin_pct < policy_min_expected_margin_pct "
           "GROUP BY customer_segment, risk_category;")
    assert tag_intent(q, sql) == "policy_violation"


def test_expected_patterns_collects_multiple_buckets():
    patterns = expected_patterns("top 10 policy-violating deals by segment")
    assert "ranking" in patterns
    assert "policy_violation" in patterns


def test_expected_patterns_empty_for_unmatched_text():
    assert expected_patterns("xyzzy plugh") == set()


def test_largest_is_a_ranking_superlative():
    """Regression (live log 15:59): "largest won volume" produced patterns=
    ['aggregation'] only, because "largest" was missing from the ranking vocabulary —
    the retrieved examples then had no top-N signal to match on."""
    q = "For the product type with the largest won volume, what is its average expected margin?"
    assert tag_intent(q) == "ranking"
    assert {"ranking", "aggregation"} <= expected_patterns(q)


def test_at_least_is_threshold_not_ranking():
    """Regression (live log 16:05): the bare word "least" inside "at least four deals"
    fired the ranking rule (\\bleast\\b matches), masking the real threshold intent.
    The lookbehind + the "at least" threshold phrase must flip both signals."""
    q = "List customers who have booked at least four deals and show their total deal amount."
    assert tag_intent(q) == "threshold"
    patterns = expected_patterns(q)
    assert "threshold" in patterns
    assert "ranking" not in patterns


def test_bare_most_still_fires_ranking():
    """The lookbehind must ONLY suppress "most" inside "at most" — a genuine
    superlative ("most common ...") still ranks."""
    q = "What are the most common reasons deals breach policy?"
    assert "ranking" in expected_patterns(q)


def test_never_raises_on_empty_input():
    assert tag_intent("") == "customer_analysis"
    assert tag_intent(None) == "customer_analysis"
