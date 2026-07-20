"""Dynamic-tier customer NAME -> id resolution (routing/entity_resolver)."""

from types import SimpleNamespace

import pytest

from sql_agent.routing import entity_resolver


def _fake_customers(monkeypatch, rows):
    def fake_execute(sql, params):
        return SimpleNamespace(rows=rows)
    monkeypatch.setattr(entity_resolver.db, "execute", fake_execute)


_ROWS = [
    {"customer_id": "CUST007", "customer_name": "Gulf Star Logistics"},
    {"customer_id": "CUST012", "customer_name": "Falcon Trading Co"},
]


def test_resolves_name_to_id(monkeypatch):
    _fake_customers(monkeypatch, _ROWS)
    hint = entity_resolver.resolve_customer_hint(
        "What rate should I offer Gulf Star Logistics?"
    )
    assert "CUST007" in hint
    assert "customer_id = 'CUST007'" in hint
    assert "CUST012" not in hint  # unrelated customer not injected


def test_case_insensitive_match(monkeypatch):
    _fake_customers(monkeypatch, _ROWS)
    hint = entity_resolver.resolve_customer_hint("explain pricing for gulf star logistics")
    assert "CUST007" in hint


def test_id_in_question_skips_resolution(monkeypatch):
    # A CUSTnnn already present => the model can filter directly; no lookup/hint.
    _fake_customers(monkeypatch, _ROWS)
    assert entity_resolver.resolve_customer_hint("pricing trace for CUST007") == ""


def test_no_match_returns_empty(monkeypatch):
    _fake_customers(monkeypatch, _ROWS)
    assert entity_resolver.resolve_customer_hint("total RWA by product type") == ""


def test_db_error_fails_open(monkeypatch):
    def boom(sql, params):
        raise RuntimeError("db down")
    monkeypatch.setattr(entity_resolver.db, "execute", boom)
    assert entity_resolver.resolve_customer_hint("offer for Gulf Star Logistics") == ""


def test_short_names_ignored(monkeypatch):
    _fake_customers(monkeypatch, [{"customer_id": "CUST001", "customer_name": "AB"}])
    # "AB" (< 4 chars) would match far too much free text, so it is not used as a key.
    assert entity_resolver.resolve_customer_hint("what about ABsolutely everything") == ""
