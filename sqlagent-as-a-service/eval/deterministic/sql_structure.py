"""Structural comparison of two SQL queries — gold vs agent — via sqlglot.

WHY STRUCTURE, NOT STRING (or a single "tables touched" set)
------------------------------------------------------------
eval/sql_introspect.py already answers "which tables/columns does this query touch". That
is enough to score selection RECALL, but it collapses the query to two flat sets and
throws away everything that distinguishes a correct query from a subtly wrong one: an
agent can read exactly gold's tables and columns and still filter on the wrong value, join
on the wrong key, or drop a GROUP BY. This module decomposes each query into the SEVEN
schema elements the task asks about — tables, selected columns, joins, filters, group-by,
order-by, aggregations (plus aliases) — and scores precision/recall/F1 for each
independently, so the report can say *where* the structures diverge rather than just that
they do.

This is deliberately extensional over the parse tree and blind to text: `WHERE a AND b`
and `WHERE b AND a` produce the same filter SET; `select X as m` and `SELECT x` produce the
same column. sqlglot is the same parser the production validator uses, so "a join" or "a
filter" means here exactly what it means at the safety gate.

None of these numbers is a pass/fail gate on their own — a legitimately different query
(different tables at the same grain, an aggregate written another way) SHOULD score < 1.0
here while still being correct, which is exactly the LLM-vs-deterministic tension we want
to surface. The result-set comparison (result_metrics.py) is what decides correctness;
this decides *similarity of construction*.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import sqlglot
from sqlglot import exp

DIALECT = "mysql"

# sql_agent/agent/prompts.py: "Always include a LIMIT of at most 50 rows" — every dynamic
# query gets this ceiling regardless of what was asked, and the validator force-appends it
# when a generated query omits one. A LIMIT at exactly this value is therefore never
# evidence that the QUESTION asked for a specific count — see is_order_significant below.
SYSTEM_ROW_CAP = 50

# Weights for rolling the seven per-element F1s into one structural-similarity scalar.
# Tables and filters dominate because they change the MEANING of a query; ordering and
# aliases are largely cosmetic and weighted low so a correct query with a different alias
# is not reported as structurally distant.
_ELEMENT_WEIGHTS = {
    "tables": 0.22,
    "columns": 0.18,
    "joins": 0.16,
    "filters": 0.20,
    "group_by": 0.10,
    "order_by": 0.05,
    "aggregations": 0.09,
}


@dataclass
class SqlFootprint:
    """The structural fingerprint of one query. Each field is a normalized SET (or map)
    so two footprints compare by content, never by the text they were parsed from."""
    tables: set[str] = field(default_factory=set)
    columns: set[str] = field(default_factory=set)          # columns in the SELECT list
    joins: set[str] = field(default_factory=set)            # normalized ON conditions
    filters: set[str] = field(default_factory=set)          # normalized WHERE predicates
    group_by: set[str] = field(default_factory=set)
    order_by: list = field(default_factory=list)            # [(expr, 'ASC'|'DESC')], ordered
    aggregations: set[str] = field(default_factory=set)     # e.g. 'sum(amount)', 'count(*)'
    aliases: dict = field(default_factory=dict)             # output alias -> source expr
    limit_value: int | None = None     # None = no LIMIT (or an unparseable one)
    parse_ok: bool = True

    @property
    def order_by_set(self) -> set[str]:
        """order_by as a set for P/R/F1; the ordered list is kept for exact-sequence checks."""
        return {f"{e}:{d}" for e, d in self.order_by}


def parse_footprint(sql: str) -> SqlFootprint:
    """Decompose one SQL string into a SqlFootprint. Never raises: an unparseable query
    yields an empty footprint with parse_ok=False, which scores 0 against a real gold —
    the correct outcome for a query the database itself would reject."""
    if not sql or not sql.strip():
        return SqlFootprint(parse_ok=False)
    try:
        ast = sqlglot.parse_one(sql, dialect=DIALECT)
    except Exception:  # noqa: BLE001 — unparseable SQL is a scoreable outcome, not a crash
        return SqlFootprint(parse_ok=False)
    if ast is None:
        return SqlFootprint(parse_ok=False)

    fp = SqlFootprint()
    fp.tables = {t.name.lower() for t in ast.find_all(exp.Table) if t.name}
    fp.aliases = _aliases(ast)
    alias_names = set(fp.aliases)

    fp.columns = _select_columns(ast, alias_names)
    fp.joins = _joins(ast)
    fp.filters = _filters(ast)
    fp.group_by = {_norm_expr(e) for e in _group_exprs(ast)}
    fp.order_by = _order_by(ast, fp.aliases)
    fp.aggregations = _aggregations(ast)
    fp.limit_value = _limit_value(ast)
    return fp


def _limit_value(ast) -> int | None:
    """The LIMIT's literal row count, or None if there is no LIMIT or it is not a plain
    integer literal (e.g. a bound parameter) — ambiguous cases are treated as "no signal"
    rather than guessed at."""
    lim = ast.find(exp.Limit)
    if lim is None:
        return None
    expr = lim.expression
    if isinstance(expr, exp.Literal) and expr.is_int:
        try:
            return int(expr.this)
        except (TypeError, ValueError):
            return None
    return None


# --------------------------------------------------------------------------- #
# Element extractors
# --------------------------------------------------------------------------- #
def _aliases(ast) -> dict:
    """Output aliases (`expr AS name`) in the projection -> the source expression.

    Kept so ORDER BY / GROUP BY that reference an alias can be resolved back to what they
    actually sort/group by, and so alias RENAMES can be scored on their own axis without
    polluting the column set (a rename is cosmetic; a different underlying expr is not).
    """
    out: dict = {}
    select = ast.find(exp.Select)
    if not select:
        return out
    for proj in select.expressions:
        if isinstance(proj, exp.Alias) and proj.alias:
            out[proj.alias.lower()] = _norm_expr(proj.this)
    return out


def _select_columns(ast, alias_names: set[str]) -> set[str]:
    """Bare column names referenced anywhere in the query, minus output aliases.

    Matches eval/sql_introspect.extract's contract (unqualified, lowercase) so the two
    modules agree on "which columns". Output aliases are excluded: `CASE ... AS band` then
    `GROUP BY band` invents `band`, it does not read a column called band."""
    cols = {c.name.lower() for c in ast.find_all(exp.Column) if c.name}
    return cols - alias_names


def _joins(ast) -> set[str]:
    """One normalized string per JOIN's ON condition, split on AND so `a=b AND c=d` scores
    as two join keys. Equalities are canonicalized order-insensitively (`a=b` == `b=a`) so
    the same join written from either side matches. A comma/CROSS join with no ON yields a
    synthetic marker naming the table pair, so it is still counted as structure."""
    out: set[str] = set()
    for j in ast.find_all(exp.Join):
        on = j.args.get("on")
        if on is None:
            tbl = j.this
            name = tbl.name.lower() if isinstance(tbl, exp.Table) and tbl.name else "?"
            out.add(f"cross:{name}")
            continue
        for pred in _split_and(on):
            out.add(_norm_predicate(pred))
    return out


def _filters(ast) -> set[str]:
    """Normalized WHERE predicates, split on top-level AND. HAVING is folded in too — it is
    a filter on aggregates and belongs to the query's selection logic just as WHERE does.
    OR-groups are kept whole (splitting them would change the meaning)."""
    out: set[str] = set()
    for clause_type in (exp.Where, exp.Having):
        clause = ast.find(clause_type)
        if clause is None:
            continue
        for pred in _split_and(clause.this):
            out.add(_norm_predicate(pred))
    return out


def _group_exprs(ast) -> list:
    group = ast.find(exp.Group)
    return list(group.expressions) if group else []


def _order_by(ast, aliases: dict) -> list:
    """Ordered [(expr, direction)] — order is retained because for a ranking the SEQUENCE
    is the answer, not just the set. Alias references are resolved to their source expr so
    `ORDER BY total DESC` and `ORDER BY SUM(x) DESC` compare equal."""
    order = ast.find(exp.Order)
    if not order:
        return []
    out = []
    for o in order.expressions:
        direction = "DESC" if o.args.get("desc") else "ASC"
        key = _norm_expr(o.this)
        key = aliases.get(key, key)   # resolve `ORDER BY <alias>` back to the real expr
        out.append((key, direction))
    return out


def _aggregations(ast) -> set[str]:
    """Aggregate function applications, normalized as `func(arg)` — e.g. `sum(amount)`,
    `avg(expected_margin_pct)`, `count(*)`. This is the axis that catches the classic
    text-to-SQL error the schema warns about (AVG of a pre-aggregated column): the
    aggregation set differs even when tables and columns match."""
    aggs: set[str] = set()
    agg_types = (exp.Sum, exp.Avg, exp.Count, exp.Min, exp.Max)
    for node in ast.find_all(exp.AggFunc):
        func = node.key.lower()
        if isinstance(node, agg_types) or func in {
                "sum", "avg", "count", "min", "max", "stddev", "variance"}:
            arg = node.this
            inner = "*" if arg is None or isinstance(arg, exp.Star) else _norm_expr(arg)
            distinct = "distinct " if node.args.get("distinct") else ""
            aggs.add(f"{func}({distinct}{inner})")
    return aggs


# --------------------------------------------------------------------------- #
# Normalization helpers
# --------------------------------------------------------------------------- #
def _split_and(node) -> list:
    """Flatten a left-deep tree of AND into its conjuncts. `a AND b AND c` -> [a, b, c],
    so each condition is scored independently rather than as one opaque string."""
    out: list = []
    stack = [node]
    while stack:
        n = stack.pop()
        if isinstance(n, exp.And):
            stack.append(n.left)
            stack.append(n.right)
        elif isinstance(n, exp.Paren):
            stack.append(n.this)
        else:
            out.append(n)
    return out


def _norm_expr(node) -> str:
    """One expression -> a canonical lowercase string, table qualifiers stripped.

    Qualifiers are dropped (`m.expected_margin_pct` -> `expected_margin_pct`) because the
    same column is aliased differently across equivalent queries; comparing qualified would
    punish a correct query for choosing table alias `m` where gold chose `ma`."""
    if node is None:
        return ""
    try:
        copy = node.copy()
        for col in copy.find_all(exp.Column):
            col.set("table", None)
        return copy.sql(dialect=DIALECT, normalize=True, comments=False).lower()
    except Exception:  # noqa: BLE001 — fall back to the node's own text
        return str(node).lower()


def _norm_predicate(node) -> str:
    """Normalize one predicate, canonicalizing symmetric comparisons so `a = b` and
    `b = a` (and `a > b` / `b < a`) hash the same. Everything else defers to _norm_expr."""
    if isinstance(node, exp.EQ):
        lhs, rhs = _norm_expr(node.left), _norm_expr(node.right)
        lo, hi = sorted((lhs, rhs))
        return f"{lo} = {hi}"
    return _norm_expr(node)


def is_order_significant(sql: str) -> bool:
    """Does row ORDER carry meaning for what counts as a correct answer to THIS query?

    True only when the query both ORDERs and LIMITs to FEWER than the system's blanket row
    cap (SYSTEM_ROW_CAP) — a genuine "top N" / ranking shape, where the ordering decides
    WHICH rows make the cut, so a different ordering can legitimately produce a different
    (still-correct-if-computed-right) row SET, and position within it is literally what was
    asked for ("the 5 highest..."). This is TRUE ranking.

    Two shapes are deliberately excluded, both cosmetic rather than answer-bearing:
      * ORDER BY with NO LIMIT — the full result set is returned regardless of ordering, so
        a differently-ordered copy of the same rows is exactly as correct (no row is
        excluded by the ordering). The ORDER BY typically exists only so gold's OWN
        snapshot is reproducible (`ORDER BY deal_id` on a full listing), not because the
        question asks for a sequence.
      * ORDER BY with LIMIT == SYSTEM_ROW_CAP — every dynamic query gets this ceiling
        whether or not the question asked for a specific count (the generation prompt
        enforces it, the validator force-appends it when missing). `ORDER BY deal_id LIMIT
        50` is a stable-listing-with-a-safety-cap, not a "give me the top 50" business ask —
        treating it as a ranking would wrongly fail an agent for returning the SAME 50 rows
        in a different order.

    Deliberately structural (sqlglot on gold_sql only) — no question-text heuristics, no
    dataset annotation to author or drift out of sync. O(len(sql)) — one extra parse.
    """
    fp = parse_footprint(sql)
    return (bool(fp.order_by) and fp.limit_value is not None
            and fp.limit_value < SYSTEM_ROW_CAP)


# --------------------------------------------------------------------------- #
# Scoring
# --------------------------------------------------------------------------- #
def _prf(gold: set, agent: set) -> dict:
    """Precision / recall / F1 of `agent` against `gold`, treating gold as ground truth.

    Element sets that are BOTH empty (neither query has a WHERE, say) score 1.0 across the
    board — agreement on absence is agreement, and would otherwise drag the weighted mean
    down for every simple aggregate query. A gold-empty / agent-nonempty case scores
    precision 0 (the agent added structure gold did not have)."""
    gold, agent = set(gold), set(agent)
    if not gold and not agent:
        return {"precision": 1.0, "recall": 1.0, "f1": 1.0,
                "missing": [], "extra": [], "gold_n": 0, "agent_n": 0}
    hit = gold & agent
    precision = len(hit) / len(agent) if agent else (1.0 if not gold else 0.0)
    recall = len(hit) / len(gold) if gold else (1.0 if not agent else 0.0)
    f1 = (2 * precision * recall / (precision + recall)) if (precision + recall) else 0.0
    return {
        "precision": round(precision, 3), "recall": round(recall, 3), "f1": round(f1, 3),
        "missing": sorted(gold - agent), "extra": sorted(agent - gold),
        "gold_n": len(gold), "agent_n": len(agent),
    }


def compare_footprints(gold_sql: str, agent_sql: str) -> dict:
    """Full structural diff of two queries: a per-element P/R/F1 block plus a single
    weighted `structural_similarity` in [0,1] and an `exact_match` flag.

    `structural_similarity` is the weighted mean of the per-element F1s (weights above);
    `exact_match` is the strict AND of every element matching, i.e. the two queries are the
    same query modulo formatting/aliasing/predicate-order. Both are returned because they
    answer different questions: "how close" vs "identical".
    """
    g, a = parse_footprint(gold_sql), parse_footprint(agent_sql)
    elements = {
        "tables": _prf(g.tables, a.tables),
        "columns": _prf(g.columns, a.columns),
        "joins": _prf(g.joins, a.joins),
        "filters": _prf(g.filters, a.filters),
        "group_by": _prf(g.group_by, a.group_by),
        "order_by": _prf(g.order_by_set, a.order_by_set),
        "aggregations": _prf(g.aggregations, a.aggregations),
    }
    similarity = sum(_ELEMENT_WEIGHTS[k] * elements[k]["f1"] for k in _ELEMENT_WEIGHTS)
    # Aliases scored separately (informational): renaming an output column is cosmetic and
    # must never pull structural_similarity down, but a big rename gap is worth reporting.
    alias_sim = _prf(set(g.aliases.values()), set(a.aliases.values()))["f1"]
    exact = all(elements[k]["f1"] == 1.0 for k in _ELEMENT_WEIGHTS)
    return {
        "parse_ok": {"gold": g.parse_ok, "agent": a.parse_ok},
        "elements": elements,
        "alias_similarity": alias_sim,
        "structural_similarity": round(similarity, 3),
        "exact_match": bool(exact and g.parse_ok and a.parse_ok),
        "order_by_sequence_match": g.order_by == a.order_by,
    }
