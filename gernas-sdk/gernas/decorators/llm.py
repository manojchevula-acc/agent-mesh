"""@trace_llm and @trace_stream — model calls (WP1-CORE-02a/02b, CORE-04, CORE-05).

Response introspection is duck-typed so one decorator handles OpenAI / Azure
OpenAI (``usage.prompt_tokens``), Anthropic (``usage.input_tokens``), Microsoft
Agent Framework (``usage_details["input_token_count"]``), LangChain
(``usage_metadata``) and raw JSON dicts. Prompt and completion text are never
recorded (PDPL); prompt provenance is recorded as template id + version + SHA-256.
"""
from __future__ import annotations

import inspect
from typing import Any, Callable, TypeVar

from opentelemetry.trace import StatusCode

from gernas.attributes import Ctx, Metric, SpanName
from gernas.cost import estimate_cost_aed
from gernas.decorators._core import (
    Call, Spec, _start_detached, get_field, iterate_async, iterate_sync, mark_status, resolve, wrap,
)
from gernas.metrics.agentic import instruments, record
from gernas.prompt_registry import record_prompt
from gernas.run_state import current_run
from gernas.tracer import get_config, safe_set

F = TypeVar("F", bound=Callable[..., Any])

_USAGE = ("usage", "usage_details", "token_usage", "usage_metadata")
_IN = ("prompt_tokens", "input_tokens", "input_token_count", "prompt_token_count")
_OUT = ("completion_tokens", "output_tokens", "output_token_count", "candidates_token_count")


def extract_usage(result: Any) -> tuple[int | None, int | None]:
    usage = get_field(result, *_USAGE)
    if usage is None and isinstance(result, dict):
        usage = result if any(k in result for k in _IN + _OUT) else None
    if usage is None:
        return None, None
    i, o = get_field(usage, *_IN), get_field(usage, *_OUT)
    return (int(i) if isinstance(i, (int, float)) else None, int(o) if isinstance(o, (int, float)) else None)


def extract_finish_reason(result: Any) -> str | None:
    choices = get_field(result, "choices")
    if isinstance(choices, (list, tuple)) and choices:
        reason = get_field(choices[0], "finish_reason")
        if reason is not None:
            return _enum_str(reason)
    reason = get_field(result, "finish_reason", "stop_reason")
    if reason is None:
        meta = get_field(result, "response_metadata")
        reason = get_field(meta, "finish_reason", "stop_reason")
    return _enum_str(reason) if reason is not None else None


def _enum_str(value: Any) -> str:
    return str(getattr(value, "value", value))


class LLMSpec(Spec):
    op = SpanName.LLM
    kind = "CLIENT"

    def __init__(self, model: Any, provider: str | None, deployment_id: str | None, operation: str,
                 prompt_template_id: str | None, prompt_version: str | None, prompt_arg: str | None,
                 usage_from: Callable[[Any], tuple] | None, model_from: Callable[[Any], str] | None) -> None:
        if callable(model) and model_from is None:  # Beacon Kit compatibility: model=callable(result)
            model_from, model = model, None
        self.model, self.provider, self.deployment_id = model, provider, deployment_id
        self.operation = operation
        self.prompt_template_id, self.prompt_version, self.prompt_arg = prompt_template_id, prompt_version, prompt_arg
        self.usage_from, self.model_from = usage_from, model_from

    def _model(self, call: Call) -> str:
        model = self.model or call.arg("model") or call.arg("model_id")
        return str(model) if model else "unknown"

    def start(self, span: Any, call: Call) -> None:
        model = self._model(call)
        call.state["model"] = model
        provider = self.provider or get_config().llm_provider or "unknown"
        call.state["provider"] = provider
        safe_set(span, Ctx.MODEL_ID, model)
        safe_set(span, Ctx.PROVIDER, provider)
        safe_set(span, Ctx.DEPLOYMENT_ID, self.deployment_id or model)
        safe_set(span, Ctx.OPERATION_NAME, self.operation)
        if self.prompt_template_id:
            text = call.arg(self.prompt_arg) if self.prompt_arg else None
            record_prompt(span, self.prompt_template_id, self.prompt_version, None if text is None else str(text))
        run = current_run()
        if run is not None:
            run.record_step()

    def success(self, span: Any, call: Call, result: Any) -> None:
        in_tok, out_tok = (resolve(self.usage_from, call, result) or (None, None)) if self.usage_from \
            else extract_usage(result)
        record_usage(span, call.state["model"], call.state["provider"], in_tok, out_tok)
        finish = extract_finish_reason(result)
        if finish:
            safe_set(span, Ctx.FINISH_REASON, [finish])
        response_model = (resolve(self.model_from, call, result) if self.model_from
                          else get_field(result, "model", "model_id", "response_model"))
        if isinstance(response_model, str):
            safe_set(span, Ctx.RESPONSE_MODEL, response_model)

    def finish(self, span: Any, call: Call, exc: BaseException | None) -> None:
        latency = call.elapsed_ms()
        safe_set(span, Ctx.GENERATION_LATENCY_MS, latency)
        record(Metric.LLM_CALLS, {Ctx.MODEL_ID: call.state.get("model", "unknown"),
                                  Ctx.PROVIDER: call.state.get("provider", "unknown"),
                                  "result": "error" if exc else "success"},
               histogram=Metric.LLM_DURATION, latency_ms=latency)


def record_usage(span: Any, model: str, provider: str, in_tok: int | None, out_tok: int | None) -> None:
    """Token counts + rate-card cost on *span* and the token/cost metrics."""
    safe_set(span, Ctx.INPUT_TOKENS, in_tok)
    safe_set(span, Ctx.OUTPUT_TOKENS, out_tok)
    dims = {Ctx.MODEL_ID: model, Ctx.PROVIDER: provider}
    try:
        tokens = instruments().counter(Metric.LLM_TOKENS)
        if in_tok:
            tokens.add(in_tok, {**dims, "direction": "input"})
        if out_tok:
            tokens.add(out_tok, {**dims, "direction": "output"})
    except Exception:  # noqa: BLE001
        pass
    priced = estimate_cost_aed(model, provider, in_tok, out_tok, get_config().rate_card_path or None)
    if priced is not None:
        cost, version = priced
        safe_set(span, Ctx.EST_COST_AED, cost)
        safe_set(span, Ctx.RATE_CARD_VERSION, version)
        record(Metric.LLM_COST, dims, n=cost)


def trace_llm(model: str | Callable[[Any], str] | None = None, *, provider: str | None = None,
              deployment_id: str | None = None, operation: str = "chat",
              prompt_template_id: str | None = None, prompt_version: str | None = None,
              prompt_arg: str | None = None, usage_from: Callable[[Any], tuple] | None = None,
              model_from: Callable[[Any], str] | None = None) -> Callable[[F], F]:
    """Trace a model call as ``llm.call``.

    ``model`` defaults to a ``model``/``model_id`` argument of the call. Pass
    ``prompt_template_id``/``prompt_version`` (+ ``prompt_arg`` naming the argument
    that holds the prompt text) to record prompt provenance.
    """
    def decorator(fn: F) -> F:
        return wrap(fn, LLMSpec(model, provider, deployment_id, operation, prompt_template_id,
                                prompt_version, prompt_arg, usage_from, model_from))
    return decorator


# ── streaming ────────────────────────────────────────────────────────────────

class StreamSpec(LLMSpec):
    op = SpanName.STREAM

    def __init__(self, *args: Any, token_count_from: Callable[[Any], int | None] | None = None, **kw: Any) -> None:
        super().__init__(*args, **kw)
        self.token_count_from = token_count_from

    def success(self, span: Any, call: Call, result: Any) -> None:  # stream result handled per chunk
        pass

    def on_item(self, span: Any, call: Call, item: Any, index: int) -> None:
        st = call.state
        if index == 0:
            st["ttft"] = call.elapsed_ms()
            safe_set(span, Ctx.TTFT_MS, st["ttft"])
        st["chunks"] = index + 1
        in_tok, out_tok = extract_usage(item)
        if out_tok is not None:  # final usage chunk (OpenAI include_usage, Anthropic message_delta)
            st["usage"] = (in_tok, out_tok)
        elif self.token_count_from is not None:
            n = resolve(self.token_count_from, call, item)
            if isinstance(n, int):
                st["tokens"] = st.get("tokens", 0) + n
        finish = extract_finish_reason(item)
        if finish:
            st["finish"] = finish

    def on_stream_end(self, span: Any, call: Call, outcome: str) -> None:
        st = call.state
        chunks = st.get("chunks", 0)
        safe_set(span, Ctx.STREAM_OUTCOME, outcome)
        safe_set(span, Ctx.STREAM_CHUNKS, chunks)
        in_tok, out_tok = st.get("usage", (None, st.get("tokens")))
        if in_tok is not None or out_tok is not None:
            record_usage(span, st.get("model", "unknown"), st.get("provider", "unknown"), in_tok, out_tok)
        if st.get("finish"):
            safe_set(span, Ctx.FINISH_REASON, [st["finish"]])
        if outcome == "completed" and chunks:
            elapsed_s = max(call.elapsed_ms() - st.get("ttft", 0.0), 1e-3) / 1000.0
            produced = out_tok if out_tok else chunks  # chunk count is the proxy when no usage is reported
            safe_set(span, Ctx.TOKENS_PER_SEC, round(produced / elapsed_s, 3))
        if outcome == "cancelled":
            safe_set(span, Ctx.ERROR_CATEGORY, "cancelled")
            mark_status(span, StatusCode.UNSET)  # OTel has no CANCELLED code; outcome attribute carries it


def trace_stream(model: str | None = None, *, provider: str | None = None, deployment_id: str | None = None,
                 token_count_from: Callable[[Any], int | None] | None = None,
                 prompt_template_id: str | None = None, prompt_version: str | None = None,
                 prompt_arg: str | None = None) -> Callable[[F], F]:
    """Trace a streaming model call as ``llm.stream``.

    Works on generator / async-generator functions and on functions (sync or
    async) that *return* an iterator. The span opens before the first chunk and
    closes in a ``finally`` — on exhaustion, error, ``close()``/client disconnect
    or garbage collection. Records TTFT, chunk count, throughput and the outcome
    (``completed`` / ``cancelled`` / ``error``).
    """
    def decorator(fn: F) -> F:
        def spec() -> StreamSpec:
            return StreamSpec(model, provider, deployment_id, "chat", prompt_template_id, prompt_version,
                              prompt_arg, None, None, token_count_from=token_count_from)
        if inspect.isasyncgenfunction(fn) or inspect.isgeneratorfunction(fn):
            return wrap(fn, spec())
        return _wrap_returning_iterator(fn, spec())  # type: ignore[return-value]
    return decorator


def _wrap_returning_iterator(fn: Callable[..., Any], spec: StreamSpec) -> Callable[..., Any]:
    import functools

    def _wrap_result(call: Call, result: Any) -> Any:
        span = _start_detached(spec, call)
        if hasattr(result, "__aiter__"):
            return iterate_async(spec, span, call, result)
        if hasattr(result, "__iter__") and not isinstance(result, (str, bytes, dict)):
            return iterate_sync(spec, span, call, result)
        # not a stream after all: close the span as a single call
        from gernas.decorators._core import _end
        _end(spec, span, call, None, None)
        return result

    if inspect.iscoroutinefunction(fn):
        @functools.wraps(fn)
        async def async_wrapper(*args: Any, **kwargs: Any) -> Any:
            call = Call(fn, args, kwargs)
            result = await fn(*args, **kwargs)
            try:
                return _wrap_result(call, result)
            except Exception:  # noqa: BLE001
                return result
        return async_wrapper

    @functools.wraps(fn)
    def sync_wrapper(*args: Any, **kwargs: Any) -> Any:
        call = Call(fn, args, kwargs)
        result = fn(*args, **kwargs)
        try:
            return _wrap_result(call, result)
        except Exception:  # noqa: BLE001
            return result
    return sync_wrapper
