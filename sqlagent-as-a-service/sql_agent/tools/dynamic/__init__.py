"""Full-dynamic tier (Section 6) — the single gated analytical_query tool through which
all free-text-to-SQL generation flows. One tool, one gate, one prompt, one log stream, so
the attack and audit surface stay as small as possible. Requires the 'dynamic_sql' scope.
"""
