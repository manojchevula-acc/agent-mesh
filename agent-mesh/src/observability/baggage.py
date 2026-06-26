"""W3C Baggage helpers for request identity propagation across process boundaries.

W3C Baggage travels alongside ``traceparent`` / ``tracestate`` in HTTP headers so
every remote A2A node can read the originating user, role, session, and request
identity without an additional lookup.

Usage (orchestrator entry point)
---------------------------------
    ctx, token = set_request_baggage(request_id, user, role, session_id)
    try:
        ...  # code whose OTel context carries the baggage
    finally:
        detach_baggage(token)

The ``opentelemetry.propagate.inject(headers)`` call made by the httpx
instrumentation writes BOTH ``traceparent`` AND ``baggage`` headers, so once
baggage is set in the context it propagates automatically through every A2A hop
— no code changes needed in ``ask_remote``.

On the receiving side, ``extract(dict(request.headers))`` (already called by
``TraceContextMiddleware``) extracts both trace context and baggage from the
inbound headers when the W3C Baggage propagator is in the composite propagator
(ensured by ``setup_observability`` → ``_ensure_composite_propagator``).
"""
from __future__ import annotations

_KEYS: dict[str, str] = {
    "request_id": "fab.request_id",
    "user":       "fab.user",
    "role":       "fab.role",
    "session_id": "fab.session_id",
}


def set_request_baggage(
    request_id: str,
    user: str,
    role: str,
    session_id: str,
) -> tuple[object, object]:
    """Attach identity baggage to the current OTel context.

    Returns ``(ctx, token)``; the caller **must** call ``detach_baggage(token)``
    in a ``finally`` block so the context is not leaked.
    """
    try:
        from opentelemetry import baggage, context as otel_context

        ctx = otel_context.get_current()
        ctx = baggage.set_baggage(_KEYS["request_id"], request_id, context=ctx)
        ctx = baggage.set_baggage(_KEYS["user"],       user,        context=ctx)
        ctx = baggage.set_baggage(_KEYS["role"],       role,        context=ctx)
        ctx = baggage.set_baggage(_KEYS["session_id"], session_id,  context=ctx)
        token = otel_context.attach(ctx)
        return ctx, token
    except Exception:
        return None, None


def detach_baggage(token: object) -> None:
    """Detach the baggage context set by :func:`set_request_baggage`. Safe no-op."""
    if token is None:
        return
    try:
        from opentelemetry import context as otel_context
        otel_context.detach(token)
    except Exception:
        pass


def get_request_id() -> str | None:
    """Returns the ``fab.request_id`` baggage value from the current context."""
    try:
        from opentelemetry import baggage
        return baggage.get_baggage(_KEYS["request_id"]) or None
    except Exception:
        return None


def get_user() -> str | None:
    """Returns the ``fab.user`` baggage value from the current context."""
    try:
        from opentelemetry import baggage
        return baggage.get_baggage(_KEYS["user"]) or None
    except Exception:
        return None


def get_role() -> str | None:
    """Returns the ``fab.role`` baggage value from the current context."""
    try:
        from opentelemetry import baggage
        return baggage.get_baggage(_KEYS["role"]) or None
    except Exception:
        return None


def get_session_id() -> str | None:
    """Returns the ``fab.session_id`` baggage value from the current context."""
    try:
        from opentelemetry import baggage
        return baggage.get_baggage(_KEYS["session_id"]) or None
    except Exception:
        return None


def extract_from_headers(headers: dict) -> object:
    """Extract trace context + baggage from inbound HTTP headers.

    Returns the resulting OTel context object. Delegates to
    ``opentelemetry.propagate.extract`` which uses the composite propagator
    (TraceContext + Baggage) configured by ``_ensure_composite_propagator``.
    """
    try:
        from opentelemetry.propagate import extract
        return extract(headers)
    except Exception:
        return None
