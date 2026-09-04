"""Section 6 — Tool Catalogue, Dynamic Tool.

Exactly ONE dynamic tool exists. It is the deliberate single point through which all
free-text-to-SQL generation flows, which keeps the attack surface and the audit
surface both as small as possible. One tool, one gate, one prompt, one log stream.
"""

from sql_agent.tools.decorator import tool

from sql_agent.validation.exceptions import AuthError

from sql_agent.tools.registry import caller_has_scope

GATED_SCOPE = "dynamic_sql"


@tool
def analytical_query(question: str) -> dict:
    """GATED — only callable by agents with the 'dynamic_sql' auth scope
    (e.g. CFO Digital Twin). Use ONLY when no other tool can answer the
    question — typically cross-table aggregation or ad-hoc analytics.

    Pass the user's NATURAL-LANGUAGE question as `question` (e.g. "total RWA and
    average return on RWA by product type"). Do NOT pass SQL, table names, or column
    names — this tool writes and validates the SQL itself from the governed schema.
    Passing SQL or guessed column names will produce a wrong result."""
    # Lazy imports avoid a circular import (routing imports the registry).
    from sql_agent.routing.tier_router import fixed_tiers_disabled
    # The scope gate is bypassed when the fixed tiers are all off — the dynamic tool is
    # then the only data path and the tier_router has already bound it for every caller.
    if not caller_has_scope(GATED_SCOPE) and not fixed_tiers_disabled():
        raise AuthError("analytical_query requires the dynamic_sql scope")
    from sql_agent.routing.query_engine import run_dynamic_pipeline
    return run_dynamic_pipeline(question)  # Section 9.2 generation + Section 8 validation
