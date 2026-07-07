"""Section 4.3 — Treasury tools (treasury_rate_sheet). Parameterised."""

from langchain_core.tools import tool

from sql_agent.db import db
from sql_agent.formatting import format_response
from sql_agent.semantic_layer.loader import canonicalize_enum


@tool
def get_funding_rate(currency: str, tenor: str) -> dict:
    """Fetch the benchmark and funding cost for ONE currency + tenor pair.
    Always exactly two equality conditions — the composite key."""
    # Snap user wording ("60-month", "usd") onto the governed codes ("60M", "USD").
    currency = canonicalize_enum("treasury_rate_sheet", "currency", currency)
    tenor = canonicalize_enum("treasury_rate_sheet", "tenor", tenor)
    sql = """
        SELECT rate_id, currency, benchmark_index, tenor, effective_date,
               benchmark_rate_pct, funding_cost_pct
        FROM treasury_rate_sheet
        WHERE currency = :currency AND tenor = :tenor
        ORDER BY effective_date DESC
    """
    rows = db.execute(sql, {"currency": currency, "tenor": tenor})
    return format_response(rows, tier="parameterised", tool="get_funding_rate")
