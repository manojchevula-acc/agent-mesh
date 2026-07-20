"""Section 14 — router tests. Gated tool omitted without scope; circuit breaker fires."""

import pytest

from sql_agent.config import settings
from sql_agent.routing.tier_router import (
    MAX_TOOL_CALLS_PER_TURN,
    fixed_tiers_disabled,
    guard_tool_call,
    tier_of,
    tools_for_caller,
)
from sql_agent.validation.exceptions import SQLAgentError


def _tool_names(tools):
    return {t.name for t in tools}


@pytest.fixture(autouse=True)
def _default_tiers_on(monkeypatch):
    """Pin both fixed tiers ON so these tests don't depend on the developer's .env
    (which may disable a tier for dynamic-only testing). Tests that exercise the toggle
    re-monkeypatch the flag they care about in their own body."""
    monkeypatch.setattr(settings, "parameterised_tools_enabled", True)
    monkeypatch.setattr(settings, "semi_dynamic_tools_enabled", True)


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


def test_disabling_parameterised_tier_omits_its_tools(monkeypatch):
    monkeypatch.setattr(settings, "parameterised_tools_enabled", False)
    names = _tool_names(tools_for_caller("pricing_agent", set()))
    assert "get_customer_360" not in names          # parameterised: gone
    assert "find_customers" in names                # semi_dynamic: still bound
    assert not fixed_tiers_disabled()               # one tier still on


def test_disabling_both_fixed_tiers_binds_dynamic_ungated(monkeypatch):
    monkeypatch.setattr(settings, "parameterised_tools_enabled", False)
    monkeypatch.setattr(settings, "semi_dynamic_tools_enabled", False)
    assert fixed_tiers_disabled()
    # No dynamic_sql scope, yet the dynamic tool is the only thing bound.
    names = _tool_names(tools_for_caller("pricing_agent", set()))
    assert names == {"analytical_query"}


def test_dynamic_scope_gate_bypassed_when_fixed_tiers_off(monkeypatch):
    from sql_agent.tools.registry import set_caller_scopes
    from sql_agent.tools.dynamic.analytical_tool import analytical_query
    from sql_agent.validation.exceptions import AuthError

    monkeypatch.setattr(settings, "parameterised_tools_enabled", True)
    monkeypatch.setattr(settings, "semi_dynamic_tools_enabled", True)
    set_caller_scopes(set())  # no dynamic_sql scope
    # Gate active while a fixed tier is on -> raises before touching the DB/LLM.
    with pytest.raises(AuthError):
        analytical_query.invoke({"question": "total rwa by product"})


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
