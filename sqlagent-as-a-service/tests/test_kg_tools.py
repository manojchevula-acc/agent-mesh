"""Approach B tools: registration, gating, and the metadata-only guarantee."""

from sql_agent.config import settings
from sql_agent.kg.templates import match_template
from sql_agent.routing.tier_router import TOOL_TIER_REGISTRY, tools_for_caller
from sql_agent.tools.registry import ALL_TOOLS

KG_TOOLS = {"get_customer_metadata", "get_deal_metadata", "get_product_metadata",
            "get_join_path"}


def test_tools_are_registered_under_the_kg_metadata_tier():
    assert KG_TOOLS <= set(ALL_TOOLS)
    assert all(TOOL_TIER_REGISTRY[name] == "kg_metadata" for name in KG_TOOLS)


def test_tools_are_not_bound_when_disabled(monkeypatch):
    monkeypatch.setattr(settings, "kg_tools_enabled", False)
    bound = {t.name for t in tools_for_caller("cfo_twin", {"dynamic_sql"})}
    assert not (KG_TOOLS & bound)


def test_tools_are_bound_when_enabled(monkeypatch):
    monkeypatch.setattr(settings, "kg_tools_enabled", True)
    bound = {t.name for t in tools_for_caller("cfo_twin", {"dynamic_sql"})}
    assert KG_TOOLS <= bound


def test_deal_pattern_wins_over_customer_pattern():
    """A question naming both a deal and its customer is a deal question."""
    template, params = match_template("margin on DEAL010 for customer CUST002")
    assert template.name == "get_deal_metadata"
    assert params == {"deal_id": "DEAL010"}


def test_template_match_is_case_insensitive_and_word_anchored():
    assert match_template("show me cust002")[1] == {"customer_id": "CUST002"}
    assert match_template("no ids here") is None


def test_tool_payload_is_metadata_only(monkeypatch):
    """The KG tools return schema metadata (table/column names, grain, purpose) — never a
    business data row. This is what keeps them ungated by the dynamic_sql scope."""
    from sql_agent.tools.kg import metadata_tools
    from tests.kg_fixtures import build_client

    from tests.conftest import call_tool

    monkeypatch.setattr(metadata_tools, "get_kg_client", lambda: build_client())
    result = call_tool(metadata_tools.get_customer_metadata, {"customer_id": "CUST002"})
    assert result["status"] == "success"
    for obj in result["objects"]:
        assert set(obj) == {"table", "db_schema", "grain", "purpose", "is_view", "columns"}
