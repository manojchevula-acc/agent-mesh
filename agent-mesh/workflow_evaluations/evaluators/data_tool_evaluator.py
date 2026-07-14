"""DataAgent tool selection evaluator for FAB AgentMesh.

Validates that DataAgent called the appropriate MCP SQL-view tool
for the given query type, based on audit trail records.
"""
from __future__ import annotations
import re
from typing import List, Optional
from .compliance_evaluator import EvalScore

# Maps query-type keywords to the expected MCP tool name.
# Derived from the 18 semantic views in datalayer-as-service/sql/03_create_semantic_views.sql
QUERY_TYPE_TO_TOOL: dict[str, str] = {
    "profitability":      "profitability_summary",
    "profit":             "profitability_summary",
    "margin":             "margin_analysis",
    "rwa":                "rwa_impact_view",
    "risk_weight":        "rwa_impact_view",
    "pricing_recommendation": "pricing_recommendation_view",
    "recommend":          "pricing_recommendation_view",
    "pricing_trace":      "pricing_trace_view",
    "pricing_exception":  "policy_exception_view",
    "exception":          "policy_exception_view",
    "win_loss":           "win_loss_insights",
    "won":                "win_loss_insights",
    "lost":               "win_loss_insights",
    "relationship_discount": "relationship_discount_view",
    "discount":           "relationship_discount_view",
    "competitor":         "competitor_price_analysis",
    "benchmark":          "segment_pricing_benchmark",
    "segment":            "segment_pricing_benchmark",
    "operations_cost":    "operations_cost_impact",
    "cost":               "operations_cost_impact",
    "new_customer":       "new_customer_pricing_view",
    "prospect":           "new_customer_pricing_view",
    "customer_360":       "customer_360",
    "360":                "customer_360",
    "historical":         "historical_deals",
    "deals":              "historical_deals",
    "pricing_policy":     "pricing_policy",
    "policy":             "pricing_policy",
    "treasury":           "treasury_rate_sheet",
    "rate":               "treasury_rate_sheet",
    "eibor":              "treasury_rate_sheet",
    "product":            "product_master",
    "customer":           "customer_master",
    "credit_rating":      "customer_360",
}

ALL_KNOWN_TOOLS = {
    "customer_360", "pricing_recommendation_view", "margin_analysis",
    "rwa_impact_view", "profitability_summary", "pricing_trace_view",
    "policy_exception_view", "segment_pricing_benchmark", "win_loss_insights",
    "relationship_discount_view", "competitor_price_analysis", "operations_cost_impact",
    "new_customer_pricing_view", "historical_deals", "pricing_policy",
    "treasury_rate_sheet", "product_master", "customer_master",
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
