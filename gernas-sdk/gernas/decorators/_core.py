"""Shared wrapper engine behind every ``@trace_*`` decorator.

One implementation handles sync functions, coroutines, generators and async
generators, so each decorator only describes *what* to record (a :class:`Spec`).
Guarantees, for every decorator:

* the canonical envelope is set first, then operation attributes;
* every write goes through :func:`gernas.tracer.safe_set` (redaction + isolation);
* a failure inside telemetry code never reaches the wrapped function — if a span
  cannot even be started the function simply runs untraced;
* spans always end — on return, exception, cancellation, generator exhaustion,
  ``close()`` or garbage collection (``finally``-based lifecycle);
* exception messages are PII-scrubbed before they are recorded.
"""
from __future__ import annotations

import asyncio
import functools
import inspect
import time
import traceback
from dataclasses import dataclass, field
from typing import Any, Callable, TypeVar

from opentelemetry import context as otel_context
from opentelemetry import trace
from opentelemetry.trace import SpanKind, Status, StatusCode

from gernas.attributes import Ctx
from gernas.tracer import get_config, get_tracer, safe_set
from gernas.utils.hashing import hash_inputs
from gernas.utils.sanitizer import scrub_pii

F = TypeVar("F", bound=Callable[..., Any])

_KINDS = {"INTERNAL": SpanKind.INTERNAL, "SERVER": SpanKind.SERVER, "CLIENT": SpanKind.CLIENT,
          "PRODUCER": SpanKind.PRODUCER, "CONSUMER": SpanKind.CONSUMER}


@functools.lru_cache(maxsize=4096)
def _param_names(fn: Callable[..., Any]) -> tuple[str, ...]:
    try:
        return tuple(inspect.signature(fn).parameters)
    except (TypeError, ValueError):
        return ()


@dataclass
class Call:
    """One invocation of a decorated function."""
    fn: Callable[..., Any]
    args: tuple
    kwargs: dict
    t0: float = field(default_factory=time.perf_counter)
    state: dict = field(default_factory=dict)

    def elapsed_ms(self) -> float:
        return round((time.perf_counter() - self.t0) * 1000.0, 3)

    def arg(self, name: str, position: int | None = None, default: Any = None) -> Any:
        """Fetch a named argument whether passed by keyword or position."""
        if name in self.kwargs:
            return self.kwargs[name]
        params = _param_names(self.fn)
        if name in params:
            idx = params.index(name)
            if idx < len(self.args):
                return self.args[idx]
        if position is not None and position < len(self.args):
            return self.args[position]
        return default

    def input_hash(self) -> str:
        if "input_hash" in self.state:  # adapters supply the business payload hash directly
            return self.state["input_hash"]
        args = self.args
        # drop a bound `self`/`cls` so the hash reflects the business input only
        if args and hasattr(args[0], "__dict__") and getattr(self.fn, "__qualname__", "").count("."):
            args = args[1:]
        return hash_inputs({"args": [repr(a) for a in args], "kwargs": {k: repr(v) for k, v in self.kwargs.items()}})


class Spec:
    """What a decorator records. Subclasses override the hooks they need."""

    op: str = ""                 # canonical operation (SpanName constant value)
    kind: str = "INTERNAL"
    new_trace: bool = False      # start a fresh trace (never inherit a parent)

    def span_name(self, call: Call) -> str:
        return self.op

    # lifecycle hooks — all guarded by the engine
    def start(self, span: Any, call: Call) -> None: ...
    def success(self, span: Any, call: Call, result: Any) -> None: ...
    def failure(self, span: Any, call: Call, exc: BaseException) -> bool:
        """Record *exc*; return True if the span status must be ERROR."""
        return not isinstance(exc, (asyncio.CancelledError, GeneratorExit))
    def finish(self, span: Any, call: Call, exc: BaseException | None) -> None: ...
    def on_item(self, span: Any, call: Call, item: Any, index: int) -> None: ...
    def on_stream_end(self, span: Any, call: Call, outcome: str) -> None: ...
    # context hooks around each resume of the wrapped code (bind ContextVars)
    def activate(self, span: Any, call: Call) -> Any: return None
    def deactivate(self, call: Call, token: Any) -> None: ...


# ── guarded primitives ───────────────────────────────────────────────────────

def _guard(fn: Callable[..., Any], *args: Any) -> Any:
    try:
        return fn(*args)
    except Exception:  # noqa: BLE001 - telemetry must never break the agent
        return None


def record_exception(span: Any, exc: BaseException) -> None:
    """Record *exc* on *span* with PII-scrubbed message and stack trace."""
    try:
        message = scrub_pii(str(exc))
        stack = scrub_pii("".join(traceback.format_exception(type(exc), exc, exc.__traceback__)))
        span.record_exception(exc, attributes={"exception.message": message, "exception.stacktrace": stack})
        safe_set(span, Ctx.ERROR_TYPE, type(exc).__name__)
    except Exception:  # noqa: BLE001
        pass


def _begin(spec: Spec, call: Call) -> tuple[Any, Any]:
    parent = otel_context.Context() if spec.new_trace else None
    span = get_tracer().start_span(spec.span_name(call), context=parent, kind=_KINDS.get(spec.kind, SpanKind.INTERNAL))
    token = otel_context.attach(trace.set_span_in_context(span))
    try:
        for key, value in get_config().envelope.items():
            safe_set(span, key, value)
        safe_set(span, Ctx.OPERATION, spec.op)
    except Exception:  # noqa: BLE001
        pass
    _guard(spec.start, span, call)
    return span, token


_marked: set[int] = set()


def _end(spec: Spec, span: Any, call: Call, exc: BaseException | None, token: Any) -> None:
    try:
        _guard(spec.finish, span, call, exc)
        if exc is None:
            if id(span) not in _marked:
                _guard(span.set_status, Status(StatusCode.OK))
        else:
            is_error = _guard(spec.failure, span, call, exc)
            if is_error is None:
                is_error = True
            if is_error:
                record_exception(span, exc)
                _guard(span.set_status, Status(StatusCode.ERROR, scrub_pii(f"{type(exc).__name__}: {exc}")[:256]))
            elif id(span) not in _marked:
                _guard(span.set_status, Status(StatusCode.OK))
    finally:
        _marked.discard(id(span))
        _guard(span.end)
        if token is not None:
            try:
                otel_context.detach(token)
            except Exception:  # noqa: BLE001
                pass


def mark_status(span: Any, code: StatusCode, description: str | None = None) -> None:
    """Set an explicit status that the engine will not overwrite at span end."""
    try:
        span.set_status(Status(code, description))
        _marked.add(id(span))
    except Exception:  # noqa: BLE001
        pass


# ── wrappers ─────────────────────────────────────────────────────────────────

def wrap(fn: F, spec: Spec) -> F:
    if inspect.isasyncgenfunction(fn):
        return _wrap_async_gen(fn, spec)  # type: ignore[return-value]
    if inspect.isgeneratorfunction(fn):
        return _wrap_gen(fn, spec)  # type: ignore[return-value]
    if inspect.iscoroutinefunction(fn):
        return _wrap_async(fn, spec)  # type: ignore[return-value]
    return _wrap_sync(fn, spec)  # type: ignore[return-value]


def _wrap_sync(fn: Callable[..., Any], spec: Spec) -> Callable[..., Any]:
    @functools.wraps(fn)
    def wrapper(*args: Any, **kwargs: Any) -> Any:
        call = Call(fn, args, kwargs)
        try:
            span, token = _begin(spec, call)
        except Exception:  # noqa: BLE001
            return fn(*args, **kwargs)
        ctx_token = _guard(spec.activate, span, call)
        exc: BaseException | None = None
        try:
            result = fn(*args, **kwargs)
            _guard(spec.success, span, call, result)
            return result
        except BaseException as e:
            exc = e
            raise
        finally:
            _guard(spec.deactivate, call, ctx_token)
            _end(spec, span, call, exc, token)
    wrapper.__gernas_spec__ = spec  # type: ignore[attr-defined]
    return wrapper


def _wrap_async(fn: Callable[..., Any], spec: Spec) -> Callable[..., Any]:
    @functools.wraps(fn)
    async def wrapper(*args: Any, **kwargs: Any) -> Any:
        call = Call(fn, args, kwargs)
        try:
            span, token = _begin(spec, call)
        except Exception:  # noqa: BLE001
            return await fn(*args, **kwargs)
        ctx_token = _guard(spec.activate, span, call)
        exc: BaseException | None = None
        try:
            result = await fn(*args, **kwargs)
            _guard(spec.success, span, call, result)
            return result
        except BaseException as e:
            exc = e
            raise
        finally:
            _guard(spec.deactivate, call, ctx_token)
            _end(spec, span, call, exc, token)
    wrapper.__gernas_spec__ = spec  # type: ignore[attr-defined]
    return wrapper


def _start_detached(spec: Spec, call: Call) -> Any:
    """Start a span for a generator: made current only while the generator runs."""
    span, token = _begin(spec, call)
    otel_context.detach(token)
    return span


def _wrap_gen(fn: Callable[..., Any], spec: Spec) -> Callable[..., Any]:
    @functools.wraps(fn)
    def wrapper(*args: Any, **kwargs: Any) -> Any:
        call = Call(fn, args, kwargs)
        try:
            span = _start_detached(spec, call)
        except Exception:  # noqa: BLE001
            yield from fn(*args, **kwargs)
            return
        yield from iterate_sync(spec, span, call, fn(*args, **kwargs))
    wrapper.__gernas_spec__ = spec  # type: ignore[attr-defined]
    return wrapper


def _wrap_async_gen(fn: Callable[..., Any], spec: Spec) -> Callable[..., Any]:
    @functools.wraps(fn)
    async def wrapper(*args: Any, **kwargs: Any) -> Any:
        call = Call(fn, args, kwargs)
        try:
            span = _start_detached(spec, call)
        except Exception:  # noqa: BLE001
            async for item in fn(*args, **kwargs):
                yield item
            return
        inner = iterate_async(spec, span, call, fn(*args, **kwargs))
        try:
            async for item in inner:
                yield item
        finally:
            await inner.aclose()  # propagate aclose()/cancellation so the span closes now, not at GC
    wrapper.__gernas_spec__ = spec  # type: ignore[attr-defined]
    return wrapper


def iterate_sync(spec: Spec, span: Any, call: Call, iterator: Any):
    """Drive *iterator* inside *span*; the span closes on every exit path."""
    outcome, exc, index = "cancelled", None, 0
    ctx = trace.set_span_in_context(span)
    it = iter(iterator)
    try:
        while True:
            token = otel_context.attach(ctx)
            ctx_token = _guard(spec.activate, span, call)
            try:
                item = next(it)
            except StopIteration:
                outcome = "completed"
                return
            finally:
                _guard(spec.deactivate, call, ctx_token)
                otel_context.detach(token)
            _guard(spec.on_item, span, call, item, index)
            index += 1
            yield item
    except GeneratorExit:
        outcome = "cancelled"
        raise
    except BaseException as e:
        outcome, exc = "error", e
        raise
    finally:
        close = getattr(it, "close", None)
        if close is not None:
            _guard(close)
        _guard(spec.on_stream_end, span, call, outcome)
        _end(spec, span, call, exc, None)


async def iterate_async(spec: Spec, span: Any, call: Call, iterator: Any):
    outcome, exc, index = "cancelled", None, 0
    ctx = trace.set_span_in_context(span)
    it = iterator.__aiter__()
    try:
        while True:
            token = otel_context.attach(ctx)
            ctx_token = _guard(spec.activate, span, call)
            try:
                item = await it.__anext__()
            except StopAsyncIteration:
                outcome = "completed"
                return
            finally:
                _guard(spec.deactivate, call, ctx_token)
                otel_context.detach(token)
            _guard(spec.on_item, span, call, item, index)
            index += 1
            yield item
    except GeneratorExit:
        outcome = "cancelled"
        raise
    except asyncio.CancelledError as e:
        outcome, exc = "cancelled", e
        raise
    except BaseException as e:
        outcome, exc = "error", e
        raise
    finally:
        aclose = getattr(it, "aclose", None)
        if aclose is not None and outcome != "completed":
            try:
                await aclose()
            except BaseException:  # noqa: BLE001
                pass
        _guard(spec.on_stream_end, span, call, outcome)
        _end(spec, span, call, exc, None)


def decorator(spec_factory: Callable[[], Spec]) -> Callable[[F], F]:
    """Build a decorator that instantiates a fresh Spec per decorated function."""
    def apply(fn: F) -> F:
        return wrap(fn, spec_factory())
    return apply


def as_list(value: Any) -> list | None:
    if value is None:
        return None
    if isinstance(value, (list, tuple)):
        return list(value)
    for attr in ("results", "documents", "items", "data", "points", "hits"):
        inner = getattr(value, attr, None) if not isinstance(value, dict) else value.get(attr)
        if isinstance(inner, (list, tuple)):
            return list(inner)
    return None


def get_field(obj: Any, *names: str, default: Any = None) -> Any:
    """Duck-typed lookup across attributes and mapping keys."""
    for name in names:
        if obj is None:
            return default
        if isinstance(obj, dict):
            if name in obj and obj[name] is not None:
                return obj[name]
        else:
            value = getattr(obj, name, None)
            if value is not None:
                return value
    return default


def resolve(value: Any, call: Call, *extra: Any) -> Any:
    """Static value, or callable(call.args, call.kwargs) / callable(result)."""
    if callable(value):
        try:
            return value(*extra) if extra else value(*call.args, **call.kwargs)
        except Exception:  # noqa: BLE001
            return None
    return value
