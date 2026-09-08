"""Agent-layer decorators — Layer 2 of beacon_kit.

@traced_agent  — wraps an agent invocation (SpanKind.SERVER)
@traced_llm_call — wraps an LLM API call (SpanKind.CLIENT)
@traced_a2a_hop  — wraps a cross-process A2A call with W3C context injection (SpanKind.CLIENT)
@traced_mcp_tool — wraps an MCP tool invocation (SpanKind.CLIENT)

Raw inputs/outputs are never stored — only hashes and structural summaries.
"""
from __future__ import annotations

import asyncio
import functools
import time
from typing import Any, Callable, TypeVar

from opentelemetry import trace, propagate
from opentelemetry.trace import SpanKind, Status, StatusCode

from beacon_kit.utils.hashing import hash_inputs, summarize_output
from beacon_kit.utils.sanitizer import scrub_pii

F = TypeVar("F", bound=Callable[..., Any])


def traced_agent(name: str, attributes: dict | None = None) -> Callable[[F], F]:
    """Wrap an agent entry-point in a SERVER span.

    Captures agent.name, agent.input_hash, agent.output_hash, agent.latency_ms.
    If the return value contains a dict with token_usage keys they are recorded
    as agent.token_usage.input_tokens / agent.token_usage.output_tokens.
    """
    def decorator(fn: F) -> F:
        tracer = trace.get_tracer(__name__)
        span_name = f"agent.{name}"

        def _record_result(span: Any, result: Any, latency_ms: float) -> None:
            span.set_attribute("agent.output_hash", hash_inputs(result))
            span.set_attribute("agent.latency_ms", latency_ms)
            if isinstance(result, dict):
                usage = result.get("token_usage") or result.get("usage") or {}
                if usage:
                    for k in ("input_tokens", "output_tokens", "total_tokens"):
                        if k in usage:
                            span.set_attribute(f"agent.token_usage.{k}", usage[k])

        if asyncio.iscoroutinefunction(fn):
            @functools.wraps(fn)
            async def async_wrapper(*args: Any, **kwargs: Any) -> Any:
                start = time.perf_counter()
                with tracer.start_as_current_span(span_name, kind=SpanKind.SERVER) as span:
                    span.set_attribute("agent.name", name)
                    span.set_attribute("agent.input_hash", hash_inputs({"args": str(args), "kwargs": str(kwargs)}))
                    for k, v in (attributes or {}).items():
                        span.set_attribute(k, v)
                    try:
                        result = await fn(*args, **kwargs)
                        _record_result(span, result, round((time.perf_counter() - start) * 1000, 2))
                        return result
                    except Exception as exc:
                        scrubbed = scrub_pii(str(exc))
                        span.record_exception(Exception(scrubbed))
                        span.set_status(Status(StatusCode.ERROR, scrubbed))
                        span.set_attribute("agent.latency_ms", round((time.perf_counter() - start) * 1000, 2))
                        raise
            return async_wrapper  # type: ignore[return-value]
        else:
            @functools.wraps(fn)
            def sync_wrapper(*args: Any, **kwargs: Any) -> Any:
                start = time.perf_counter()
                with tracer.start_as_current_span(span_name, kind=SpanKind.SERVER) as span:
                    span.set_attribute("agent.name", name)
                    span.set_attribute("agent.input_hash", hash_inputs({"args": str(args), "kwargs": str(kwargs)}))
                    for k, v in (attributes or {}).items():
                        span.set_attribute(k, v)
                    try:
                        result = fn(*args, **kwargs)
                        _record_result(span, result, round((time.perf_counter() - start) * 1000, 2))
                        return result
                    except Exception as exc:
                        scrubbed = scrub_pii(str(exc))
                        span.record_exception(Exception(scrubbed))
                        span.set_status(Status(StatusCode.ERROR, scrubbed))
                        span.set_attribute("agent.latency_ms", round((time.perf_counter() - start) * 1000, 2))
                        raise
            return sync_wrapper  # type: ignore[return-value]

    return decorator


def traced_llm_call(
    model: str | Callable[[Any], str] | None = None,
    attribute_keys: list[str] | None = None,
) -> Callable[[F], F]:
    """Wrap an LLM API call in a CLIENT span.

    model: literal model name string, or a callable that extracts it from the result.
    attribute_keys: named kwargs to extract as additional span attributes.

    Captures llm.model, llm.prompt_hash, llm.completion_hash,
    llm.input_tokens, llm.output_tokens, llm.latency_ms.
    """
    def decorator(fn: F) -> F:
        tracer = trace.get_tracer(__name__)
        span_name = "llm.call"

        def _record_llm_result(span: Any, result: Any, resolved_model: str, latency_ms: float) -> None:
            span.set_attribute("llm.model", resolved_model)
            span.set_attribute("llm.completion_hash", hash_inputs(result))
            span.set_attribute("llm.latency_ms", latency_ms)
            if isinstance(result, dict):
                usage = result.get("usage") or result.get("token_usage") or {}
                if usage:
                    for k in ("input_tokens", "output_tokens", "total_tokens",
                              "prompt_tokens", "completion_tokens"):
                        if k in usage:
                            span.set_attribute(f"llm.{k}", usage[k])

        def _resolve_model(result: Any) -> str:
            if callable(model):
                try:
                    return str(model(result))
                except Exception:
                    return "unknown"
            return model or "unknown"

        if asyncio.iscoroutinefunction(fn):
            @functools.wraps(fn)
            async def async_wrapper(*args: Any, **kwargs: Any) -> Any:
                start = time.perf_counter()
                with tracer.start_as_current_span(span_name, kind=SpanKind.CLIENT) as span:
                    span.set_attribute("llm.prompt_hash", hash_inputs({"args": str(args), "kwargs": str(kwargs)}))
                    if attribute_keys:
                        for key in attribute_keys:
                            if key in kwargs:
                                span.set_attribute(f"llm.{key}", str(kwargs[key]))
                    try:
                        result = await fn(*args, **kwargs)
                        _record_llm_result(span, result, _resolve_model(result), round((time.perf_counter() - start) * 1000, 2))
                        return result
                    except Exception as exc:
                        scrubbed = scrub_pii(str(exc))
                        span.record_exception(Exception(scrubbed))
                        span.set_status(Status(StatusCode.ERROR, scrubbed))
                        span.set_attribute("llm.latency_ms", round((time.perf_counter() - start) * 1000, 2))
                        raise
            return async_wrapper  # type: ignore[return-value]
        else:
            @functools.wraps(fn)
            def sync_wrapper(*args: Any, **kwargs: Any) -> Any:
                start = time.perf_counter()
                with tracer.start_as_current_span(span_name, kind=SpanKind.CLIENT) as span:
                    span.set_attribute("llm.prompt_hash", hash_inputs({"args": str(args), "kwargs": str(kwargs)}))
                    if attribute_keys:
                        for key in attribute_keys:
                            if key in kwargs:
                                span.set_attribute(f"llm.{key}", str(kwargs[key]))
                    try:
                        result = fn(*args, **kwargs)
                        _record_llm_result(span, result, _resolve_model(result), round((time.perf_counter() - start) * 1000, 2))
                        return result
                    except Exception as exc:
                        scrubbed = scrub_pii(str(exc))
                        span.record_exception(Exception(scrubbed))
                        span.set_status(Status(StatusCode.ERROR, scrubbed))
                        span.set_attribute("llm.latency_ms", round((time.perf_counter() - start) * 1000, 2))
                        raise
            return sync_wrapper  # type: ignore[return-value]

    return decorator


def traced_a2a_hop(target_node: str) -> Callable[[F], F]:
    """Wrap an A2A (agent-to-agent) outbound call in a CLIENT span.

    Injects W3C traceparent/baggage into the first dict argument or a 'carrier'
    kwarg so the remote node picks up the trace context.

    Captures a2a.target_node, a2a.latency_ms, a2a.result_hash.
    """
    def decorator(fn: F) -> F:
        tracer = trace.get_tracer(__name__)
        span_name = f"a2a.{target_node}"

        def _inject_context(args: tuple, kwargs: dict) -> None:
            carrier: dict | None = kwargs.get("carrier") or kwargs.get("headers")
            if carrier is None:
                for arg in args:
                    if isinstance(arg, dict):
                        carrier = arg
                        break
            if carrier is not None:
                propagate.inject(carrier)

        if asyncio.iscoroutinefunction(fn):
            @functools.wraps(fn)
            async def async_wrapper(*args: Any, **kwargs: Any) -> Any:
                start = time.perf_counter()
                with tracer.start_as_current_span(span_name, kind=SpanKind.CLIENT) as span:
                    span.set_attribute("a2a.target_node", target_node)
                    _inject_context(args, kwargs)
                    try:
                        result = await fn(*args, **kwargs)
                        span.set_attribute("a2a.result_hash", hash_inputs(result))
                        span.set_attribute("a2a.latency_ms", round((time.perf_counter() - start) * 1000, 2))
                        return result
                    except Exception as exc:
                        scrubbed = scrub_pii(str(exc))
                        span.record_exception(Exception(scrubbed))
                        span.set_status(Status(StatusCode.ERROR, scrubbed))
                        span.set_attribute("a2a.latency_ms", round((time.perf_counter() - start) * 1000, 2))
                        raise
            return async_wrapper  # type: ignore[return-value]
        else:
            @functools.wraps(fn)
            def sync_wrapper(*args: Any, **kwargs: Any) -> Any:
                start = time.perf_counter()
                with tracer.start_as_current_span(span_name, kind=SpanKind.CLIENT) as span:
                    span.set_attribute("a2a.target_node", target_node)
                    _inject_context(args, kwargs)
                    try:
                        result = fn(*args, **kwargs)
                        span.set_attribute("a2a.result_hash", hash_inputs(result))
                        span.set_attribute("a2a.latency_ms", round((time.perf_counter() - start) * 1000, 2))
                        return result
                    except Exception as exc:
                        scrubbed = scrub_pii(str(exc))
                        span.record_exception(Exception(scrubbed))
                        span.set_status(Status(StatusCode.ERROR, scrubbed))
                        span.set_attribute("a2a.latency_ms", round((time.perf_counter() - start) * 1000, 2))
                        raise
            return sync_wrapper  # type: ignore[return-value]

    return decorator


def traced_mcp_tool(service: str, tool_name: str) -> Callable[[F], F]:
    """Wrap an MCP tool invocation in a CLIENT span.

    Captures mcp.service, mcp.tool_name, mcp.input_hash, mcp.result_hash, mcp.latency_ms.
    """
    def decorator(fn: F) -> F:
        tracer = trace.get_tracer(__name__)
        span_name = f"mcp.{service}.{tool_name}"

        if asyncio.iscoroutinefunction(fn):
            @functools.wraps(fn)
            async def async_wrapper(*args: Any, **kwargs: Any) -> Any:
                start = time.perf_counter()
                with tracer.start_as_current_span(span_name, kind=SpanKind.CLIENT) as span:
                    span.set_attribute("mcp.service", service)
                    span.set_attribute("mcp.tool_name", tool_name)
                    span.set_attribute("mcp.input_hash", hash_inputs({"args": str(args), "kwargs": str(kwargs)}))
                    try:
                        result = await fn(*args, **kwargs)
                        span.set_attribute("mcp.result_hash", hash_inputs(result))
                        span.set_attribute("mcp.latency_ms", round((time.perf_counter() - start) * 1000, 2))
                        return result
                    except Exception as exc:
                        scrubbed = scrub_pii(str(exc))
                        span.record_exception(Exception(scrubbed))
                        span.set_status(Status(StatusCode.ERROR, scrubbed))
                        span.set_attribute("mcp.latency_ms", round((time.perf_counter() - start) * 1000, 2))
                        raise
            return async_wrapper  # type: ignore[return-value]
        else:
            @functools.wraps(fn)
            def sync_wrapper(*args: Any, **kwargs: Any) -> Any:
                start = time.perf_counter()
                with tracer.start_as_current_span(span_name, kind=SpanKind.CLIENT) as span:
                    span.set_attribute("mcp.service", service)
                    span.set_attribute("mcp.tool_name", tool_name)
                    span.set_attribute("mcp.input_hash", hash_inputs({"args": str(args), "kwargs": str(kwargs)}))
                    try:
                        result = fn(*args, **kwargs)
                        span.set_attribute("mcp.result_hash", hash_inputs(result))
                        span.set_attribute("mcp.latency_ms", round((time.perf_counter() - start) * 1000, 2))
                        return result
                    except Exception as exc:
                        scrubbed = scrub_pii(str(exc))
                        span.record_exception(Exception(scrubbed))
                        span.set_status(Status(StatusCode.ERROR, scrubbed))
                        span.set_attribute("mcp.latency_ms", round((time.perf_counter() - start) * 1000, 2))
                        raise
            return sync_wrapper  # type: ignore[return-value]

    return decorator
