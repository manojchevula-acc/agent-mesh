"""Section 14 — router tests. Gated tool omitted without scope; circuit breaker fires."""

import pytest

from sql_agent.routing.tier_router import (
    MAX_TOOL_CALLS_PER_TURN,
    guard_tool_call,
    tier_of,
    tools_for_caller,
)
from sql_agent.validation.exceptions import SQLAgentError


def _tool_names(tools):
    return {t.name for t in tools}


def test_gated_tool_omitted_without_scope():
    tools = tools_for_caller("pricing_agent", set())
    assert "analytical_query" not in _tool_names(tools)
    # The safe tiers are always present. (Base-table get_customer was retired from the
    # registry — see tier_router docstring — so assert on a bound semantic-view tool.)
    assert "get_customer_360" in _tool_names(tools)
    assert "find_customers" in _tool_names(tools)


def test_gated_tool_present_with_scope():
    tools = tools_for_caller("cfo_digital_twin", {"dynamic_sql"})
    assert "analytical_query" in _tool_names(tools)


def test_tier_of():
    assert tier_of("get_customer_360") == "parameterised"
    assert tier_of("find_deals") == "semi_dynamic"
    assert tier_of("analytical_query") == "full_dynamic"
    assert tier_of("does_not_exist") == "unknown"


def test_circuit_breaker_total_calls():
    state = {"tool_call_count": MAX_TOOL_CALLS_PER_TURN, "dynamic_call_count": 0}
    with pytest.raises(SQLAgentError):
        guard_tool_call(state, "get_customer")


def test_circuit_breaker_dynamic_calls():
    state = {"tool_call_count": 0, "dynamic_call_count": 2}
    with pytest.raises(SQLAgentError):
        guard_tool_call(state, "analytical_query")


def test_circuit_breaker_allows_within_bounds():
    state = {"tool_call_count": 0, "dynamic_call_count": 0}
    guard_tool_call(state, "get_customer")  # no raise
