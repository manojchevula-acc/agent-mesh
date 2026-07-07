"""Section 14 — validator tests. Each of the six checks, pass + fail cases."""

import pytest

from sql_agent.validation.exceptions import (
    ColumnBlockedError,
    InjectionDetectedError,
    SQLAgentError,
    StatementTypeError,
    TableNotAllowedError,
)
from sql_agent.validation.sql_validator import ROW_CAP, SQLValidator


@pytest.fixture
def validator():
    return SQLValidator()


def test_valid_select_passes_and_gets_limit(validator):
    safe = validator.validate("SELECT customer_id FROM customer_master WHERE risk_category = 'Low'")
    assert f"LIMIT {ROW_CAP}" in safe


def test_existing_limit_within_cap_preserved(validator):
    safe = validator.validate("SELECT customer_id FROM customer_master LIMIT 10")
    assert "LIMIT 10" in safe


def test_oversized_limit_clamped(validator):
    safe = validator.validate("SELECT customer_id FROM customer_master LIMIT 5000")
    assert f"LIMIT {ROW_CAP}" in safe
    assert "5000" not in safe


def test_statement_type_rejects_dml(validator):
    with pytest.raises(StatementTypeError):
        validator.validate("DELETE FROM customer_master WHERE customer_id = 'CUST002'")


def test_statement_type_rejects_insert(validator):
    with pytest.raises(StatementTypeError):
        validator.validate("INSERT INTO customer_master (customer_id) VALUES ('X')")


def test_table_whitelist(validator):
    with pytest.raises(TableNotAllowedError):
        validator.validate("SELECT * FROM customers WHERE id = 1")


def test_blocked_column(validator):
    with pytest.raises(ColumnBlockedError):
        validator.validate("SELECT national_id FROM customer_master")


def test_injection_scanner_catches_comment_trick(validator):
    # Single-statement SELECT that survives parse + statement-type, tripping check #5.
    with pytest.raises(InjectionDetectedError):
        validator.validate("SELECT customer_id FROM customer_master WHERE customer_id = 'x' --")


def test_injection_scanner_regex_for_hex(validator):
    # Hex payloads are caught by the scanner in isolation (and fail parse end-to-end).
    with pytest.raises(InjectionDetectedError):
        validator._check_5_injection_scan("SELECT 0x41 FROM customer_master")


def test_injection_scanner_regex_for_union(validator):
    # The UNION pattern is matched by the scanner in isolation...
    with pytest.raises(InjectionDetectedError):
        validator._check_5_injection_scan("SELECT 1 UNION SELECT 2")


def test_union_query_is_hard_rejected(validator):
    # ...and end-to-end the layered pipeline still hard-rejects UNION (earlier, via
    # the statement-type check, which sees a UNION root rather than a single SELECT).
    with pytest.raises(SQLAgentError):
        validator.validate("SELECT customer_id FROM customer_master UNION SELECT policy_id FROM pricing_policy")


def test_stacked_query_is_hard_rejected(validator):
    with pytest.raises(SQLAgentError):
        validator.validate("SELECT customer_id FROM customer_master; DROP TABLE customer_master")
