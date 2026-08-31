"""Binds the tool list per caller scope.

ALL_TOOLS is the master map of tool-name -> callable tool. The set of tools a parent
agent is actually given is the routing table (Design Document §3.4); routing/tier_router
selects from this map per caller, omitting gated tools for callers without the scope.

A ContextVar carries the current caller's auth scopes so the gated analytical_query
can double-check its scope at call time (defence in depth behind registry omission).
"""

from contextvars import ContextVar

# Caller-scope context MUST be defined before importing analytical_tool, which imports
# caller_has_scope from this module (registry <-> analytical_tool is a cycle otherwise).
_current_caller_scopes: ContextVar[frozenset] = ContextVar(
    "_current_caller_scopes", default=frozenset()
)


def set_caller_scopes(scopes) -> None:
    _current_caller_scopes.set(frozenset(scopes or []))


def caller_has_scope(scope: str) -> bool:
    return scope in _current_caller_scopes.get()


# Tools live in per-tier subpackages (parameterised / semi_dynamic / dynamic / meta);
# registry stays the single flat import point, so callers keep importing from here.
from .parameterised.customer_tools import (
    get_customer, get_customer_by_name, get_customer_exposure,
)
from .parameterised.product_tools import (
    get_product, get_product_by_name, get_products_for_segment,
)
from .parameterised.treasury_tools import get_funding_rate
from .parameterised.policy_tools import get_pricing_policy
from .parameterised.deal_tools import get_deal, get_deals_for_customer
from .parameterised.calculation_tools import (
    compute_recommended_price,
    compute_margin_headroom,
    compute_approval_required,
    compute_rwa,
    compute_ticket_eligibility,
)
from .parameterised.semantic_view_tools import (
    get_customer_360,
    get_deal_pricing_compliance,
    get_deal_margin_analysis,
    get_customer_profitability,
    get_deal_rwa_impact,
    get_customer_pricing_recommendations,
    get_customer_margin_analysis,
    get_customer_rwa_impact,
    get_new_customer_pricing,
    get_competitor_price_analysis,
    get_pricing_trace,
    get_segment_pricing_benchmark,
    get_operations_cost_impact,
    get_relationship_discount,
    get_win_loss_insights,
    get_policy_exceptions,
    get_non_compliant_deals,
    get_cross_sell_opportunity,
    get_credit_rating_events,
    get_similar_customer_pricing,
)
from .semi_dynamic.search_tools import (
    find_customers, find_products, find_policies, find_deals,
)
from .dynamic.analytical_tool import analytical_query
from .kg.metadata_tools import (
    get_customer_metadata, get_deal_metadata, get_join_path, get_product_metadata,
)
# DISABLED for now — clarification asking is turned off (see ALL_TOOLS below).
# from .meta.clarify_tools import ask_clarification

# The master tool map. registry is the single import point for every tool.
ALL_TOOLS = {
    # parameterised — customer
    "get_customer": get_customer,
    "get_customer_by_name": get_customer_by_name,
    "get_customer_exposure": get_customer_exposure,
    # parameterised — product
    "get_product": get_product,
    "get_product_by_name": get_product_by_name,
    "get_products_for_segment": get_products_for_segment,
    # parameterised — treasury
    "get_funding_rate": get_funding_rate,
    # parameterised — policy
    "get_pricing_policy": get_pricing_policy,
    # parameterised — deals
    "get_deal": get_deal,
    "get_deals_for_customer": get_deals_for_customer,
    # parameterised — calculations
    "compute_recommended_price": compute_recommended_price,
    "compute_margin_headroom": compute_margin_headroom,
    "compute_approval_required": compute_approval_required,
    "compute_rwa": compute_rwa,
    "compute_ticket_eligibility": compute_ticket_eligibility,
    # parameterised — semantic views (fab_semantic)
    "get_customer_360": get_customer_360,
    "get_deal_pricing_compliance": get_deal_pricing_compliance,
    "get_deal_margin_analysis": get_deal_margin_analysis,
    "get_customer_profitability": get_customer_profitability,
    "get_deal_rwa_impact": get_deal_rwa_impact,
    "get_customer_pricing_recommendations": get_customer_pricing_recommendations,
    "get_customer_margin_analysis": get_customer_margin_analysis,
    "get_customer_rwa_impact": get_customer_rwa_impact,
    "get_new_customer_pricing": get_new_customer_pricing,
    "get_competitor_price_analysis": get_competitor_price_analysis,
    "get_pricing_trace": get_pricing_trace,
    "get_segment_pricing_benchmark": get_segment_pricing_benchmark,
    "get_operations_cost_impact": get_operations_cost_impact,
    "get_relationship_discount": get_relationship_discount,
    "get_win_loss_insights": get_win_loss_insights,
    "get_policy_exceptions": get_policy_exceptions,
    "get_non_compliant_deals": get_non_compliant_deals,
    "get_cross_sell_opportunity": get_cross_sell_opportunity,
    "get_credit_rating_events": get_credit_rating_events,
    "get_similar_customer_pricing": get_similar_customer_pricing,
    # semi-dynamic
    "find_customers": find_customers,
    "find_products": find_products,
    "find_policies": find_policies,
    "find_deals": find_deals,
    # full dynamic (gated)
    "analytical_query": analytical_query,
    # kg metadata — schema metadata only, no business rows (tier: kg_metadata)
    "get_customer_metadata": get_customer_metadata,
    "get_deal_metadata": get_deal_metadata,
    "get_product_metadata": get_product_metadata,
    "get_join_path": get_join_path,
    # meta — ask the user for a missing required input (touches no data)
    # DISABLED for now — clarification asking is turned off.
    # "ask_clarification": ask_clarification,
}
