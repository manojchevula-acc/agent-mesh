"""Catalog abstraction + business glossary (Component B).

At POC scale this is a thin layer over schema.yaml; at client scale it becomes the
generated metadata catalog (INFORMATION_SCHEMA + FK constraints + curated descriptions +
embeddings), produced offline by the data-pipeline. ``glossary_expand`` maps business
vocabulary onto physical column names so both retrieval rankers benefit from synonyms.
"""

from __future__ import annotations

import re

# Business synonym -> physical column name(s). Seeded from the domain; grows into the
# generated catalog's glossary at scale. Keys are matched as whole words, case-insensitive.
GLOSSARY: dict[str, list[str]] = {
    "gearing": ["debt_to_equity_ratio"],
    "leverage": ["debt_to_equity_ratio"],
    "dte": ["debt_to_equity_ratio"],
    "exposure": ["existing_exposure_aed"],
    "revenue": ["annual_revenue_aed"],
    "turnover": ["annual_revenue_aed"],
    "margin": ["expected_margin_pct", "net_margin_pct"],
    "win rate": ["win_rate_pct", "deal_outcome"],
    "profitability": ["net_margin_pct", "profitability_tier"],
    "capital": ["capital_required_aed", "rwa_aed"],
    "rwa": ["rwa_aed", "rwa_risk_weight_pct"],
    "tenor": ["tenor"],
    "sme": ["customer_segment"],
    "corporate": ["customer_segment"],
}


def glossary_expand(question: str) -> str:
    """Append the physical column names for any business synonym present in the question.
    Returns the original text plus the mapped tokens, so downstream rankers (dense + BM25)
    both see the physical vocabulary. Purely additive — never removes the user's wording."""
    lower = question.lower()
    extra: list[str] = []
    for term, columns in GLOSSARY.items():
        if re.search(rf"\b{re.escape(term)}\b", lower):
            extra.extend(columns)
    if not extra:
        return question
    return f"{question} {' '.join(dict.fromkeys(extra))}"
