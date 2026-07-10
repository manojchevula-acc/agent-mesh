"""The six checks from the Design Document (§7), implemented as one pipeline.

Every query, from every tier, passes through validate() before db.execute() is ever
called. The checks are cheap and the cost of skipping one is a regulated-data
incident, so there is no fast path. Technical Spec §8.2.
"""

import re

import sqlglot  # parse (check #1) and table/column AST walk (checks #3, #4)

from sql_agent.config import settings
from sql_agent.semantic_layer.loader import (
    ALLOWED_TABLES,
    BLOCKED_COLUMNS,
    VIEW_TABLES,
    table_columns,
)

from .exceptions import (
    ColumnBlockedError,
    ColumnNotInTableError,
    InjectionDetectedError,
    JoinNotAllowedError,
    ParseError,
    StatementTypeError,
    TableNotAllowedError,
)

ROW_CAP = settings.row_cap
STATEMENT_TIMEOUT_SECONDS = settings.statement_timeout_seconds

INJECTION_PATTERNS = [
    r";\s*(DROP|DELETE|UPDATE|INSERT|ALTER|TRUNCATE)\b",  # stacked queries
    r"\bUNION\b.+\bSELECT\b",                              # UNION-based injection
    r"--",                                                  # trailing comment trick
    r"/\*.*\*/",                                            # block comment trick
    r"0x[0-9a-fA-F]+",                                      # hex-encoded payloads
]


class SQLValidator:
    def validate(self, sql: str, allowed_join_pairs=None, strict_columns=False) -> str:
        """Runs the safety checks IN ORDER. Returns the (possibly LIMIT-augmented)
        safe SQL string, or raises a typed exception.

        ``allowed_join_pairs`` (set of frozenset table-name pairs) is supplied only by the
        dynamic tier, whose deterministic join resolution knows exactly which joins are
        permitted; when given, check #7 enforces that every join in the SQL is one of them.
        ``strict_columns`` (dynamic tier only) enables check #8, which verifies every
        qualified column belongs to its table — catching a wrong-table reference (e.g.
        ``historical_deals.customer_segment``) before it reaches the database. Hand-written
        tool SQL passes neither, so those tiers are unaffected.
        """
        ast = self._check_1_parse(sql)
        self._check_2_statement_type(ast)
        self._check_3_table_whitelist(ast)
        self._check_4_column_filter(ast)
        self._check_5_injection_scan(sql)
        sql = self._check_6_row_cap(sql, ast)
        self._check_9_view_join_scope(ast)
        if settings.join_graph_check_enabled and allowed_join_pairs is not None:
            self._check_7_join_graph(ast, allowed_join_pairs)
        if settings.column_binding_check_enabled and strict_columns:
            self._check_8_column_binding(ast)
        return sql

    # 1. SQL parse check
    def _check_1_parse(self, sql: str):
        # Parse using the configured backend's dialect so engine-specific syntax
        # (e.g. MySQL CONCAT, backtick quoting) validates correctly. Lazy import
        # avoids a circular import with the db package at module-load time.
        from sql_agent.db.dialect import sqlglot_dialect

        try:
            return sqlglot.parse_one(sql, read=sqlglot_dialect())
        except Exception as e:
            raise ParseError(f"Malformed SQL: {e}")

    # 2. Statement type filter
    def _check_2_statement_type(self, ast):
        if ast is None or ast.key.upper() != "SELECT":
            got = "EMPTY" if ast is None else ast.key.upper()
            raise StatementTypeError(f"Only SELECT allowed, got {got}")

    # 3. Table whitelist
    def _check_3_table_whitelist(self, ast):
        tables = {t.name.lower() for t in ast.find_all(sqlglot.exp.Table)}
        unknown = tables - ALLOWED_TABLES
        if unknown:
            raise TableNotAllowedError(f"Table(s) not in allowed list: {unknown}")

    # 4. Column filter (blocked / sensitive columns)
    def _check_4_column_filter(self, ast):
        cols = {c.name.lower() for c in ast.find_all(sqlglot.exp.Column)}
        hit = cols & BLOCKED_COLUMNS
        if hit:
            raise ColumnBlockedError(f"Blocked column(s) referenced: {hit}")

    # 5. Injection scanner
    def _check_5_injection_scan(self, sql: str):
        for pattern in INJECTION_PATTERNS:
            if re.search(pattern, sql, re.IGNORECASE):
                raise InjectionDetectedError(f"Injection pattern matched: {pattern}")

    # 6. Row cap + timeout (timeout enforced by db/executor.py at execution time)
    def _check_6_row_cap(self, sql: str, ast) -> str:
        if ast.args.get("limit") is None:
            sql = sql.rstrip().rstrip(";") + f" LIMIT {ROW_CAP}"
        else:
            limit_val = int(ast.args["limit"].text("expression"))
            if limit_val > ROW_CAP:
                sql = re.sub(r"LIMIT\s+\d+", f"LIMIT {ROW_CAP}", sql, flags=re.IGNORECASE)
        return sql

    # 7. Join-graph check — every join must be a declared relationship (dynamic tier only).
    # Makes deterministic joins ENFORCED rather than merely suggested: the LLM may choose
    # which tables to use, but never how they connect. Retryable -> self-correction.
    def _check_7_join_graph(self, ast, allowed_pairs) -> None:
        alias_map = self._alias_map(ast)  # alias OR table name -> real table name
        for join in ast.find_all(sqlglot.exp.Join):
            on = join.args.get("on")
            if on is None:
                # A comma/cross join or an ON-less join has no vetted relationship.
                raise JoinNotAllowedError("Join without an ON condition is not allowed")
            equalities = list(on.find_all(sqlglot.exp.EQ))
            if not equalities:
                raise JoinNotAllowedError("Join has no equi-join condition")
            for eq in equalities:
                left = self._resolve_table(eq.left, alias_map)
                right = self._resolve_table(eq.right, alias_map)
                if not left or not right or left == right:
                    continue  # not a cross-table comparison we can vet; skip
                if frozenset((left, right)) not in allowed_pairs:
                    raise JoinNotAllowedError(
                        f"Join not in relationship graph: {left} <-> {right}"
                    )

    # 8. Column-binding check — every QUALIFIED column must belong to its table (dynamic
    # tier only). Catches an LLM referencing a real column on the wrong table (e.g.
    # hd.customer_segment when customer_segment lives on customer_master) before the DB
    # rejects it. Unqualified columns and non-governed qualifiers (CTE/derived) are skipped.
    def _check_8_column_binding(self, ast) -> None:
        alias_map = self._alias_map(ast)     # alias/table name -> real governed table
        col_map = table_columns()            # {table: frozenset(column names)}
        for col in ast.find_all(sqlglot.exp.Column):
            if not col.table:
                continue  # unqualified — cannot attribute to a specific table
            real = alias_map.get(col.table.lower())
            if real is None or real not in col_map:
                continue  # unknown qualifier (CTE/derived/subquery) — not our concern
            if col.name.lower() not in col_map[real]:
                raise ColumnNotInTableError(
                    f"Column '{col.name}' does not belong to table '{real}'"
                )

    # 9. View-join scope check — always on, every tier (defence in depth; hand-written
    # tool SQL never joins a view anyway, so this only ever fires for the dynamic tier).
    # A view may be joined to customer_master ONLY — declared per-view in schema.yaml,
    # for a customer dimension attribute (e.g. industry, region) the view itself lacks —
    # and NEVER chained to a third table. Without this, check #7's per-edge validation
    # alone would permit a 2-hop path like view -> customer_master -> historical_deals
    # (both edges individually declared), which would silently fan out the view's rows
    # by that customer's deal count instead of just adding lookup columns.
    def _check_9_view_join_scope(self, ast) -> None:
        tables = {t.name.lower() for t in ast.find_all(sqlglot.exp.Table)}
        views_in_query = tables & VIEW_TABLES
        if not views_in_query:
            return
        # Checked BEFORE the "others" early-return: two views with no base table present
        # (others would be empty) is still exactly the case this must catch.
        if len(views_in_query) > 1:
            raise JoinNotAllowedError(
                f"Cannot join views to each other: {views_in_query}"
            )
        others = tables - views_in_query
        if not others:
            return  # view queried standalone — the normal case
        if others != {"customer_master"}:
            raise JoinNotAllowedError(
                f"View {views_in_query} may only be joined to customer_master "
                f"(for a customer attribute it lacks), not {others}"
            )

    @staticmethod
    def _alias_map(ast) -> dict:
        """Map every alias (and bare table name) to its real table name, so join checks
        work whether the SQL qualifies columns by alias (``d.customer_id``) or table."""
        mapping: dict[str, str] = {}
        for t in ast.find_all(sqlglot.exp.Table):
            real = t.name.lower()
            mapping[real] = real
            if t.alias:
                mapping[t.alias.lower()] = real
        return mapping

    @staticmethod
    def _resolve_table(expr, alias_map: dict) -> str:
        """Resolve the real table name behind a column reference's qualifier, or ''."""
        if not isinstance(expr, sqlglot.exp.Column) or not expr.table:
            return ""
        return alias_map.get(expr.table.lower(), "")
