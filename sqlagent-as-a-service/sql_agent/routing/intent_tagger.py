"""Lightweight RULE-BASED intent tagging for few-shot retrieval (Phase 6).

Distinct from ``routing/intent_classifier.py`` (an LLM call used upstream for
tier/domain routing, once per turn): this tagger has to run once per CANDIDATE example
during retrieval scoring, so it must be fast and deterministic — no model call, no
added latency. It also has to run identically on a curated example's text (at
metadata-generation time, see ``memory/example_metadata.py``) and on the live
question at query time, so the two never drift apart the way an LLM-labeled seed set
and a rule-labeled live question could.

Ordered keyword rules, most specific first; the first rule whose keywords match (in the
question OR the example's own SQL-derived pattern tags, when available) wins. Falls
back to "customer_analysis" — the most generic bucket in this banking-pricing domain —
when nothing matches. Never raises.
"""

from __future__ import annotations

import re

from sql_agent.memory.sql_pattern import classify_sql

INTENTS = (
    "policy_violation", "threshold", "trend", "ranking", "comparison", "aggregation",
    "risk_analysis", "profitability_analysis", "pricing_analysis", "customer_analysis",
)

_KEYWORDS: dict[str, tuple[str, ...]] = {
    "policy_violation": ("policy", "violat", "breach", "non.?compliant", "exception",
                         "compliant", "compliance"),
    # NOTE: bare "over"/"under" are deliberately excluded — too ambiguous on their own
    # (e.g. "over time" is a trend signal, not a threshold one); "exceed"/"below"/
    # "above"/named limits are unambiguous enough to use directly. "at least/at most"
    # and "more/fewer/less than" are quantity-threshold phrases ("at least four deals"
    # -> HAVING count >= 4), listed here so they win over ranking's superlatives.
    "threshold": ("below", "above", "exceed", "minimum", "maximum", "floor", "ceiling",
                  "threshold", "at least", "at most", "more than", "fewer than",
                  "less than", "or more", "or fewer"),
    "trend": ("trend", "over time", "monthly", "quarterly", "yearly", "month",
              "quarter", "growth", "declin"),
    # "most"/"least" are superlatives ONLY when not part of the threshold phrases
    # "at most"/"at least" — the lookbehind keeps "at least four deals" from reading
    # as a ranking question (the bare \bleast\b used to fire on it).
    "ranking": ("top", "highest", "lowest", "best", "worst", r"(?<!at\s)most",
                r"(?<!at\s)least", "bottom", "rank", "largest", "biggest",
                "greatest", "smallest", "fewest"),
    "comparison": ("compare", "versus", "vs", "difference between", "compared to"),
    "aggregation": ("average", "avg", "sum", "total", "count", "how many",
                    "number of"),
    "risk_analysis": ("risk", "rwa", "capital", "exposure", "credit rating", "gearing",
                      "leverage"),
    "profitability_analysis": ("profit", "profitability", "net margin", "roe"),
    "pricing_analysis": ("price", "pricing", "margin", "discount", "quote", "rate"),
    "customer_analysis": ("customer", "segment", "client", "relationship"),
}

# SQL-pattern tags (memory/sql_pattern.py) that corroborate an intent when the
# example's own SQL is available (seed examples; not the live question, which has none
# yet). Matched at the SAME priority tier as the keyword rule below.
_SQL_HINTS: dict[str, tuple[str, ...]] = {
    "policy_violation": ("policy_violation",),
    "threshold": ("threshold",),
    "trend": ("trend",),
    "ranking": ("ranking", "top_n", "bottom_n"),
    "comparison": ("comparison",),
    "aggregation": ("aggregation",),
}

_DEFAULT_INTENT = "customer_analysis"


def _matches(text: str, keywords: tuple[str, ...]) -> bool:
    """Whole-word/phrase match — every keyword is wrapped in ``\\b`` boundaries so a
    short word (e.g. "count") can't fire on a substring of an unrelated one (e.g.
    "disCOUNT"). A keyword already containing regex syntax (e.g. "non.?compliant")
    still gets boundary-anchored at its own start/end, which is exactly what's wanted."""
    return any(re.search(rf"\b{kw}\b", text) for kw in keywords)


def tag_intent(question: str, sql: str | None = None) -> str:
    """Rule-based intent for ``question`` (and optionally its ``sql``, when tagging a
    curated example rather than a live question). Never raises."""
    text = (question or "").lower()
    sql_patterns: set[str] = set()
    if sql:
        shape = classify_sql(sql)
        if shape:
            sql_patterns = set(shape["patterns"])

    for intent in INTENTS:
        if _matches(text, _KEYWORDS[intent]):
            return intent
        hints = _SQL_HINTS.get(intent, ())
        if hints and sql_patterns & set(hints):
            return intent
    return _DEFAULT_INTENT


def expected_patterns(question: str) -> set[str]:
    """SQL-pattern tags a LIVE question's wording implies, for the pattern-overlap
    retrieval factor (``memory/example_ranker.py``) — the live question has no SQL yet
    (that's what generation produces), so its expected patterns come from the same
    keyword rules as ``tag_intent`` instead of ``sql_pattern.classify_sql``. Unlike
    ``tag_intent`` (first match wins, one label), this collects EVERY matching bucket
    among the ones that double as ``sql_pattern`` tags, since a question can plausibly
    need more than one (e.g. "top 10 policy-violating deals" is both ranking AND
    policy_violation). Structural tags with no natural-language signal (join, cte,
    window_function, subquery, exists, case_when, top_n/bottom_n) are intentionally
    omitted — they describe HOW the SQL is written, not what the question asks."""
    text = (question or "").lower()
    return {intent for intent in _SQL_HINTS if _matches(text, _KEYWORDS[intent])}
