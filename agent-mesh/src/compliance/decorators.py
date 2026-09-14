"""Compliance layer decorators.

@compliance_gate  — emits a ComplianceEvent around a function call
@guardrail_check  — emits a GuardrailEvent; blocks execution if triggered
@rbac_check       — enforces role; emits RBACEvent on deny
"""
from __future__ import annotations

import asyncio
import functools
import uuid
from typing import Any, Callable, Literal, TypeVar

from .hashing import hash_inputs

F = TypeVar("F", bound=Callable[..., Any])


class GuardrailBlockedError(RuntimeError):
    def __init__(self, rule_id: str, reason: str = "") -> None:
        self.rule_id = rule_id
        super().__init__(f"Guardrail '{rule_id}' blocked execution. {reason}".strip())


class RBACDeniedError(PermissionError):
    def __init__(self, required_role: str, actual_role: str, resource: str = "") -> None:
        self.required_role = required_role
        self.actual_role = actual_role
        super().__init__(
            f"Access denied: required role '{required_role}', "
            f"actual role '{actual_role}' on resource '{resource}'."
        )


_default_config = None


def set_default_compliance_config(config: Any) -> None:
    global _default_config
    _default_config = config


def _resolve_config(kwargs: dict) -> Any:
    cfg = kwargs.pop("config", None) or _default_config
    if cfg is None:
        raise ValueError(
            "compliance_gate/guardrail_check/rbac_check requires a config. "
            "Pass config= kwarg or call set_default_compliance_config() at startup."
        )
    return cfg


def compliance_gate(
    event_type: str,
    actor_key: str = "actor",
    run_id_key: str = "run_id",
) -> Callable[[F], F]:
    """Emit a ComplianceEvent before and after the wrapped function."""
    def decorator(fn: F) -> F:
        if asyncio.iscoroutinefunction(fn):
            @functools.wraps(fn)
            async def async_wrapper(*args: Any, **kwargs: Any) -> Any:
                from .events import ComplianceEvent, emit_compliance_event
                cfg = _resolve_config(kwargs)
                actor = kwargs.get(actor_key, "system")
                run_id = kwargs.get(run_id_key, str(uuid.uuid4()))
                try:
                    result = await fn(*args, **kwargs)
                    event = ComplianceEvent(
                        event_type=event_type,  # type: ignore[arg-type]
                        run_id=run_id,
                        actor=actor,
                        outcome="PASS",
                        payload_hash=hash_inputs(result),
                    )
                    emit_compliance_event(event, cfg)
                    return result
                except Exception as exc:
                    event = ComplianceEvent(
                        event_type=event_type,  # type: ignore[arg-type]
                        run_id=run_id,
                        actor=actor,
                        outcome="FAIL",
                        payload_hash=hash_inputs(str(exc)),
                    )
                    emit_compliance_event(event, cfg)
                    raise
            return async_wrapper  # type: ignore[return-value]
        else:
            @functools.wraps(fn)
            def sync_wrapper(*args: Any, **kwargs: Any) -> Any:
                from .events import ComplianceEvent, emit_compliance_event
                cfg = _resolve_config(kwargs)
                actor = kwargs.get(actor_key, "system")
                run_id = kwargs.get(run_id_key, str(uuid.uuid4()))
                try:
                    result = fn(*args, **kwargs)
                    event = ComplianceEvent(
                        event_type=event_type,  # type: ignore[arg-type]
                        run_id=run_id,
                        actor=actor,
                        outcome="PASS",
                        payload_hash=hash_inputs(result),
                    )
                    emit_compliance_event(event, cfg)
                    return result
                except Exception as exc:
                    event = ComplianceEvent(
                        event_type=event_type,  # type: ignore[arg-type]
                        run_id=run_id,
                        actor=actor,
                        outcome="FAIL",
                        payload_hash=hash_inputs(str(exc)),
                    )
                    emit_compliance_event(event, cfg)
                    raise
            return sync_wrapper  # type: ignore[return-value]
    return decorator


def guardrail_check(
    rule_id: str,
    action_on_block: Literal["block", "warn", "escalate"] = "block",
) -> Callable[[F], F]:
    """Emit a GuardrailEvent from a function that returns (triggered: bool, reason: str)."""
    def decorator(fn: F) -> F:
        if asyncio.iscoroutinefunction(fn):
            @functools.wraps(fn)
            async def async_wrapper(*args: Any, **kwargs: Any) -> Any:
                from .events import GuardrailEvent, emit_guardrail_event
                cfg = _resolve_config(kwargs)
                result = await fn(*args, **kwargs)
                triggered, reason = result if isinstance(result, tuple) else (bool(result), "")
                event = GuardrailEvent(
                    rule_id=rule_id,
                    triggered=triggered,
                    action=action_on_block if triggered else "allow",
                    reason_hash=hash_inputs(reason),
                )
                emit_guardrail_event(event, cfg)
                if triggered and action_on_block == "block":
                    raise GuardrailBlockedError(rule_id, reason)
                return result
            return async_wrapper  # type: ignore[return-value]
        else:
            @functools.wraps(fn)
            def sync_wrapper(*args: Any, **kwargs: Any) -> Any:
                from .events import GuardrailEvent, emit_guardrail_event
                cfg = _resolve_config(kwargs)
                result = fn(*args, **kwargs)
                triggered, reason = result if isinstance(result, tuple) else (bool(result), "")
                event = GuardrailEvent(
                    rule_id=rule_id,
                    triggered=triggered,
                    action=action_on_block if triggered else "allow",
                    reason_hash=hash_inputs(reason),
                )
                emit_guardrail_event(event, cfg)
                if triggered and action_on_block == "block":
                    raise GuardrailBlockedError(rule_id, reason)
                return result
            return sync_wrapper  # type: ignore[return-value]
    return decorator


def rbac_check(required_role: str, resource: str = "") -> Callable[[F], F]:
    """Enforce a role requirement; emits RBACEvent and raises RBACDeniedError on deny."""
    def decorator(fn: F) -> F:
        if asyncio.iscoroutinefunction(fn):
            @functools.wraps(fn)
            async def async_wrapper(*args: Any, **kwargs: Any) -> Any:
                from .events import RBACEvent, emit_rbac_event
                cfg = _resolve_config(kwargs)
                actual_role = _get_role_from_context()
                allowed = actual_role == required_role
                event = RBACEvent(
                    resource=resource or fn.__qualname__,
                    required_role=required_role,
                    actual_role=actual_role,
                    outcome="ALLOW" if allowed else "DENY",
                )
                emit_rbac_event(event, cfg)
                if not allowed:
                    raise RBACDeniedError(required_role, actual_role, resource or fn.__qualname__)
                return await fn(*args, **kwargs)
            return async_wrapper  # type: ignore[return-value]
        else:
            @functools.wraps(fn)
            def sync_wrapper(*args: Any, **kwargs: Any) -> Any:
                from .events import RBACEvent, emit_rbac_event
                cfg = _resolve_config(kwargs)
                actual_role = _get_role_from_context()
                allowed = actual_role == required_role
                event = RBACEvent(
                    resource=resource or fn.__qualname__,
                    required_role=required_role,
                    actual_role=actual_role,
                    outcome="ALLOW" if allowed else "DENY",
                )
                emit_rbac_event(event, cfg)
                if not allowed:
                    raise RBACDeniedError(required_role, actual_role, resource or fn.__qualname__)
                return fn(*args, **kwargs)
            return sync_wrapper  # type: ignore[return-value]
    return decorator


def _get_role_from_context() -> str:
    try:
        from opentelemetry import baggage as otel_baggage
        return (
            otel_baggage.get_baggage("fab.role")
            or otel_baggage.get_baggage("beacon.role")
            or "anonymous"
        )
    except Exception:
        return "anonymous"
