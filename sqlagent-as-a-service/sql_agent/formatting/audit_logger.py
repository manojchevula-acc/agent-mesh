"""Audit & compliance logging. Technical Spec §12.2.

Every invocation logs: caller_agent, correlation_id, tool, tier, args (and, for the
gated dynamic tier, the generated SQL). Hard-reject events additionally raise a
high-severity security event for monitoring (Design Document §11.1).
"""

import json
import time

from sql_agent.logging_config import get_logger

# Structured compliance record (JSON, one line per call).
logger = get_logger("audit")
# Human-readable flow line so a request is easy to follow in the console.
flow = get_logger("tools")


def _tier_of(tool_name: str) -> str:
    # Imported lazily to avoid a circular import with routing.tier_router.
    from sql_agent.routing.tier_router import tier_of
    return tier_of(tool_name)


def log_invocation(state, tool_calls, result):
    cid = state.get("correlation_id") or "-"
    for call in tool_calls:
        tier = _tier_of(call["name"])
        entry = {
            "ts": time.time(),
            "correlation_id": state.get("correlation_id"),
            "caller_agent": state.get("caller_agent"),
            "tool": call["name"],
            "args": call["args"],
            "tier": tier,
        }
        flow.info("[%s] TOOL run | %s[%s] | args=%s",
                  cid, call["name"], tier, call["args"])
        logger.debug(json.dumps(entry))  # full structured record at DEBUG
        if tier == "full_dynamic":
            # also persist the generated SQL text for compliance review
            generated_sql = None
            if isinstance(result, dict):
                generated_sql = result.get("sql")
            if generated_sql:
                flow.info("[%s] TOOL sql | %s", cid, generated_sql)
            logger.debug(json.dumps({**entry, "generated_sql": generated_sql}))


def log_kg_lookup(question: str, lookup, constraints=None) -> None:
    """Persist what the metadata Knowledge Graph contributed to this query.

    The KG doc §10 names "traceability of generated SQL and data lineage" as the governance
    risk and "log KG lookups, retrieved subschema, and validator decisions alongside
    generated SQL" as the mitigation. This is that record. Written per dynamic call, BEFORE
    generation, so it exists even when generation subsequently fails — a rejected attempt
    still consumed metadata, and an auditor reconstructing the turn needs to see what the
    agent was TOLD, not only what it produced.

    Two streams, matching the existing convention: a one-line human-readable flow entry at
    INFO, and the full structured JSON record at DEBUG.

    Contains no row data — table names, column names, business terms, join keys, the matched
    template with its bound entity id, similarity scores, and the KG fingerprint. The
    fingerprint ties the decision to an exact build of the graph, so a later schema migration
    cannot retroactively change the explanation of an old answer.
    """
    if lookup is None:
        return
    record = lookup.as_dict()
    if constraints is not None:
        record["constraints"] = constraints.as_dict()
    record["ts"] = time.time()
    record["question"] = question
    if lookup.template:
        flow.info("KG lookup | %s(%s) | tables=%s | %dms", lookup.template,
                  ", ".join(f"{k}={v}" for k, v in lookup.params.items()),
                  lookup.tables, lookup.latency_ms)
    else:
        flow.info("KG lookup | signals=%s | terms=%s | tables=%s | joins=%d | %dms",
                  lookup.signals_used or "none", lookup.terms or "none",
                  lookup.tables or "none", len(lookup.join_edges), lookup.latency_ms)
    logger.debug(json.dumps({"event": "kg_lookup", **record}, default=str))


def log_validator_decision(sql: str, outcome: str, detail: str = "",
                           kg_enforced: bool = False) -> None:
    """Record a validator verdict against a specific SQL string.

    Separate from log_invocation because a rejection is NOT an invocation — the SQL never ran
    — yet it is exactly the event an auditor asks about ("what did the agent try, and why was
    it stopped?"). ``kg_enforced`` distinguishes a KG-constrained rejection (#10-#12) from a
    core safety rejection (#1-#9): the first is a correctness judgement sourced from a synced
    artifact, the second is the governance boundary itself.
    """
    flow.info("VALIDATOR %s%s | %s", outcome, " [KG]" if kg_enforced else "", detail or "-")
    logger.debug(json.dumps({
        "ts": time.time(), "event": "validator_decision", "outcome": outcome,
        "detail": detail, "kg_enforced": kg_enforced, "sql": sql,
    }))


def log_security_event(event_type: str, detail: str, caller_agent: str):
    logger.warning(json.dumps({
        "ts": time.time(), "event": event_type, "detail": detail,
        "caller_agent": caller_agent, "severity": "high",
    }))
