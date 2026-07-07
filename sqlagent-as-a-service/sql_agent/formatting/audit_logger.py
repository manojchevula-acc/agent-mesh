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


def log_security_event(event_type: str, detail: str, caller_agent: str):
    logger.warning(json.dumps({
        "ts": time.time(), "event": event_type, "detail": detail,
        "caller_agent": caller_agent, "severity": "high",
    }))
