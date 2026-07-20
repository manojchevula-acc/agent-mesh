"""Customer NAME -> customer_id resolution for the dynamic tier.

The parameterised view tools take a ``customer_id`` argument and resolve a name to an id
in code (``semantic_view_tools._resolve_customer_id``) before querying. The dynamic tier
gets the customer as free text inside the question, so when the fixed tiers are turned off
we reproduce that step here: find any customer named in the question, map it to its id, and
hand the generator an explicit hint so it filters on the id — exactly what the view tools
did — instead of guessing a ``customer_name`` predicate or a fabricated id.

Best-effort and fail-safe: any DB/error path returns an empty hint, so generation simply
proceeds without it (the question still carries the name for the model to use).
"""

from __future__ import annotations

import re

from sql_agent.db import db
from sql_agent.logging_config import get_logger

log = get_logger("entity")

_CUST_ID_RE = re.compile(r"\bCUST\d+\b", re.IGNORECASE)
# Ignore very short names as match keys — a 1-3 char customer_name would match far too much
# free text. Real customer names in this dataset are multi-word company names.
_MIN_NAME_LEN = 4


def _all_customers() -> list[dict]:
    """(customer_id, customer_name) for every customer. Small reference table; fetched
    per call so a newly added customer resolves without a process restart."""
    rows = db.execute("SELECT customer_id, customer_name FROM customer_master", {})
    return rows.rows if hasattr(rows, "rows") else list(rows)


def resolve_customer_hint(question: str) -> str:
    """Return a prompt hint mapping any customer NAME found in ``question`` to its id, or
    "" when nothing resolves. Names already given as CUSTnnn ids need no resolution."""
    if not question or _CUST_ID_RE.search(question):
        return ""
    try:
        customers = _all_customers()
    except Exception as exc:  # noqa: BLE001 — resolution is best-effort; never block generation
        log.debug("entity resolution skipped | %s", exc)
        return ""

    q_lower = question.lower()
    seen: set[str] = set()
    matches: list[tuple[str, str]] = []
    for row in customers:
        name = (row.get("customer_name") or "").strip()
        cid = (row.get("customer_id") or "").strip()
        if not name or not cid or len(name) < _MIN_NAME_LEN:
            continue
        if name.lower() in q_lower and cid not in seen:
            seen.add(cid)
            matches.append((name, cid))

    if not matches:
        return ""
    lines = "\n".join(f"- \"{name}\" -> customer_id = '{cid}'" for name, cid in matches)
    log.info("ENTITY resolution | %s", ", ".join(f"{n}={c}" for n, c in matches))
    return (
        "ENTITY RESOLUTION — the question names these customers; filter on the id, "
        "not the name:\n" + lines + "\n"
    )
