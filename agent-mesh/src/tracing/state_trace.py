"""Human-readable MeshState transition trace.

Records, as a single readable timeline, how the ``MeshState`` message mutates as
it flows executor -> executor through the mesh workflow, and what is sent /
received over each A2A hop. Answers the question: *"what is being passed from one
agent to another, and how?"*

Emitted via the ``mesh.state`` category logger, so lines land in a dedicated
per-request file ``data/logs/state/{request_id}.log`` (clean format), the
per-request combined file, and the combined log.

Design
------
- Non-invasive: executors call :func:`log_state_handoff` at their exit. The prior
  field snapshot is stashed on the state object itself (``_trace_snapshot``) so we
  can diff without threading extra state through the graph.
- Payload previews are truncated (``Config.STATE_TRACE_PREVIEW_CHARS``) to keep
  files small and limit pre-redaction PII exposure.
- Never raises: tracing must not break a request.
"""
from __future__ import annotations

from typing import Any, Dict, Optional

from src.config import Config
from src.observability import get_logger, CAT_STATE

_log = get_logger(CAT_STATE)

# Fields we diff/report on. Kept small and meaningful (skips internal/verbose bits).
_TRACKED_FIELDS = (
    "compliance_verdict",
    "answer",
    "blocked",
    "block_stage",
)


def preview(text: Optional[str], limit: int | None = None) -> str:
    """Return a single-line, truncated preview of ``text`` for logging."""
    if text is None:
        return ""
    limit = limit or Config.STATE_TRACE_PREVIEW_CHARS
    flat = " ".join(str(text).split())
    return flat if len(flat) <= limit else flat[: limit - 1] + "…"


# Every meaningful MeshState field, in the order shown in the dump. Long text
# fields get preview-truncated; trail is rendered in full.
_FULL_FIELDS = (
    "user_name",
    "role",
    "query",
    "session_id",
    "compliance_verdict",
    "answer",
    "blocked",
    "block_stage",
)


def full_state(state: Any) -> str:
    """Render the complete current MeshState as an indented, readable block.

    Lets the reader watch the whole dataclass evolve step by step across the
    pipeline (not just the diff). Long text fields are truncated to keep the
    file small; ``trail`` and history-turn count are shown explicitly.
    """
    lines = ["    state {"]
    for f in _FULL_FIELDS:
        val = getattr(state, f, None)
        if isinstance(val, str):
            rendered = preview(val) if val else "(empty)"
        else:
            rendered = repr(val)
        lines.append(f"        {f:<18}= {rendered}")
    trail = getattr(state, "trail", []) or []
    lines.append(f"        {'trail':<18}= [{', '.join(trail)}]")
    hist = getattr(state, "conversation_history", []) or []
    lines.append(f"        {'history_turns':<18}= {len(hist) // 2}")
    lines.append("    }")
    return "\n".join(lines)


def snapshot(state: Any) -> Dict[str, Any]:
    """Capture the tracked MeshState fields (plus trail length) for diffing."""
    snap: Dict[str, Any] = {f: getattr(state, f, None) for f in _TRACKED_FIELDS}
    snap["_trail_len"] = len(getattr(state, "trail", []) or [])
    return snap


def _format_delta(prev: Dict[str, Any], curr: Dict[str, Any], state: Any) -> list[str]:
    """Render field changes between two snapshots as readable bullet strings."""
    lines: list[str] = []
    first = not prev  # no prior snapshot: this is the first handoff
    for f in _TRACKED_FIELDS:
        old, new = prev.get(f), curr.get(f)
        if old == new:
            continue
        # On the very first handoff, skip fields still at their falsy default —
        # they haven't meaningfully "changed", just appeared in the baseline.
        if first and not new:
            continue
        if isinstance(new, str) and len(new) > 60:
            lines.append(f'+{f}="{preview(new)}"')
        else:
            lines.append(f"+{f}={new!r}")
    # Show any trail entries appended since the last snapshot.
    old_len = int(prev.get("_trail_len", 0) or 0)
    trail = getattr(state, "trail", []) or []
    if len(trail) > old_len:
        added = trail[old_len:]
        lines.append("trail+=[" + ", ".join(added) + "]")
    return lines


def log_state_handoff(
    from_stage: str,
    to_stage: str,
    state: Any,
    *,
    note: Optional[str] = None,
    a2a: Optional[Dict[str, str]] = None,
) -> None:
    """Log one MeshState handoff: field deltas + optional A2A payload previews.

    Args:
        from_stage: executor emitting the state (e.g. ``"compliance"``).
        to_stage:   next executor, or ``"END"`` for a terminal yield.
        state:      the MeshState instance (mutated in place by executors).
        note:       optional freeform note (e.g. ``"blocked -> early yield"``).
        a2a:        optional dict describing the A2A hop this executor made, with
                    keys: ``target`` (node), ``prompt``, ``response``.
    """
    if not Config.LOG_STATE_TRACE:
        return
    try:
        prev = getattr(state, "_trace_snapshot", None) or {}
        curr = snapshot(state)
        deltas = _format_delta(prev, curr, state)
        state._trace_snapshot = curr  # stash for the next hop

        parts = [f"{from_stage} --> {to_stage}"]
        if note:
            parts.append(f"({note})")
        _log.info(" ".join(parts))

        if a2a:
            target = a2a.get("target", "?")
            if a2a.get("prompt") is not None:
                _log.info("    A2A send  workflow->%s  prompt: \"%s\"",
                          target, preview(a2a.get("prompt")))
            if a2a.get("response") is not None:
                _log.info("    A2A recv  %s->workflow  resp:   \"%s\"",
                          target, preview(a2a.get("response")))

        if deltas:
            _log.info("    delta     %s", "  ".join(deltas))
        elif not a2a:
            _log.info("    delta     (no tracked field change)")

        # Full MeshState snapshot so the whole dataclass is visible at each step.
        _log.info("%s", full_state(state))
    except Exception:
        # Tracing must never break the request pipeline.
        pass
