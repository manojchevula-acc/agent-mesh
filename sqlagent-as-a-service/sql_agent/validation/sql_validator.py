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
    CardinalityRiskError,
    ColumnBlockedError,
    ColumnNotInTableError,
    InjectionDetectedError,
    JoinNotAllowedError,
    KGColumnUnknownError,
    KGJoinNotAllowedError,
    KGTypeMismatchError,
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

# A quoted literal MySQL would still coerce to a usable number ('12', '3.5', '-0.4'). Only a
# quoted value that is not numeric AT ALL trips the type half of check #11.
_NUMERIC_LITERAL = re.compile(r"^[+-]?\d+(\.\d+)?$")


def _looks_numeric(value: str) -> bool:
    return bool(_NUMERIC_LITERAL.match(value.strip()))


class SQLValidator:
    def validate(self, sql: str, allowed_join_pairs=None, strict_columns=False,
                 kg_constraints=None) -> str:
        """Runs the safety checks IN ORDER. Returns the (possibly LIMIT-augmented)
        safe SQL string, or raises a typed exception.

        ``allowed_join_pairs`` (set of frozenset table-name pairs) is supplied only by the
        dynamic tier, whose deterministic join resolution knows exactly which joins are
        permitted; when given, check #7 enforces that every join in the SQL is one of them.
        ``strict_columns`` (dynamic tier only) enables check #8, which verifies every
        qualified column belongs to its table — catching a wrong-table reference (e.g.
        ``historical_deals.customer_segment``) before it reaches the database. Hand-written
        tool SQL passes neither, so those tiers are unaffected.

        ``kg_constraints`` (a kg.constraints.KGConstraints, dynamic tier only) enables the
        KG-constrained checks #10-#12. They EXTEND the checks above rather than replacing any
        of them: checks #1-#9 remain the security boundary and still derive their allow-lists
        from schema.yaml, so a KG that is stale, unbuilt or switched off can never WIDEN what
        the agent is permitted to read — only decline to add the extra precision. That
        ordering is deliberate: correctness grounding may depend on a synced artifact; the
        safety gate may not.
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
        if kg_constraints is not None:
            if settings.kg_join_check_enabled:
                self._check_10_kg_join_keys(ast, kg_constraints)
            if settings.kg_column_check_enabled:
                self._check_11_kg_columns(ast, kg_constraints)
            if settings.kg_cardinality_check_enabled:
                self._check_12_kg_cardinality(ast, kg_constraints)
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

    # ---------------------------------------------------------------------------------
    # KG-constrained validation (KG design doc §4.1, "KG-Constrained Validation").
    # These run only for the dynamic tier and only when a KG constraint bundle was resolved
    # for this query. Each is a STRICTER version of something the pipeline already does
    # approximately, using metadata checks #7/#8 simply do not have: the exact column pairs
    # behind a relationship, the physical/enum domain of a column, and join cardinality. All
    # are retryable, and every message names the FIX so the self-correction prompt carries
    # the answer, not just the complaint.
    # ---------------------------------------------------------------------------------

    # 10. KG join-key check — a join must use the KG edge's DECLARED column pair.
    # Check #7 asks "may these two tables be joined at all?"; this asks "and on the right
    # keys?". Joining customer_master to historical_deals on `product_id` instead of
    # `customer_id` passes #7 (the table pair IS declared), parses, and runs without error in
    # MySQL — it just answers a different question. This is the check that makes join paths
    # retrieved rather than guessed, which is the whole point of the layer.
    def _check_10_kg_join_keys(self, ast, kg) -> None:
        alias_map = self._alias_map(ast)
        for join in ast.find_all(sqlglot.exp.Join):
            on = join.args.get("on")
            if on is None:
                raise KGJoinNotAllowedError("Join without an ON condition is not allowed")
            for eq in on.find_all(sqlglot.exp.EQ):
                left = self._column_ref(eq.left, alias_map)
                right = self._column_ref(eq.right, alias_map)
                if left is None or right is None or left[0] == right[0]:
                    continue  # not a cross-table column comparison we can vet
                if not (kg.has_table(left[0]) and kg.has_table(right[0])):
                    continue  # outside the KG's scope for this query — #3/#7 still apply
                declared = kg.declared_pairs(left[0], right[0])
                if not declared:
                    raise KGJoinNotAllowedError(
                        f"No relationship exists in the knowledge graph between "
                        f"'{left[0]}' and '{right[0]}' — do not join them"
                    )
                used = frozenset((f"{left[0]}.{left[1]}", f"{right[0]}.{right[1]}"))
                if used not in declared:
                    expected = " OR ".join(
                        " = ".join(sorted(pair)) for pair in sorted(map(sorted, declared))
                    )
                    raise KGJoinNotAllowedError(
                        f"Join {left[0]}.{left[1]} = {right[0]}.{right[1]} is not the "
                        f"declared key for this relationship. Join on: {expected}"
                    )

    # 11. KG column check — existence, type compatibility, and enum domain.
    # Existence overlaps #8 but is sourced from the KG (which knows the PHYSICAL columns, not
    # only the declared ones); the type/enum halves are new. The enum half earns its keep on
    # this schema in particular: the generator reliably echoes the user's wording ("Term
    # Loan", "60-month") rather than the governed token ("Loan", "60M"), which executes
    # cleanly and returns ZERO rows — a wrong answer that looks like a valid one. The
    # parameterised tools already defend against this via loader.canonicalize; until now the
    # dynamic tier had no equivalent.
    def _check_11_kg_columns(self, ast, kg) -> None:
        alias_map = self._alias_map(ast)

        for col in ast.find_all(sqlglot.exp.Column):
            ref = self._column_ref(col, alias_map)
            if ref is None or not kg.has_table(ref[0]):
                continue
            if kg.column(ref[0], ref[1]) is None:
                raise KGColumnUnknownError(
                    f"Column '{ref[1]}' does not exist on '{ref[0]}' — check which table "
                    f"owns it before qualifying it"
                )

        for comparison in ast.find_all(sqlglot.exp.EQ, sqlglot.exp.NEQ):
            for column_side, literal_side in ((comparison.left, comparison.right),
                                              (comparison.right, comparison.left)):
                if isinstance(literal_side, sqlglot.exp.Literal):
                    self._assert_literal_fits(column_side, literal_side, alias_map, kg)

        for member in ast.find_all(sqlglot.exp.In):
            for literal in member.args.get("expressions") or []:
                if isinstance(literal, sqlglot.exp.Literal):
                    self._assert_literal_fits(member.this, literal, alias_map, kg)

    def _assert_literal_fits(self, column_expr, literal, alias_map, kg) -> None:
        """One column-vs-literal comparison, checked against the KG's column node."""
        ref = self._column_ref(column_expr, alias_map)
        if ref is None or not kg.has_table(ref[0]):
            return
        node = kg.column(ref[0], ref[1])
        if node is None:
            return
        value = str(literal.this)

        if node.enum_values:
            allowed = {v.lower() for v in node.enum_values}
            if literal.is_string and value.lower() not in allowed:
                raise KGTypeMismatchError(
                    f"'{value}' is not a permitted value for {ref[0]}.{ref[1]}. "
                    f"Allowed values: {', '.join(node.enum_values)}"
                )
            return  # an enum column's domain IS its enum; no further type check applies

        # A numeric column compared to a non-numeric string literal never matches a row, and
        # is nearly always the model quoting a value it should have written bare.
        if node.is_numeric and literal.is_string and not _looks_numeric(value):
            raise KGTypeMismatchError(
                f"{ref[0]}.{ref[1]} is {node.logical_type}; it cannot be compared to the "
                f"text value '{value}'"
            )

    # 12. KG cardinality / fan-out guard — the check that catches a query which is
    # syntactically perfect, passes every other check, executes without error, and returns a
    # WRONG NUMBER. Joining a one-row-per-entity table to a many-row table multiplies the
    # first table's rows, so SUM/AVG/COUNT over ITS columns silently counts each value once
    # per matching row on the other side. Off by default (kg_cardinality_check_enabled): it
    # is a judgement call rather than a safety rule, so it earns a shadow period first, the
    # same way intent detection and the answer judge were introduced.
    def _check_12_kg_cardinality(self, ast, kg) -> None:
        alias_map = self._alias_map(ast)
        aggregated = self._aggregated_tables(ast, alias_map)
        if not aggregated:
            return
        for join in ast.find_all(sqlglot.exp.Join):
            on = join.args.get("on")
            if on is None:
                continue
            for eq in on.find_all(sqlglot.exp.EQ):
                left = self._column_ref(eq.left, alias_map)
                right = self._column_ref(eq.right, alias_map)
                if left is None or right is None or left[0] == right[0]:
                    continue
                for near, far in ((left[0], right[0]), (right[0], left[0])):
                    if near in aggregated and kg.fans_out(near, far):
                        edge = kg.edge(near, far)
                        raise CardinalityRiskError(
                            f"Aggregating {near}'s columns across a {edge.cardinality} join "
                            f"to {far} double-counts them ({far} has multiple rows per "
                            f"{near} row). Aggregate {far}'s own measures, or pre-aggregate "
                            f"{far} in a subquery before joining"
                        )
                # A composite relationship joined on only SOME of its key columns is the same
                # failure with a different cause: the missing predicate is what was keeping
                # the join one-to-one.
                if kg.is_composite(left[0], right[0]):
                    self._assert_composite_complete(join, left[0], right[0], alias_map, kg)

    def _assert_composite_complete(self, join, table_a, table_b, alias_map, kg) -> None:
        declared = kg.declared_pairs(table_a, table_b)
        used = set()
        for eq in (join.args.get("on") or join).find_all(sqlglot.exp.EQ):
            left = self._column_ref(eq.left, alias_map)
            right = self._column_ref(eq.right, alias_map)
            if left and right and left[0] != right[0]:
                used.add(frozenset((f"{left[0]}.{left[1]}", f"{right[0]}.{right[1]}")))
        missing = declared - used
        if missing:
            predicates = " AND ".join(
                " = ".join(sorted(p)) for p in sorted(map(sorted, missing)))
            raise CardinalityRiskError(
                f"{table_a} and {table_b} join on a composite key; the ON clause is missing "
                f"{predicates}, which fans out the result"
            )

    def _aggregated_tables(self, ast, alias_map) -> set[str]:
        """Tables whose columns sit inside a fan-out-sensitive aggregate.

        SUM, AVG and plain COUNT are sensitive to duplicated rows. MIN, MAX and
        COUNT(DISTINCT ...) are not — duplicates do not change their result — so they are
        deliberately excluded rather than producing a false rejection.
        """
        tables: set[str] = set()
        for node in ast.find_all(sqlglot.exp.Sum, sqlglot.exp.Avg, sqlglot.exp.Count):
            if isinstance(node, sqlglot.exp.Count) and isinstance(
                node.this, sqlglot.exp.Distinct
            ):
                continue
            for col in node.find_all(sqlglot.exp.Column):
                ref = self._column_ref(col, alias_map)
                if ref is not None:
                    tables.add(ref[0])
        return tables

    @staticmethod
    def _column_ref(expr, alias_map: dict) -> tuple[str, str] | None:
        """(real_table, column) for a QUALIFIED column reference, else None.

        Unqualified columns return None: without a qualifier there is no sound way to say
        which table owns them, and guessing is exactly what these checks exist to stop."""
        if not isinstance(expr, sqlglot.exp.Column) or not expr.table:
            return None
        real = alias_map.get(expr.table.lower())
        return (real, expr.name.lower()) if real else None

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
