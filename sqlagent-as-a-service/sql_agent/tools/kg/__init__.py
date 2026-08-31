"""KG metadata tools (tier: kg_metadata).

Approach B's templates exposed as ReAct tools. They return SCHEMA METADATA only — table and
column names, grains, purposes, join keys — and read no business rows, so they are not gated
by the dynamic_sql scope the way analytical_query is.

Bound only when KG_TOOLS_ENABLED=true. Default off: with both fixed tiers disabled the agent
has exactly one tool by design, and the template match runs deterministically inside the KG
node instead, with no extra LLM round trip (design §7.4).
"""
