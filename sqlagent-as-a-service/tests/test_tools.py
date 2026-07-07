"""Section 14 — tool tests. Correct SQL shape per tool; exactly the declared filters.

db.execute is monkeypatched to capture (sql, params) without needing a live database.
"""

import pytest

from sql_agent.db import db


@pytest.fixture
def capture(monkeypatch):
    calls = []

    def fake_execute(sql, params=None):
        calls.append({"sql": " ".join(sql.split()), "params": params or {}})
        return []  # empty result set; tools just format it

    monkeypatch.setattr(db, "execute", fake_execute)
    return calls


def test_get_customer_single_shape(capture):
    from sql_agent.tools.parameterised.customer_tools import get_customer

    get_customer.invoke({"customer_id": "CUST002"})
    assert capture[0]["params"] == {"customer_id": "CUST002"}
    assert "WHERE customer_id = :customer_id" in capture[0]["sql"]
    assert "FROM customer_master" in capture[0]["sql"]


def test_get_pricing_policy_three_conditions(capture):
    from sql_agent.tools.parameterised.policy_tools import get_pricing_policy

    get_pricing_policy.invoke({"segment": "Corporate", "product_type": "Loan",
                               "risk_category": "Low"})
    sql = capture[0]["sql"]
    assert "customer_segment = :segment" in sql
    assert "product_type = :product_type" in sql
    assert "risk_category = :risk_category" in sql
    assert "status = 'Active'" in sql


def test_find_customers_only_emits_populated_filters(capture):
    from sql_agent.tools.semi_dynamic.search_tools import find_customers

    find_customers.invoke({"segment": "Corporate", "min_revenue": 50_000_000})
    sql = capture[0]["sql"]
    where = sql.split("WHERE", 1)[1]  # inspect the generated clause only
    assert "customer_segment = :segment" in where
    assert "annual_revenue_aed >= :min_revenue" in where
    # Unconstrained filters must NOT appear as conditions in the WHERE clause.
    assert "risk_category =" not in where
    assert "region =" not in where
    assert capture[0]["params"] == {"segment": "Corporate", "min_revenue": 50_000_000}


def test_find_deals_below_floor_shortcut(capture):
    from sql_agent.tools.semi_dynamic.search_tools import find_deals

    find_deals.invoke({"outcome": "Won", "below_floor": True})
    sql = capture[0]["sql"]
    assert "deal_outcome = :outcome" in sql
    assert "expected_margin_pct < 0" in sql
