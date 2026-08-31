"""KG-constrained validation — checks #10, #11 and #12.

Every table/column below also exists in the real schema.yaml, so checks #1-#9 (which read the
real semantic layer) pass and these tests isolate the KG behaviour.
"""

import pytest

from sql_agent.config import settings
from sql_agent.kg.constraints import constraints_for
from sql_agent.validation.exceptions import (
    CardinalityRiskError,
    KGColumnUnknownError,
    KGJoinNotAllowedError,
    KGTypeMismatchError,
)
from sql_agent.validation.sql_validator import SQLValidator
from tests.kg_fixtures import build_client


@pytest.fixture
def validator():
    return SQLValidator()


@pytest.fixture
def kg():
    client = build_client()
    return constraints_for(set(client.snapshot().tables), client=client)


@pytest.fixture(autouse=True)
def _enable_kg_checks(monkeypatch):
    monkeypatch.setattr(settings, "kg_join_check_enabled", True)
    monkeypatch.setattr(settings, "kg_column_check_enabled", True)
    monkeypatch.setattr(settings, "kg_cardinality_check_enabled", True)


# --- check #10: join keys ----------------------------------------------------------------


def test_wrong_join_key_on_a_valid_table_pair_is_rejected(validator, kg):
    """The KEY case. This pair IS declared, so check #7 passes; the SQL parses and would run
    without error in MySQL — it would just answer a different question."""
    sql = ("SELECT hd.deal_id FROM historical_deals hd "
           "JOIN customer_master cm ON hd.product_id = cm.customer_id")
    with pytest.raises(KGJoinNotAllowedError) as exc:
        validator.validate(sql, kg_constraints=kg)
    assert "customer_id" in str(exc.value)  # the message names the correct key


def test_correct_join_key_passes(validator, kg):
    sql = ("SELECT hd.deal_id FROM historical_deals hd "
           "JOIN customer_master cm ON hd.customer_id = cm.customer_id")
    assert validator.validate(sql, kg_constraints=kg)


def test_undeclared_relationship_is_rejected(validator, kg):
    sql = ("SELECT p.product_id FROM product_master p "
           "JOIN pricing_policy pp ON p.product_id = pp.policy_id")
    with pytest.raises(KGJoinNotAllowedError):
        validator.validate(sql, kg_constraints=kg)


# --- check #11: column existence, type, enum domain --------------------------------------


def test_column_on_the_wrong_table_is_rejected(validator, kg):
    sql = "SELECT hd.customer_segment FROM historical_deals hd"
    with pytest.raises(KGColumnUnknownError):
        validator.validate(sql, kg_constraints=kg)


def test_off_enum_literal_is_rejected_with_the_allowed_values(validator, kg):
    """'Term Loan' executes cleanly and returns ZERO rows — a wrong answer that looks valid.
    The message must carry the governed values, because that is what the self-correction
    prompt feeds back to the model."""
    sql = "SELECT p.product_id FROM product_master p WHERE p.product_type = 'Term Loan'"
    with pytest.raises(KGTypeMismatchError) as exc:
        validator.validate(sql, kg_constraints=kg)
    assert "Loan" in str(exc.value) and "Deposit" in str(exc.value)


def test_governed_enum_value_passes(validator, kg):
    sql = "SELECT p.product_id FROM product_master p WHERE p.product_type = 'Loan'"
    assert validator.validate(sql, kg_constraints=kg)


def test_enum_violation_inside_in_list_is_rejected(validator, kg):
    sql = ("SELECT cm.customer_id FROM customer_master cm "
           "WHERE cm.customer_segment IN ('Corporate', 'Enterprise')")
    with pytest.raises(KGTypeMismatchError):
        validator.validate(sql, kg_constraints=kg)


def test_numeric_column_vs_text_literal_is_rejected(validator, kg):
    sql = ("SELECT cm.customer_id FROM customer_master cm "
           "WHERE cm.debt_to_equity_ratio = 'high'")
    with pytest.raises(KGTypeMismatchError):
        validator.validate(sql, kg_constraints=kg)


def test_quoted_number_against_a_numeric_column_is_allowed(validator, kg):
    """MySQL coerces '2.5' happily; rejecting it would be a false positive."""
    sql = ("SELECT cm.customer_id FROM customer_master cm "
           "WHERE cm.debt_to_equity_ratio = '2.5'")
    assert validator.validate(sql, kg_constraints=kg)


# --- check #12: fan-out -------------------------------------------------------------------


def test_aggregate_across_a_one_to_many_join_is_rejected(validator, kg):
    """customer_master is on the 'one' side; joining deals multiplies its rows, so SUM over
    its own column double-counts. Runs without error, returns a wrong number."""
    sql = ("SELECT SUM(cm.existing_exposure_aed) FROM customer_master cm "
           "JOIN historical_deals hd ON cm.customer_id = hd.customer_id")
    with pytest.raises(CardinalityRiskError):
        validator.validate(sql, kg_constraints=kg)


def test_aggregating_the_many_side_is_allowed(validator, kg):
    sql = ("SELECT AVG(hd.expected_margin_pct) FROM customer_master cm "
           "JOIN historical_deals hd ON cm.customer_id = hd.customer_id")
    assert validator.validate(sql, kg_constraints=kg)


def test_min_max_and_count_distinct_are_not_flagged(validator, kg):
    """Duplicates do not change their result, so flagging them would be a false positive."""
    for expr in ("MAX(cm.existing_exposure_aed)", "COUNT(DISTINCT cm.customer_id)"):
        sql = (f"SELECT {expr} FROM customer_master cm "
               "JOIN historical_deals hd ON cm.customer_id = hd.customer_id")
        assert validator.validate(sql, kg_constraints=kg)


def test_partial_composite_join_is_rejected(validator, kg):
    """Joining on customer_segment but not risk_category is what turns a one-to-many join
    into a fan-out. The aggregate is on historical_deals (unrelated to the composite pair)
    so this isolates the composite-completeness check from the plain directional fan-out
    check — pricing_policy is genuinely the "one" side of customer_master, so aggregating
    ITS OWN column across that join is a separate, always-on risk (see
    test_aggregate_across_a_one_to_many_join_is_rejected) that would otherwise pre-empt this
    case before the composite-specific message is ever reached."""
    sql = ("SELECT AVG(hd.expected_margin_pct) FROM historical_deals hd "
           "JOIN customer_master cm ON hd.customer_id = cm.customer_id "
           "JOIN pricing_policy pp ON cm.customer_segment = pp.customer_segment")
    with pytest.raises(CardinalityRiskError) as exc:
        validator.validate(sql, kg_constraints=kg)
    assert "risk_category" in str(exc.value)


def test_complete_composite_join_passes(validator, kg):
    sql = ("SELECT AVG(hd.expected_margin_pct) FROM historical_deals hd "
           "JOIN customer_master cm ON hd.customer_id = cm.customer_id "
           "JOIN pricing_policy pp ON cm.customer_segment = pp.customer_segment "
           "AND cm.risk_category = pp.risk_category")
    assert validator.validate(sql, kg_constraints=kg)


# --- the layering guarantee ----------------------------------------------------------------


def test_no_kg_constraints_leaves_behaviour_unchanged(validator):
    """The security boundary must never depend on the KG. With kg_constraints=None the
    validator runs exactly the checks it ran before this layer existed."""
    sql = ("SELECT hd.deal_id FROM historical_deals hd "
           "JOIN customer_master cm ON hd.product_id = cm.customer_id")
    assert validator.validate(sql)          # #10 would have rejected this


def test_kg_checks_cannot_widen_the_allow_list(validator, kg):
    """A table absent from schema.yaml is still rejected by check #3, whatever the KG says."""
    from sql_agent.validation.exceptions import TableNotAllowedError

    with pytest.raises(TableNotAllowedError):
        validator.validate("SELECT * FROM some_ungoverned_table", kg_constraints=kg)
