"""Multi-tag SQL pattern classifier (Phase 5) — deterministic ``sqlglot`` AST
introspection, no LLM.

Replaces the 4-bucket classifier that used to live in ``example_index.py``
(``ranking`` / ``aggregation`` / ``trend`` / ``lookup``) with the full taxonomy the
retrieval scoring needs to tell "compares two columns against each other" (a
policy-violation-shaped check) apart from "aggregates one column" (a plain average) —
the exact gap that let a lexically-similar-but-logically-different few-shot example
outrank a genuinely relevant one (see docs/FEWSHOT_RETRIEVAL.md).

Supported tags: aggregation, comparison, ranking, trend, policy_violation, threshold,
top_n, bottom_n, join, window_function, cte, subquery, exists, case_when, lookup
(``lookup`` only when nothing else applies).

Never raises: a bad/missing/unparseable SQL yields ``None`` from ``classify_sql`` — every
caller treats that as "no signal available", matching the rest of this codebase's
"retrieval must never break the turn" contract.
"""

from __future__ import annotations

import re

_TIME_HINTS = ("date", "month", "year", "week", "day", "quarter")
_POLICY_HINTS = re.compile(r"policy|compliant|minimum|below_minimum|min_expected_margin")


def _resolve_table(alias_map: dict[str, str], alias: str | None) -> str:
    if not alias:
        return ""
    return alias_map.get(alias, alias)


def classify_sql(sql: str | None) -> dict | None:
    """Parse ``sql`` and return its structural facts + pattern tags, or ``None`` on
    any parse failure or missing SQL."""
    if not sql:
        return None
    try:
        import sqlglot
        from sqlglot import exp

        ast = sqlglot.parse_one(sql, dialect="mysql")
    except Exception:  # noqa: BLE001 — enrichment is best-effort, never fatal
        return None
    if ast is None:
        return None

    tables_nodes = list(ast.find_all(exp.Table))
    alias_map = {t.alias: t.name for t in tables_nodes if t.alias}
    tables = list(dict.fromkeys(t.name.lower() for t in tables_nodes if t.name))
    columns = sorted({c.name.lower() for c in ast.find_all(exp.Column) if c.name})

    joins: list[str] = []
    for j in ast.find_all(exp.Join):
        on = j.args.get("on")
        if isinstance(on, exp.EQ) and isinstance(on.this, exp.Column) \
                and isinstance(on.expression, exp.Column):
            lt = _resolve_table(alias_map, on.this.table) or on.this.table
            rt = _resolve_table(alias_map, on.expression.table) or on.expression.table
            joins.append(f"{lt}.{on.this.name} = {rt}.{on.expression.name}")

    group = ast.find(exp.Group)
    where = ast.find(exp.Where)
    order = ast.find(exp.Order)
    has_limit = ast.find(exp.Limit) is not None

    group_cols = sorted({c.name.lower() for c in group.find_all(exp.Column)}) if group else []
    where_cols = sorted({c.name.lower() for c in where.find_all(exp.Column)}) if where else []
    aggs = sorted({type(f).__name__.upper() for f in ast.find_all(exp.AggFunc)})

    has_comparison = has_threshold = has_policy = False
    if where is not None:
        for cmp in where.find_all((exp.LT, exp.GT, exp.GTE, exp.LTE, exp.NEQ, exp.EQ)):
            left, right = cmp.this, cmp.expression
            left_is_col = isinstance(left, exp.Column)
            right_is_col = isinstance(right, exp.Column)
            names = [n.name.lower() for n in (left, right) if isinstance(n, exp.Column)]
            touches_policy = any(_POLICY_HINTS.search(n) for n in names)
            if left_is_col and right_is_col:
                has_comparison = True
                has_policy = has_policy or touches_policy
            elif left_is_col or right_is_col:
                if not isinstance(cmp, exp.EQ):  # inequality vs. a literal -> threshold
                    has_threshold = True
                has_policy = has_policy or touches_policy

    has_window = ast.find(exp.Window) is not None
    has_cte = ast.find(exp.With) is not None
    has_exists = ast.find(exp.Exists) is not None
    has_case = ast.find(exp.Case) is not None
    # Best-effort: more than one SELECT in the tree (outer + nested) signals a subquery.
    # A soft signal, not a hard classification — acceptable to double-count alongside CTE.
    has_subquery = ast.find(exp.Subquery) is not None or len(list(ast.find_all(exp.Select))) > 1
    has_join = bool(joins) or len(tables) > 1

    is_desc = None
    if order is not None and order.expressions:
        is_desc = bool(order.expressions[0].args.get("desc"))

    patterns: list[str] = []
    if has_cte:
        patterns.append("cte")
    if has_window:
        patterns.append("window_function")
    if has_subquery:
        patterns.append("subquery")
    if has_exists:
        patterns.append("exists")
    if has_case:
        patterns.append("case_when")
    if has_join:
        patterns.append("join")
    if has_comparison:
        patterns.append("comparison")
    if has_threshold:
        patterns.append("threshold")
    if has_policy:
        patterns.append("policy_violation")
    if order is not None and has_limit:
        patterns.append("ranking")
        patterns.append("bottom_n" if is_desc is False else "top_n")
    if group_cols and any(hint in col for col in group_cols for hint in _TIME_HINTS):
        patterns.append("trend")
    if aggs:
        patterns.append("aggregation")
    if not patterns:
        patterns.append("lookup")

    return {
        "patterns": patterns, "tables": tables, "columns": columns, "joins": joins,
        "aggs": aggs, "group_cols": group_cols, "where_cols": where_cols,
        "has_order": order is not None, "has_limit": has_limit,
    }


def sql_pattern(sql: str | None) -> str:
    """Single PRIMARY bucket for ``sql`` — backward-compatible with callers that want
    one label (e.g. eval/check_example_retrieval.py's legacy comparison). Priority:
    trend > policy_violation > comparison > aggregation > ranking > lookup. Prefer
    ``classify_sql(sql)["patterns"]`` for the full multi-tag set."""
    shape = classify_sql(sql)
    if shape is None:
        return ""
    for bucket in ("trend", "policy_violation", "comparison", "aggregation", "ranking"):
        if bucket in shape["patterns"]:
            return bucket
    return "lookup"


def shape_phrase(sql: str | None) -> str:
    """Best-effort natural-language description of ``sql``'s query logic (patterns,
    aggregates, group/filter columns, ordering) — fed into the dense embedding
    alongside the question so the vector space clusters examples by what the SQL
    actually DOES, not just how the question happens to be phrased. Never raises."""
    shape = classify_sql(sql)
    if shape is None:
        return ""

    bits = [f"Query pattern: {', '.join(shape['patterns'])}."]
    if shape["aggs"]:
        bits.append(f"Aggregates: {', '.join(shape['aggs']).lower()}.")
    if shape["group_cols"]:
        bits.append(f"Grouped by: {', '.join(shape['group_cols'])}.")
    if shape["where_cols"]:
        bits.append(f"Filtered by: {', '.join(shape['where_cols'][:5])}.")
    if shape["joins"]:
        bits.append(f"Joined on: {', '.join(shape['joins'][:3])}.")
    if shape["has_order"]:
        bits.append("Top-N ranked result." if shape["has_limit"] else "Sorted result.")
    return " ".join(bits)
