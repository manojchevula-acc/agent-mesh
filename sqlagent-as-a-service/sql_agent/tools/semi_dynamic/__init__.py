"""Semi-dynamic tier (Section 5) — clause-builder find_* tools that assemble a filtered
shortlist over ONE table from a bounded set of caller-supplied filters. The LLM picks the
filters; the tool builds the WHERE clause from a fixed, validated template.
"""
