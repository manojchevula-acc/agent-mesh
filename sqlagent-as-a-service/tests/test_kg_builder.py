"""Builder: join-rule parsing, cardinality inference, drift, and the governance boundary."""

import pytest

from sql_agent.kg.builder import DriftReport, _type_compatible, parse_join_rule
from sql_agent.kg.schema import MANY_TO_ONE, ONE_TO_MANY, STATUS_ACTIVE, STATUS_PROPOSED
from tests.kg_fixtures import build_graph


@pytest.mark.parametrize("rule,expected", [
    ("customer_id -> historical_deals.customer_id", (("customer_id", "customer_id"),)),
    ("product_id", (("product_id", "product_id"),)),
    ("customer_segment + risk_category",
     (("customer_segment", "customer_segment"), ("risk_category", "risk_category"))),
    ("currency -> treasury_rate_sheet.currency AND tenor -> treasury_rate_sheet.tenor",
     (("currency", "currency"), ("tenor", "tenor"))),
])
def test_all_three_live_join_syntaxes_round_trip(rule, expected):
    pairs, unparsed = parse_join_rule(rule)
    assert pairs == expected and unparsed == []


def test_unparseable_fragment_is_reported_not_guessed():
    """A half-understood join predicate is worse than a missing one."""
    pairs, unparsed = parse_join_rule("customer_id -> deals.customer_id AND (something odd)")
    assert pairs == (("customer_id", "customer_id"),)
    assert unparsed == ["(something odd)"]


def test_cardinality_flips_with_direction():
    """customer_master is unique on customer_id, so deals -> customer is many-to-one and the
    reverse is the fan-out direction."""
    graph = build_graph()
    assert graph.edge_between("historical_deals", "customer_master").cardinality == MANY_TO_ONE
    assert graph.edge_between("customer_master", "historical_deals").cardinality == ONE_TO_MANY


def test_only_active_edges_are_traversable():
    """Proposed edges (name matches, data_dictionary prose) must never reach adjacency —
    guessed joins are the failure mode this layer removes."""
    graph = build_graph()
    assert all(e.status == STATUS_ACTIVE for e in graph.active_edges())
    adjacency = graph.adjacency()
    for edge in graph.foreign_keys:
        if edge.status == STATUS_PROPOSED:
            assert edge.to_table not in adjacency.get(edge.from_table, {})


def test_fingerprint_is_stable_and_content_addressed():
    a, b = build_graph(), build_graph()
    assert a.compute_fingerprint() == b.compute_fingerprint()
    b.tables.pop("product_master")
    assert a.compute_fingerprint() != b.compute_fingerprint()


def test_artifact_round_trips(tmp_path):
    from sql_agent.kg.builder import read_artifact, write_artifact

    original = build_graph()
    restored = read_artifact(write_artifact(original, tmp_path / "kg.json"))
    assert restored.fingerprint == original.fingerprint
    assert restored.edge_between("customer_master", "pricing_policy").column_pairs == \
        original.edge_between("customer_master", "pricing_policy").column_pairs


# --- drift ---------------------------------------------------------------------------------


def test_missing_declared_column_is_blocking_drift():
    report = DriftReport(missing_columns=["historical_deals.customer_id"])
    assert report.has_blocking_drift


def test_undeclared_table_is_not_blocking_drift():
    """The DB may legitimately hold tables schema.yaml never governed (e.g. fab_data) —
    informational only, never a build failure."""
    report = DriftReport(undeclared_tables=["fab_data_legacy"])
    assert not report.has_blocking_drift


def test_missing_table_is_blocking_drift():
    report = DriftReport(missing_tables=["historical_deals"])
    assert report.has_blocking_drift


def test_no_drift_renders_a_clean_message():
    assert "No drift" in DriftReport().render()


# --- type compatibility (drift's type-change detector) --------------------------------------


@pytest.mark.parametrize("logical,physical,compatible", [
    ("float", "decimal(10,4)", True),
    ("float", "varchar(20)", False),
    ("int", "bigint", True),
    ("int", "varchar(20)", False),
    ("str", "varchar(255)", True),
    ("enum", "enum('Y','N')", True),
    ("date", "datetime", True),
])
def test_type_compatible_catches_a_class_change(logical, physical, compatible):
    assert _type_compatible(logical, physical) is compatible
