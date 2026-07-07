"""Risk-weighted asset and capital calculations. Technical Spec §7.2."""

DEFAULT_CAPITAL_RATIO = 0.08  # Basel III minimum CET1+buffer, configurable


def rwa(exposure_aed: float, rwa_risk_weight_pct: float) -> float:
    """rwa_aed = exposure * (risk_weight_pct / 100)

    Inputs may arrive as Decimal (from the DB); coerce to float so we never mix
    Decimal with the float literals here (Decimal / float raises TypeError)."""
    return round(float(exposure_aed) * (float(rwa_risk_weight_pct) / 100.0), 2)


def capital_charge(rwa_aed: float, capital_ratio: float = DEFAULT_CAPITAL_RATIO) -> float:
    """capital_charge = rwa * capital_ratio"""
    return round(float(rwa_aed) * capital_ratio, 2)
