"""DataAgent tool selection evaluator for FAB AgentMesh.

Validates that DataAgent called the appropriate MCP SQL-view tool
for the given query type, based on audit trail records.
"""
from __future__ import annotations
import re
from typing import List, Optional
from .compliance_evaluator import EvalScore

# Maps query-type keywords to the expected MCP tool name.
# SOURCE OF TRUTH: DataAgent system prompt TOOL SELECTION table (src/agents/data_agent.py).
# Keys use spaces (not underscores) to match natural-language query text.
# Multi-word keys are listed longest-first so the prefix scan in run_maf_eval.py
# matches the most-specific entry before any shorter substring fires.
#
# IMPORTANT: "rate" is NOT mapped here — it is a substring of "corporate", "rate of
# return", etc. and caused systematic false-positives.  Use "treasury" or "eibor"
# for treasury-rate queries; "rate/price/recommended" routes to pricing_recommendation.
QUERY_TYPE_TO_TOOL: dict[str, str] = {
    # DataAgent TOOL SELECTION: rate/price/recommended → pricing_recommendation
    "pricing recommendation": "pricing_recommendation",
    "recommended price":      "pricing_recommendation",
    "recommend":              "pricing_recommendation",
    "price":                  "pricing_recommendation",
    # margin/spread/benchmark → margin_analysis
    "margin":                 "margin_analysis",
    "spread":                 "margin_analysis",
    # profit/ROE/ROA/income → profitability_summary
    "profitability":          "profitability_summary",
    "profit":                 "profitability_summary",
    "roe":                    "profitability_summary",
    "roa":                    "profitability_summary",
    # RWA/capital/Basel → rwa_impact
    "rwa":                    "rwa_impact",
    "capital":                "rwa_impact",
    "basel":                  "rwa_impact",
    # new customer/prospect → new_customer_pricing
    "new customer":           "new_customer_pricing",
    "prospect":               "new_customer_pricing",
    # competitor/market rate → competitor_price_analysis
    "competitor":             "competitor_price_analysis",
    "market rate":            "competitor_price_analysis",
    # trace/breakdown/how priced → pricing_trace
    "pricing trace":          "pricing_trace",
    "breakdown":              "pricing_trace",
    # segment floor/ceiling → segment_pricing_benchmark
    "segment floor":          "segment_pricing_benchmark",
    "segment ceiling":        "segment_pricing_benchmark",
    "benchmark":              "segment_pricing_benchmark",
    "segment":                "segment_pricing_benchmark",
    # ops cost/cost margin → operations_cost_impact
    "operations cost":        "operations_cost_impact",
    "ops cost":               "operations_cost_impact",
    # discount/relationship → relationship_discount
    "relationship discount":  "relationship_discount",
    "discount":               "relationship_discount",
    # win rate/loss analysis → win_loss_insights
    "win rate":               "win_loss_insights",
    "win loss":               "win_loss_insights",
    "loss analysis":          "win_loss_insights",
    "won":                    "win_loss_insights",
    "lost":                   "win_loss_insights",
    # policy exception/breach → policy_exception
    "policy exception":       "policy_exception",
    "exception":              "policy_exception",
    "breach":                 "policy_exception",
    # non-compliant/below floor → non_compliant_deals
    "non-compliant":          "non_compliant_deals",
    "non compliant":          "non_compliant_deals",
    "below floor":            "non_compliant_deals",
    # cross-sell/upsell → cross_sell_opportunity
    "cross-sell":             "cross_sell_opportunity",
    "cross sell":             "cross_sell_opportunity",
    "upsell":                 "cross_sell_opportunity",
    # rating change/downgrade → credit_rating_events
    "credit rating":          "credit_rating_events",
    "rating change":          "credit_rating_events",
    "downgrade":              "credit_rating_events",
    # similar customer → similar_customer_pricing
    "similar customer":       "similar_customer_pricing",
    # profile/360/who is → customer_360
    "customer 360":           "customer_360",
    "360":                    "customer_360",
    "profile":                "customer_360",
    "customer":               "customer_360",
    # treasury/eibor → treasury_rate_sheet  (never bare "rate" — too ambiguous)
    "treasury":               "treasury_rate_sheet",
    "eibor":                  "treasury_rate_sheet",
    "funding cost":           "treasury_rate_sheet",
    # misc
    "product":                "product_master",
    "historical":             "historical_deals",
    "deals":                  "historical_deals",
    "policy":                 "pricing_policy",
}

ALL_KNOWN_TOOLS = {
    "customer_360", "pricing_recommendation", "margin_analysis",
    "rwa_impact", "profitability_summary", "pricing_trace",
    "policy_exception", "segment_pricing_benchmark", "win_loss_insights",
    "relationship_discount", "competitor_price_analysis", "operations_cost_impact",
    "new_customer_pricing", "historical_deals", "pricing_policy",
    "treasury_rate_sheet", "product_master", "customer_master",
    "cross_sell_opportunity", "credit_rating_events", "similar_customer_pricing",
    "compare_fab_vs_competitor", "non_compliant_deals",
}


def correct_sql_view_called(
    agent_outputs: List[str],
    query_type: str,
) -> EvalScore:
    """Validates that DataAgent invoked the right SQL-view tool for the query type.

    Args:
        agent_outputs: list of DataAgent output strings from audit trail
                       (the `output` field for agent_name=DataAgent records)
        query_type: keyword describing the query (e.g. "profitability", "margin")
    """
    expected_tool = QUERY_TYPE_TO_TOOL.get(query_type.lower())
    if expected_tool is None:
        return EvalScore(0.5, "UNKNOWN_QUERY_TYPE", f"No expected tool mapping for query_type='{query_type}'")

    combined_output = " ".join(agent_outputs).lower()

    # Check if expected tool name appears in DataAgent outputs
    if expected_tool in combined_output:
        return EvalScore(1.0, "CORRECT_TOOL", f"Found expected tool '{expected_tool}' in DataAgent output")

    # Check if any known tool was mentioned (partial credit)
    for tool in ALL_KNOWN_TOOLS:
        if tool in combined_output:
            return EvalScore(0.5, "WRONG_TOOL", f"DataAgent used '{tool}' but expected '{expected_tool}'")

    return EvalScore(0.0, "NO_TOOL_FOUND", f"No MCP tool reference found in DataAgent output; expected '{expected_tool}'")


def data_agent_was_called(audit_records: List[dict]) -> EvalScore:
    """Checks that DataAgent was invoked at least once for this request."""
    called = any(r.get("agent_name") == "DataAgent" for r in audit_records)
    if called:
        return EvalScore(1.0, "DATA_AGENT_CALLED", "DataAgent invoked")
    return EvalScore(0.0, "DATA_AGENT_NOT_CALLED", "DataAgent was not invoked for this request")


def rag_agent_was_called(audit_records: List[dict]) -> EvalScore:
    """Checks that RAGAgent was invoked at least once for this request."""
    called = any(r.get("agent_name") == "RAGAgent" for r in audit_records)
    if called:
        return EvalScore(1.0, "RAG_AGENT_CALLED", "RAGAgent invoked")
    return EvalScore(0.0, "RAG_AGENT_NOT_CALLED", "RAGAgent was not invoked for this request")
