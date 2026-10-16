"""Generic span decorators and Beacon Kit compatibility aliases.

``traced``, ``traced_node`` and ``traced_pipeline`` are general-purpose spans kept
from Beacon Kit (now with the canonical envelope and safe attribute writes).

``traced_agent``, ``traced_llm_call``, ``traced_tool``, ``traced_mcp_tool`` and
``traced_a2a_hop`` are deprecated aliases that forward to the spec decorators
(``trace_agent``, ``trace_llm``, ``trace_tool``, ``trace_mcp``,
``trace_delegation``). They emit a ``DeprecationWarning`` once per decoration.
"""
from __future__ import annotations

import warnings
from typing import Any, Callable, Mapping, TypeVar

from opentelemetry.trace import SpanKind

from gernas.decorators._core import Call, Spec, wrap
from gernas.tracer import safe_set

F = TypeVar("F", bound=Callable[..., Any])

_KIND_NAMES = {SpanKind.INTERNAL: "INTERNAL", SpanKind.SERVER: "SERVER", SpanKind.CLIENT: "CLIENT",
               SpanKind.PRODUCER: "PRODUCER", SpanKind.CONSUMER: "CONSUMER"}


class _GenericSpec(Spec):
    def __init__(self, name: str, attributes: Mapping[str, Any] | None, kind: SpanKind,
                 state_attributes: list[str] | None = None, attribute_keys: list[str] | None = None,
                 prefix: str = "") -> None:
        self.op = name
        self.kind = _KIND_NAMES.get(kind, "INTERNAL")
        self.attributes = dict(attributes or {})
        self.state_attributes, self.attribute_keys, self.prefix = state_attributes, attribute_keys, prefix

    def start(self, span: Any, call: Call) -> None:
        for key, value in self.attributes.items():
            safe_set(span, key, value)
        if self.state_attributes and call.args and isinstance(call.args[0], dict):
            for key in self.state_attributes:
                if key in call.args[0]:
                    safe_set(span, f"node.{key}", str(call.args[0][key]))
        if self.attribute_keys:
            for key in self.attribute_keys:
                if key in call.kwargs:
                    safe_set(span, f"{self.prefix}{key}", str(call.kwargs[key]))


def traced(fn: F | None = None, *, name: str | None = None, attributes: Mapping[str, Any] | None = None,
           kind: SpanKind = SpanKind.INTERNAL) -> Any:
    """Wrap a function in a child span (``@traced`` or ``@traced(name=...)``)."""
    def decorator(f: F) -> F:
        return wrap(f, _GenericSpec(name or f.__qualname__, attributes, kind))
    return decorator(fn) if fn is not None else decorator


def traced_node(name: str, attributes: Mapping[str, Any] | None = None,
                state_attributes: list[str] | None = None) -> Callable[[F], F]:
    """Span for a graph node; ``state_attributes`` are read from a dict state arg as ``node.<key>``."""
    def decorator(fn: F) -> F:
        return wrap(fn, _GenericSpec(name, attributes, SpanKind.INTERNAL, state_attributes=state_attributes))
    return decorator


def traced_pipeline(name: str, attribute_keys: list[str] | None = None) -> Callable[[F], F]:
    """Root span for a pipeline; ``attribute_keys`` kwargs are recorded as ``pipeline.<key>``."""
    def decorator(fn: F) -> F:
        return wrap(fn, _GenericSpec(name, {"pipeline.name": name}, SpanKind.INTERNAL,
                                     attribute_keys=attribute_keys, prefix="pipeline."))
    return decorator


# ── deprecated Beacon Kit aliases ────────────────────────────────────────────

def _deprecated(old: str, new: str) -> None:
    warnings.warn(f"{old} is deprecated; use gernas.{new}", DeprecationWarning, stacklevel=3)


def traced_agent(name: str, attributes: Mapping[str, Any] | None = None) -> Callable[[F], F]:
    _deprecated("traced_agent", "trace_agent")
    from gernas.decorators.agent import trace_agent
    return trace_agent(name, attributes=attributes)


def traced_llm_call(model: str | Callable[[Any], str] | None = None,
                    attribute_keys: list[str] | None = None) -> Callable[[F], F]:
    _deprecated("traced_llm_call", "trace_llm")
    from gernas.decorators.llm import trace_llm
    return trace_llm(model)


def traced_tool(name: str) -> Callable[[F], F]:
    _deprecated("traced_tool", "trace_tool")
    from gernas.decorators.tool import trace_tool
    return trace_tool(name)


def traced_mcp_tool(service: str, tool_name: str) -> Callable[[F], F]:
    _deprecated("traced_mcp_tool", "trace_mcp")
    from gernas.decorators.tool import trace_mcp
    return trace_mcp(service, tool_name=tool_name)


def traced_a2a_hop(target_node: str) -> Callable[[F], F]:
    _deprecated("traced_a2a_hop", "trace_delegation")
    from gernas.decorators.delegation import trace_delegation
    return trace_delegation(target_node)
