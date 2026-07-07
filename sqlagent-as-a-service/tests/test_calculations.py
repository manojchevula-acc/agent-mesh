"""Section 14 — calculation tests, pinned to known historical_deals rows.

These have no dependencies (no SQL, no LLM), so they run anywhere.
"""

import pytest

from sql_agent.calculations.eligibility import (
    segment_eligible,
    tenor_eligible,
    ticket_eligible,
)
from sql_agent.calculations.pricing import (
    PriceInputs,
    approval_required,
    discount_utilisation,
    margin_headroom,
    net_interest_margin,
    recommended_price,
)
from sql_agent.calculations.risk import capital_charge, rwa


def test_recommended_price_matches_deal001():
    inp = PriceInputs(funding_cost_pct=6.00, standard_margin_pct=2.10,
                      risk_premium_pct=0.45, relationship_discount_pct=0.22)
    assert recommended_price(inp) == 8.33  # matches stored DEAL001 exactly


def test_net_interest_margin():
    assert net_interest_margin(8.30, 6.00) == 2.30


def test_margin_headroom_below_floor():
    nim = net_interest_margin(7.00, 6.00)  # 1.00
    assert margin_headroom(nim, 1.50) == -0.50  # below floor


def test_approval_required_threshold():
    # Corporate 0.34 discount exceeds 0.30 threshold -> True
    assert approval_required(0.34, 0.30) is True
    assert approval_required(0.22, 0.30) is False


def test_discount_utilisation():
    assert discount_utilisation(0.22, 0.50) == 0.44
    with pytest.raises(ValueError):
        discount_utilisation(0.10, 0.0)


def test_rwa_and_capital_charge():
    rwa_aed = rwa(120000000, 75)  # 90,000,000
    assert rwa_aed == 90000000.00
    assert capital_charge(rwa_aed) == 7200000.00  # 0.08 Basel minimum


def test_eligibility_helpers():
    assert ticket_eligible(50000000, 1000000, 500000000) is True
    assert ticket_eligible(900000, 1000000, 500000000) is False
    assert tenor_eligible("60M", "12M,24M,36M,60M") is True
    assert tenor_eligible("1M", "12M,24M,36M,60M") is False
    assert segment_eligible("Corporate", "Corporate,SME") is True
    assert segment_eligible("Retail", "Corporate,SME") is False
