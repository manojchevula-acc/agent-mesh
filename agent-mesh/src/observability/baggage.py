"""Application request context carried as W3C baggage (Agent Mesh business data).

The mesh's audit trail and cross-process reasoning collection need the originating
user, role, session and request id on every A2A node. These ``fab.*`` entries are
*application* baggage: they ride on the propagation the Gernas SDK configures
(W3C traceparent + baggage, injected by ``GernasA2AInterceptor``, extracted by
``GernasASGIMiddleware``) and are **never** written to telemetry — the SDK copies
only its own allow-listed GERNAS keys onto spans. Telemetry identity (session,
task, tokenized user) is set separately with ``gernas.request_context``.

    ctx, token = set_request_baggage(request_id, user, role, session_id)
    try:
        ...
    finally:
        detach_baggage(token)
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
