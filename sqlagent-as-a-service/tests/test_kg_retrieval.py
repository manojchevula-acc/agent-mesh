"""Join-path retrieval and term disambiguation — the two things the KG exists to get right."""

import pytest

from sql_agent.config import settings
from sql_agent.kg.client import view_scope_ok
from sql_agent.kg.retrieval import kg_join_closure, resolve_kg_joins
from tests.kg_fixtures import build_client, build_graph


@pytest.fixture
def client():
    return build_client()


# --- join-path retrieval correctness ---------------------------------------------------


def test_direct_edge_returns_two_table_path(client):
    assert client.join_path("historical_deals", "customer_master") == [
        "historical_deals", "customer_master"]


def test_bridged_path_routes_through_the_hub(client):
    """product_master and pricing_policy are not directly related; the only legal route is
    via historical_deals + customer_master."""
    path = client.join_path("product_master", "pricing_policy")
    assert path[0] == "product_master" and path[-1] == "pricing_policy"
    assert "customer_master" in path


def test_join_path_returns_exact_composite_keys(client):
    """The composite edge must surface BOTH predicates — a single-key join here silently
    fans out, which is the whole reason column_pairs exists."""
    clauses, allowed, used, edges = resolve_kg_joins(
        {"customer_master", "pricing_policy"}, client=client)
    assert len(edges) == 1
    on = edges[0].on_clause()
    assert "customer_segment" in on and "risk_category" in on and " AND " in on
    assert frozenset(("customer_master", "pricing_policy")) in allowed


def test_bridge_tables_enter_the_render_set(client):
    """A path through a bridge must add that bridge to used_tables, or the generator is shown
    a join to a table that is not in its schema context."""
    _, _, used, _ = resolve_kg_joins({"product_master", "pricing_policy"}, client=client)
    assert {"product_master", "pricing_policy", "historical_deals",
            "customer_master"} <= used


def test_unrelated_tables_yield_no_path(client, monkeypatch):
    monkeypatch.setattr(settings, "kg_max_join_hops", 1)
    assert client.join_path("product_master", "pricing_policy") == []


# --- the view-scope rule (validator check #9) ------------------------------------------


def test_view_to_view_path_is_rejected(client):
    """BFS would happily return margin_analysis -> customer_master ->
    pricing_recommendation_view. Check #9 rejects that outright, so the KG must not offer
    it — otherwise it hands the generator a path guaranteed to fail."""
    assert client.join_path("margin_analysis", "pricing_recommendation_view") == []


def test_view_may_still_join_customer_master(client):
    assert client.join_path("margin_analysis", "customer_master") == [
        "margin_analysis", "customer_master"]


def test_view_may_not_reach_a_third_table(client):
    """A view alongside anything other than customer_master fans its rows out."""
    assert client.join_path("margin_analysis", "pricing_policy") == []


@pytest.mark.parametrize(
    "path,legal",
    [
        (["customer_master", "historical_deals"], True),
        (["margin_analysis"], True),
        (["margin_analysis", "customer_master"], True),
        (["margin_analysis", "customer_master", "pricing_policy"], False),
        (["margin_analysis", "customer_master", "pricing_recommendation_view"], False),
    ],
)
def test_view_scope_predicate(path, legal):
    assert view_scope_ok(path, build_graph().views()) is legal


# --- term disambiguation ----------------------------------------------------------------


def test_term_resolves_to_every_physical_column_it_names(client):
    """"policy minimum" is written two different ways across three objects. The KG must
    surface ALL of them with their owners — picking one is the planner's job, and guessing is
    exactly the failure this removes."""
    resolved = client.resolve_term("policy minimum")
    assert set(resolved) == {
        ("pricing_policy", "pricing_policy.min_expected_margin_pct"),
        ("margin_analysis", "margin_analysis.min_expected_margin_pct"),
        ("pricing_recommendation_view",
         "pricing_recommendation_view.policy_min_expected_margin_pct"),
    }


def test_term_spans_differently_named_columns(client):
    """The two spellings must both appear — matching only one is the silent-wrong-answer
    case (a view is chosen whose column has the other name)."""
    columns = {key.rsplit(".", 1)[-1] for _, key in client.resolve_term("policy minimum")}
    assert columns == {"min_expected_margin_pct", "policy_min_expected_margin_pct"}


def test_term_carries_its_definition(client):
    """The definition is what gets embedded; an empty one caps semantic recall."""
    assert client.snapshot().terms["gearing"].definition


def test_unknown_term_resolves_to_nothing(client):
    assert client.resolve_term("xyzzy") == []


# --- join closure (the measured 97% -> 100% step) ---------------------------------------


def test_closure_adds_base_neighbours_only(client):
    """Closure must not pull in views: customer_master neighbours both of them, and a
    view-inclusive closure drags most of the schema into the prompt."""
    closed = kg_join_closure({"customer_master"}, client=client)
    assert "historical_deals" in closed and "pricing_policy" in closed
    assert "margin_analysis" not in closed
    assert "pricing_recommendation_view" not in closed
