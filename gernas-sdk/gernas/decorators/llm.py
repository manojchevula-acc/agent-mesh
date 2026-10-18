"""@trace_llm and @trace_embedding — model calls (Blueprint §2 AI / prompt / cost
context, §3 LLM Gateway / LLMs / Embedding Services, §6 LLM metrics).

One ``llm.call`` span per model call, streaming or not. Generator and
async-generator functions (or ``stream=True`` for functions returning an iterator)
add TTFT and tokens/second. Usage is read duck-typed from OpenAI / Azure OpenAI,
Anthropic, Microsoft Agent Framework, LangChain and raw JSON responses. Prompt and
completion text are never recorded.
"""

from __future__ import annotations

import functools
import inspect
from collections.abc import Callable
from typing import Any, TypeVar

from opentelemetry import trace
from opentelemetry.trace import StatusCode

from gernas._metrics import observe, record
from gernas.attributes import Ctx, Metric, SpanName
from gernas.cost import estimate_cost
from gernas.decorators._core import (
    Call,
    Spec,
    _end,
    _start_detached,
    get_field,
    iterate_async,
    iterate_sync,
    mark_status,
    resolve,
    wrap,
)
from gernas.run_state import current_run
from gernas.tracer import get_config, safe_set

F = TypeVar("F", bound=Callable[..., Any])

_USAGE = ("usage", "usage_details", "token_usage", "usage_metadata")
_IN = ("prompt_tokens", "input_tokens", "input_token_count", "prompt_token_count")
_OUT = ("completion_tokens", "output_tokens", "output_token_count", "candidates_token_count")
_CACHED = ("cache_read_input_tokens", "cache_read_input_token_count", "cached_tokens")


def _int(v: Any) -> int | None:
    return int(v) if isinstance(v, (int, float)) and not isinstance(v, bool) else None


def extract_usage(result: Any) -> tuple[int | None, int | None, int | None]:
    """``(input, output, cached)`` token counts from any supported response shape."""
    usage = get_field(result, *_USAGE)
    if usage is None and isinstance(result, dict) and any(k in result for k in _IN + _OUT):
        usage = result
    if usage is None:
        return None, None, None
    cached = get_field(usage, *_CACHED)
    if cached is None:
        cached = get_field(
            get_field(usage, "prompt_tokens_details", "input_token_details"), "cached_tokens", "cache_read"
        )
    return _int(get_field(usage, *_IN)), _int(get_field(usage, *_OUT)), _int(cached)


def extract_stop_reason(result: Any) -> str | None:
    choices = get_field(result, "choices")
    if isinstance(choices, (list, tuple)) and choices:
        reason = get_field(choices[0], "finish_reason")
        if reason is not None:
            return str(getattr(reason, "value", reason))
    reason = get_field(result, "finish_reason", "stop_reason")
    if reason is None:
        reason = get_field(get_field(result, "response_metadata"), "finish_reason", "stop_reason")
    return None if reason is None else str(getattr(reason, "value", reason))


def record_usage(
    span: Any,
    provider: str | None,
    model_id: str | None,
    op: str,
    token_input: int | None,
    token_output: int | None,
    token_cached: int | None = None,
) -> None:
    """Blueprint §2 cost context on *span* plus the token / cost metrics."""
    safe_set(span, Ctx.TOKEN_INPUT, token_input)
    safe_set(span, Ctx.TOKEN_OUTPUT, token_output)
    safe_set(span, Ctx.TOKEN_CACHED, token_cached)
    dims = {Ctx.PROVIDER: provider, Ctx.MODEL_ID: model_id, Ctx.OPERATION: op}
    for kind, n in (("input", token_input), ("output", token_output), ("cached", token_cached)):
        if n:
            record(Metric.LLM_TOKENS, {**dims, Ctx.TOKEN_TYPE: kind}, n=n)
    cfg = get_config()
    cost = estimate_cost(model_id, provider, token_input, token_output, cfg.rate_card_path if cfg else "")
    if cost is not None:
        safe_set(span, Ctx.ESTIMATED_COST, cost)
        record(Metric.LLM_ESTIMATED_COST, dims, n=cost)


def mark_fallback() -> None:
    """Mark the current model / tool call as served by a fallback (Blueprint §5 Fallback Rate)."""
    span = trace.get_current_span()
    safe_set(span, Ctx.FALLBACK_USED, True)
    run = current_run()
    op = (getattr(span, "attributes", None) or {}).get(Ctx.OPERATION)
    record(Metric.FALLBACKS, {Ctx.AGENT_ID: run.agent_id if run else None, Ctx.OPERATION: op})


class _ModelSpec(Spec):
    kind = "CLIENT"

    def __init__(self, model_id: Any, provider: str | None, deployment_id: str | None) -> None:
        self.model_id, self.provider, self.deployment_id = model_id, provider, deployment_id

    def _model(self, call: Call) -> str | None:
        model = resolve(self.model_id, call) if callable(self.model_id) else self.model_id
        model = model or call.arg("model") or call.arg("model_id")
        return None if model is None else str(model)

    def start(self, span: Any, call: Call) -> None:
        call.state["model"], call.state["provider"] = self._model(call), self.provider
        safe_set(span, Ctx.PROVIDER, self.provider)
        safe_set(span, Ctx.MODEL_ID, call.state["model"])
        safe_set(span, Ctx.DEPLOYMENT_ID, self.deployment_id)
        run = current_run()
        if run is not None:
            run.record_step()

    def finish(self, span: Any, call: Call, exc: BaseException | None) -> None:
        from gernas.run_state import error_category

        record(
            Metric.LLM_CALLS,
            {
                Ctx.PROVIDER: call.state.get("provider"),
                Ctx.MODEL_ID: call.state.get("model"),
                Ctx.OPERATION: self.op,
                Ctx.EXECUTION_STATUS: "error" if exc else "ok",
                Ctx.ERROR_CATEGORY: error_category(exc) if exc else None,
            },
            histogram=Metric.LLM_DURATION,
            duration_ms=call.elapsed_ms(),
        )


class LLMSpec(_ModelSpec):
    op = SpanName.LLM

    def __init__(
        self,
        model_id: Any,
        provider: str | None,
        deployment_id: str | None,
        prompt_template_id: str | None,
        prompt_version: str | None,
        usage_from: Callable[[Any], tuple] | None,
    ) -> None:
        super().__init__(model_id, provider, deployment_id)
        self.prompt_template_id, self.prompt_version, self.usage_from = prompt_template_id, prompt_version, usage_from

    def start(self, span: Any, call: Call) -> None:
        super().start(span, call)
        safe_set(span, Ctx.PROMPT_TEMPLATE_ID, self.prompt_template_id)
        safe_set(span, Ctx.PROMPT_VERSION, None if self.prompt_version is None else str(self.prompt_version))

    def success(self, span: Any, call: Call, result: Any) -> None:
        if self.usage_from is not None:
            usage = resolve(self.usage_from, call, result) or ()
            usage = (tuple(usage) + (None, None, None))[:3]
        else:
            usage = extract_usage(result)
        record_usage(span, call.state["provider"], call.state["model"], self.op, *usage)
        safe_set(span, Ctx.STOP_REASON, extract_stop_reason(result))
        version = get_field(result, "model", "model_version")
        if isinstance(version, str) and version != call.state["model"]:
            safe_set(span, Ctx.MODEL_VERSION, version)

    # streaming
    def on_item(self, span: Any, call: Call, item: Any, index: int) -> None:
        st = call.state
        if index == 0:
            st["ttft"] = call.elapsed_ms()
            safe_set(span, Ctx.TTFT_MS, st["ttft"])
        st["chunks"] = index + 1
        usage = extract_usage(item)
        if usage[1] is not None:
            st["usage"] = usage
        reason = extract_stop_reason(item)
        if reason:
            st["stop"] = reason

    def on_stream_end(self, span: Any, call: Call, outcome: str) -> None:
        st = call.state
        usage = st.get("usage", (None, None, None))
        if any(u is not None for u in usage):
            record_usage(span, st.get("provider"), st.get("model"), self.op, *usage)
        safe_set(span, Ctx.STOP_REASON, st.get("stop"))
        produced = usage[1] or st.get("chunks", 0)
        if outcome == "completed" and produced:
            seconds = max(call.elapsed_ms() - st.get("ttft", 0.0), 1e-3) / 1000.0
            tps = round(produced / seconds, 3)
            safe_set(span, Ctx.TOKENS_PER_SECOND, tps)
            observe(
                Metric.LLM_TOKENS_PER_SECOND, tps, {Ctx.PROVIDER: st.get("provider"), Ctx.MODEL_ID: st.get("model")}
            )
        if "ttft" in st:
            observe(Metric.LLM_TTFT, st["ttft"], {Ctx.PROVIDER: st.get("provider"), Ctx.MODEL_ID: st.get("model")})
        if outcome == "cancelled":
            st["execution_status"] = "cancelled"
            safe_set(span, Ctx.EXECUTION_STATUS, "cancelled")
            safe_set(span, Ctx.ERROR_CATEGORY, "cancelled")
            mark_status(span, StatusCode.UNSET)  # neither OK nor ERROR; OTel has no CANCELLED code


def trace_llm(
    model_id: str | Callable[..., str] | None = None,
    *,
    provider: str | None = None,
    deployment_id: str | None = None,
    prompt_template_id: str | None = None,
    prompt_version: str | None = None,
    usage_from: Callable[[Any], tuple] | None = None,
    stream: bool = False,
) -> Callable[[F], F]:
    """Trace a model call as ``llm.call``.

    ``model_id`` defaults to a ``model`` / ``model_id`` argument of the call.
    Generator functions are traced as streams automatically; use ``stream=True`` for a
    function that *returns* an iterator. ``usage_from(result)`` may return
    ``(input, output[, cached])`` for response shapes the SDK does not recognise.
    """

    def decorator(fn: F) -> F:
        spec = LLMSpec(model_id, provider, deployment_id, prompt_template_id, prompt_version, usage_from)
        if stream and not (inspect.isasyncgenfunction(fn) or inspect.isgeneratorfunction(fn)):
            return _wrap_returning_iterator(fn, spec)  # type: ignore[return-value]
        return wrap(fn, spec)

    return decorator


def _wrap_returning_iterator(fn: Callable[..., Any], spec: LLMSpec) -> Callable[..., Any]:
    def _traced(call: Call, result: Any) -> Any:
        span = _start_detached(spec, call)
        if hasattr(result, "__aiter__"):
            return iterate_async(spec, span, call, result)
        if hasattr(result, "__iter__") and not isinstance(result, (str, bytes, dict)):
            return iterate_sync(spec, span, call, result)
        spec.success(span, call, result)
        _end(spec, span, call, None, None)
        return result

    if inspect.iscoroutinefunction(fn):

        @functools.wraps(fn)
        async def async_wrapper(*args: Any, **kwargs: Any) -> Any:
            call = Call(fn, args, kwargs)
            result = await fn(*args, **kwargs)
            try:
                return _traced(call, result)
            except Exception:  # noqa: BLE001
                return result

        return async_wrapper

    @functools.wraps(fn)
    def sync_wrapper(*args: Any, **kwargs: Any) -> Any:
        call = Call(fn, args, kwargs)
        result = fn(*args, **kwargs)
        try:
            return _traced(call, result)
        except Exception:  # noqa: BLE001
            return result

    return sync_wrapper


class EmbeddingSpec(_ModelSpec):
    op = SpanName.EMBEDDING

    def __init__(self, model_id: Any, provider: str | None, deployment_id: str | None, input_arg: str) -> None:
        super().__init__(model_id, provider, deployment_id)
        self.input_arg = input_arg

    def start(self, span: Any, call: Call) -> None:
        super().start(span, call)
        texts = call.arg(self.input_arg)
        if texts is None:
            texts = next((a for a in call.args if isinstance(a, (list, tuple, str))), None)
        safe_set(span, Ctx.BATCH_SIZE, len(texts) if isinstance(texts, (list, tuple)) else int(texts is not None))

    def success(self, span: Any, call: Call, result: Any) -> None:
        token_input, _, _ = extract_usage(result)
        if token_input is None:
            token_input = _int(get_field(get_field(result, "usage"), "total_tokens"))
        record_usage(span, call.state["provider"], call.state["model"], self.op, token_input, None)


def trace_embedding(
    model_id: str | Callable[..., str] | None = None,
    *,
    provider: str | None = None,
    deployment_id: str | None = None,
    input_arg: str = "texts",
) -> Callable[[F], F]:
    """Trace an embedding call as ``embedding.create`` (batch size from the input)."""

    def decorator(fn: F) -> F:
        return wrap(fn, EmbeddingSpec(model_id, provider, deployment_id, input_arg))

    return decorator
