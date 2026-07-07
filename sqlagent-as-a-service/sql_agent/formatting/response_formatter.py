"""Typed JSON output. Builds the standard success/error envelope. Technical Spec §12.1.

Rows are rendered as compact JSON (never a raw DB cursor). A truncated flag is set
whenever the row cap (50) is reached. Every response carries an audit_id linking it
to the compliance log entry (Design Document §6.2).
"""

import datetime
import decimal
import re
import uuid
import time


def _sql_literal(value) -> str:
    """Render one bound parameter as a SQL literal for display only."""
    if value is None:
        return "NULL"
    if isinstance(value, bool):  # bool before int — bool is an int subclass
        return "TRUE" if value else "FALSE"
    if isinstance(value, (int, float, decimal.Decimal)):
        return str(value)
    return "'" + str(value).replace("'", "''") + "'"


def _render_sql(sql: str, params: dict) -> str:
    """Inline named bound params (:name) into the SQL so the UI can show the exact
    query that ran. Display only — the DB still received a parameterised statement.
    Longest names first so ``:min_revenue`` is not clipped by ``:min``."""
    if not sql or not params:
        return sql
    rendered = sql
    for key in sorted(params, key=len, reverse=True):
        # :key only when not followed by another identifier char (word boundary).
        rendered = re.sub(rf":{re.escape(key)}\b", _sql_literal(params[key]), rendered)
    # Collapse the whitespace from multi-line f-string SQL into a tidy single block.
    return re.sub(r"\s+", " ", rendered).strip()


def _coerce_rows(rows) -> list[dict]:
    """Accept a QueryResult, a list of dicts, a single dict, or None."""
    if rows is None:
        return []
    if hasattr(rows, "rows"):  # db.executor.QueryResult
        return list(rows.rows)
    if isinstance(rows, list):
        return rows
    return [rows]


def _json_safe(obj):
    """Recursively convert DB types that are not JSON-serializable.

    MySQL cursors return datetime.date / datetime.datetime / decimal.Decimal.
    Without this, LangGraph's ToolNode falls back to str(result), producing
    a Python repr string that _parse_tool_content in api.py cannot re-parse.
    """
    if isinstance(obj, dict):
        return {k: _json_safe(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_json_safe(item) for item in obj]
    if isinstance(obj, (datetime.datetime, datetime.date, datetime.time)):
        return obj.isoformat()
    if isinstance(obj, decimal.Decimal):
        return float(obj)
    if isinstance(obj, bytes):
        return obj.decode("utf-8", errors="replace")
    return obj


def format_response(rows, tier: str, tool: str, calculated: dict = None,
                    truncated: bool = False, started_at: float = None,
                    explain: dict = None, notice: str = None) -> dict:
    """Builds the standard success envelope (Design Document §6.2).

    `explain` (optional) carries a human-readable breakdown of a calculation — the
    symbolic formula plus the actual term values — so the UI can show HOW a computed
    figure was derived. See compute_recommended_price for the shape.

    `notice` (optional) is a non-fatal caveat about the result (e.g. the answer-alignment
    judge was not fully satisfied). It marks a best-effort success, not an error, so the
    agent can present the result while flagging the uncertainty.
    """
    data = _json_safe(_coerce_rows(rows))
    # Honour the truncation flag carried on a QueryResult when not passed explicitly.
    if hasattr(rows, "truncated"):
        truncated = truncated or rows.truncated
    latency_ms = None
    if hasattr(rows, "latency_ms") and rows.latency_ms is not None:
        latency_ms = rows.latency_ms
    elif started_at is not None:
        latency_ms = round((time.time() - started_at) * 1000)

    envelope = {
        "status": "success",
        "tool": tool,
        "query_tier": tier,
        "rows_returned": len(data),
        "data": data,
        # _json_safe the calc results too: compute_* tools return Decimals here, and an
        # un-sanitised Decimal makes the whole envelope non-JSON-serialisable — ToolNode
        # then str()s it and api.py can't parse it back, so the step vanishes from the
        # trace/UI (and a compute-only turn would wrongly look like NoToolInvoked).
        "calculated": _json_safe(calculated or {}),
        "truncated": truncated,
        "latency_ms": latency_ms,
        "cache_hit": False,
        "audit_id": f"qa_{uuid.uuid4().hex[:8]}",
    }

    if explain:
        envelope["calc_explain"] = _json_safe(explain)

    if notice:
        envelope["notice"] = notice

    # Surface the exact SELECT that ran for EVERY tier (parameterised, semi-dynamic,
    # dynamic) so the UI can always show it. The SQL + bound params live on the
    # QueryResult; calc tools pass plain lists and so carry no SQL.
    if hasattr(rows, "sql") and rows.sql:
        params = getattr(rows, "params", {}) or {}
        envelope["sql"] = _render_sql(rows.sql, params)
        if params:
            envelope["sql_params"] = _json_safe(params)

    return envelope


def format_calc(calculated: dict, tier: str, tool: str,
                started_at: float = None, explain: dict = None) -> dict:
    """Convenience wrapper for calculation tools: no rows, populated `calculated`.
    Pass `explain` to show the formula + term values behind the figure in the UI."""
    return format_response([], tier=tier, tool=tool, calculated=calculated,
                           started_at=started_at, explain=explain)


def format_error(error_type: str, message: str, retryable: bool,
                 attempts_used: int = 1) -> dict:
    return {
        "status": "error",
        "error_type": error_type,
        "message": message,
        "retryable": retryable,
        "attempts_used": attempts_used,
        "audit_id": f"qa_{uuid.uuid4().hex[:8]}",
    }
