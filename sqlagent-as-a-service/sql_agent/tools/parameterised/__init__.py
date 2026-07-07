"""Parameterised tier (Section 4) — fixed-shape tools that fetch ONE named entity or
compute ONE figure for specific inputs. Each tool owns a single reviewed query/formula;
the LLM only chooses the tool and its arguments, never the SQL.

Includes the deterministic compute_* calculation tools and the pre-joined fab_semantic
view tools, both of which route as tier "parameterised" in tier_router.TOOL_TIER_REGISTRY.
"""
