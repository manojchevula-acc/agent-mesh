"""Section 9.1 / 9.3 — Tier routing + the bounded fan-out circuit breaker.

Tier routing is mostly DECLARATIVE: it is the set of tools bound to the agent's LLM
(see agent/graph.py). This module holds the one piece of run-time logic — gating the
dynamic tool — plus a registry used for tooling/metrics, and the circuit breaker.

TOOL SCOPE: the single-entity base-table lookups (get_customer, get_product,
get_pricing_policy, get_deal, ...) and the compute_* calculators are commented out
below rather than deleted: they repeatedly caused the agent to fabricate a required
argument (a guessed deal_id, product_id, tenor, or customer_segment) when the user's
question didn't name one, silently returning wrong-population data or a wrong computed
figure instead of failing loudly. The fab_semantic view tools that remain mirror the
Data Agent / DataLayer-as-a-Service tool set (mcp_server/tools.py) — every one has the
same shape (customer_id/deal_id in, pre-built view out, nothing the model has to
invent). find_* (semi_dynamic) and analytical_query (full_dynamic) are KEPT even though
Data Agent has no equivalent — they cover genuine search/aggregate needs the view
tools don't, and haven't shown the same fabrication failure mode. Re-enable a
commented-out tool only alongside a deterministic guard that the required argument was
actually supplied by the user.
"""

from sql_agent.config import settings
from sql_agent.tools.registry import ALL_TOOLS, set_caller_scopes
from sql_agent.validation.exceptions import SQLAgentError

TOOL_TIER_REGISTRY = {
    # -- Removed: base-table lookups and compute_* calculators required arguments
    # (deal_id, product_id, tenor, segment) that the model fabricated when unstated. See
    # module docstring. Keep the implementations importable in sql_agent/tools/ in case
    # one is reinstated behind a proper argument-presence guard.
    # "get_customer": "parameterised", "get_customer_by_name": "parameterised",
    # "get_customer_exposure": "parameterised", "get_product": "parameterised",
    # "get_product_by_name": "parameterised",
    # "get_products_for_segment": "parameterised", "get_funding_rate": "parameterised",
    # "get_pricing_policy": "parameterised", "get_deal": "parameterised",
    # "get_deals_for_customer": "parameterised",
    # "compute_recommended_price": "parameterised", "compute_margin_headroom": "parameterised",
    # "compute_approval_required": "parameterised", "compute_rwa": "parameterised",
    # "compute_ticket_eligibility": "parameterised",
    # parameterised — semantic views (fab_semantic) — Data Agent parity set
    "get_customer_360": "parameterised",
    "get_deal_pricing_compliance": "parameterised",
    "get_deal_margin_analysis": "parameterised",
    "get_customer_profitability": "parameterised",
    "get_deal_rwa_impact": "parameterised",
    "get_customer_pricing_recommendations": "parameterised",
    "get_customer_margin_analysis": "parameterised",
    "get_customer_rwa_impact": "parameterised",
    "get_new_customer_pricing": "parameterised",
    "get_competitor_price_analysis": "parameterised",
    "get_pricing_trace": "parameterised",
    "get_segment_pricing_benchmark": "parameterised",
    "get_operations_cost_impact": "parameterised",
    "get_relationship_discount": "parameterised",
    "get_win_loss_insights": "parameterised",
    "get_policy_exceptions": "parameterised",
    "get_non_compliant_deals": "parameterised",
    "get_cross_sell_opportunity": "parameterised",
    "get_credit_rating_events": "parameterised",
    "get_similar_customer_pricing": "parameterised",
    # semi-dynamic
    "find_customers": "semi_dynamic", "find_products": "semi_dynamic",
    "find_policies": "semi_dynamic", "find_deals": "semi_dynamic",
    # full dynamic (gated)
    "analytical_query": "full_dynamic",
    # meta — ask the user for a missing required input (touches no data)
    # DISABLED for now: do not bind ask_clarification to the agent, so it answers
    # with sensible defaults instead of stopping to ask the user.
    # "ask_clarification": "meta",
}

GATED_TOOLS = {"analytical_query"}
GATED_SCOPE = "dynamic_sql"

# Bounded fan-out ceilings (Section 9.3). Same failure mode as the runaway-agent-loop
# incident pattern; mitigated the same way — a hard ceiling plus logging.
MAX_TOOL_CALLS_PER_TURN = settings.max_tool_calls_per_turn
MAX_DYNAMIC_CALLS_PER_TURN = settings.max_dynamic_calls_per_turn


def tools_for_caller(caller_agent: str, auth_scopes: set) -> list:
    """Returns the exact tool list to bind to this caller's LLM. Gated tools
    are simply omitted for callers without the required scope — the LLM cannot
    select a tool it was never shown. Also publishes the scopes into the
    ContextVar so the gated tool can double-check at call time."""
    auth_scopes = set(auth_scopes or [])
    set_caller_scopes(auth_scopes)
    tools = [ALL_TOOLS[name] for name in TOOL_TIER_REGISTRY if name not in GATED_TOOLS]
    if GATED_SCOPE in auth_scopes:
        tools.append(ALL_TOOLS["analytical_query"])
    return tools


def tier_of(tool_name: str) -> str:
    return TOOL_TIER_REGISTRY.get(tool_name, "unknown")


def guard_tool_call(state, tool_name: str) -> None:
    """Circuit breaker (Section 9.3). Raises once a ceiling is reached."""
    if state["tool_call_count"] >= MAX_TOOL_CALLS_PER_TURN:
        raise SQLAgentError("Tool-call ceiling reached for this turn")
    if tier_of(tool_name) == "full_dynamic":
        if state["dynamic_call_count"] >= MAX_DYNAMIC_CALLS_PER_TURN:
            raise SQLAgentError("Dynamic-call ceiling reached for this turn")
