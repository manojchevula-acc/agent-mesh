"""LangChain callback handler producing GERNAS spans (WP1-CORE-02d step 1).

``GernasLangChainCallback`` turns LangChain run events into spans:
chains → ``chain.run``, chat/LLM calls → ``llm.call`` (with token usage),
tools → ``tool.{name}``, retrievers → ``retrieval.search``. Parent/child links are
kept per LangChain ``run_id`` through OTel context, so nested chains produce a
correct hierarchy under whatever span is current when the chain is invoked.
Requires ``langchain-core`` (``pip install gernas-telemetry[langchain]``).
"""
from __future__ import annotations

import threading
import time
from typing import Any
from uuid import UUID

from opentelemetry import context as otel_context
from opentelemetry import trace
from opentelemetry.trace import SpanKind, Status, StatusCode

from gernas.attributes import Ctx, SpanName
from gernas.decorators._core import record_exception
from gernas.decorators.llm import extract_usage, record_usage
from gernas.tracer import get_config, get_tracer, safe_set
from gernas.utils.hashing import hash_inputs

try:
    from langchain_core.callbacks import BaseCallbackHandler
except ImportError as exc:  # pragma: no cover - exercised only without the extra
    raise ImportError("gernas.adapters.langchain requires langchain-core: "
                      "pip install gernas-telemetry[langchain]") from exc


def _name(serialized: dict | None, kwargs: dict, default: str) -> str:
    if kwargs.get("name"):
        return str(kwargs["name"])
    if serialized:
        if serialized.get("name"):
            return str(serialized["name"])
        ident = serialized.get("id")
        if isinstance(ident, list) and ident:
            return str(ident[-1])
    return default


class GernasLangChainCallback(BaseCallbackHandler):
    """Callback handler that records LangChain runs as GERNAS spans."""

    raise_error = False  # a telemetry failure must never break the chain

    def __init__(self, provider: str | None = None) -> None:
        super().__init__()
        self._provider = provider
        self._spans: dict[UUID, tuple[Any, float, dict]] = {}
        self._lock = threading.Lock()

    # ── span bookkeeping ─────────────────────────────────────────────────────
    def _open(self, run_id: UUID, parent_run_id: UUID | None, op: str, name: str, kind: SpanKind) -> Any:
        try:
            with self._lock:
                parent = self._spans.get(parent_run_id) if parent_run_id else None
            ctx = trace.set_span_in_context(parent[0]) if parent else otel_context.get_current()
            span = get_tracer().start_span(name, context=ctx, kind=kind)
            for key, value in get_config().envelope.items():
                safe_set(span, key, value)
            safe_set(span, Ctx.OPERATION, op)
            with self._lock:
                self._spans[run_id] = (span, time.perf_counter(), {})
            return span
        except Exception:  # noqa: BLE001
            return None

    def _close(self, run_id: UUID, error: BaseException | None = None) -> tuple[Any, float, dict] | None:
        with self._lock:
            entry = self._spans.pop(run_id, None)
        if entry is None:
            return None
        span = entry[0]
        try:
            if error is not None:
                record_exception(span, error)
                span.set_status(Status(StatusCode.ERROR, type(error).__name__))
            else:
                span.set_status(Status(StatusCode.OK))
        finally:
            span.end()
        return entry

    def _get(self, run_id: UUID) -> tuple[Any, float, dict] | None:
        with self._lock:
            return self._spans.get(run_id)

    # ── chains ───────────────────────────────────────────────────────────────
    def on_chain_start(self, serialized: dict, inputs: Any, *, run_id: UUID, parent_run_id: UUID | None = None,
                       **kwargs: Any) -> None:
        span = self._open(run_id, parent_run_id, SpanName.CHAIN, SpanName.CHAIN, SpanKind.INTERNAL)
        if span is not None:
            safe_set(span, Ctx.CHAIN_NAME, _name(serialized, kwargs, "chain"))
            safe_set(span, Ctx.INPUT_HASH, hash_inputs(inputs))

    def on_chain_end(self, outputs: Any, *, run_id: UUID, **kwargs: Any) -> None:
        entry = self._get(run_id)
        if entry is not None:
            safe_set(entry[0], Ctx.OUTPUT_HASH, hash_inputs(outputs))
            safe_set(entry[0], Ctx.DURATION_MS, round((time.perf_counter() - entry[1]) * 1000, 3))
        self._close(run_id)

    def on_chain_error(self, error: BaseException, *, run_id: UUID, **kwargs: Any) -> None:
        self._close(run_id, error)

    # ── LLM / chat model ─────────────────────────────────────────────────────
    def _llm_start(self, serialized: dict, run_id: UUID, parent_run_id: UUID | None, kwargs: dict) -> None:
        span = self._open(run_id, parent_run_id, SpanName.LLM, SpanName.LLM, SpanKind.CLIENT)
        if span is None:
            return
        params = kwargs.get("invocation_params") or {}
        meta = kwargs.get("metadata") or {}
        model = params.get("model") or params.get("model_name") or meta.get("ls_model_name") or \
            _name(serialized, {}, "unknown")
        provider = self._provider or meta.get("ls_provider") or get_config().llm_provider or "unknown"
        state = self._spans[run_id][2]
        state.update(model=str(model), provider=str(provider))
        safe_set(span, Ctx.MODEL_ID, str(model))
        safe_set(span, Ctx.PROVIDER, str(provider))
        safe_set(span, Ctx.DEPLOYMENT_ID, str(model))
        safe_set(span, Ctx.OPERATION_NAME, "chat")

    def on_llm_start(self, serialized: dict, prompts: list[str], *, run_id: UUID,
                     parent_run_id: UUID | None = None, **kwargs: Any) -> None:
        self._llm_start(serialized, run_id, parent_run_id, kwargs)

    def on_chat_model_start(self, serialized: dict, messages: list, *, run_id: UUID,
                            parent_run_id: UUID | None = None, **kwargs: Any) -> None:
        self._llm_start(serialized, run_id, parent_run_id, kwargs)

    def on_llm_end(self, response: Any, *, run_id: UUID, **kwargs: Any) -> None:
        entry = self._get(run_id)
        if entry is not None:
            span, t0, state = entry
            usage = (getattr(response, "llm_output", None) or {}).get("token_usage") \
                if isinstance(getattr(response, "llm_output", None), dict) else None
            in_tok, out_tok = extract_usage({"usage": usage}) if usage else (None, None)
            if in_tok is None and out_tok is None:
                try:
                    message = response.generations[0][0].message
                    in_tok, out_tok = extract_usage(message)
                except (AttributeError, IndexError, TypeError):
                    pass
            record_usage(span, state.get("model", "unknown"), state.get("provider", "unknown"), in_tok, out_tok)
            safe_set(span, Ctx.GENERATION_LATENCY_MS, round((time.perf_counter() - t0) * 1000, 3))
        self._close(run_id)

    def on_llm_error(self, error: BaseException, *, run_id: UUID, **kwargs: Any) -> None:
        self._close(run_id, error)

    # ── tools / retrievers ───────────────────────────────────────────────────
    def on_tool_start(self, serialized: dict, input_str: str, *, run_id: UUID,
                      parent_run_id: UUID | None = None, **kwargs: Any) -> None:
        name = _name(serialized, kwargs, "tool")
        span = self._open(run_id, parent_run_id, SpanName.TOOL, SpanName.TOOL.format(tool_id=name), SpanKind.INTERNAL)
        if span is not None:
            safe_set(span, Ctx.TOOL_ID, name)
            safe_set(span, Ctx.TOOL_TYPE, "function")
            safe_set(span, Ctx.AUTH_DECISION, "allow")
            safe_set(span, Ctx.INPUT_HASH, hash_inputs(input_str))

    def on_tool_end(self, output: Any, *, run_id: UUID, **kwargs: Any) -> None:
        entry = self._get(run_id)
        if entry is not None:
            safe_set(entry[0], Ctx.OUTPUT_HASH, hash_inputs(str(output)))
        self._close(run_id)

    def on_tool_error(self, error: BaseException, *, run_id: UUID, **kwargs: Any) -> None:
        self._close(run_id, error)

    def on_retriever_start(self, serialized: dict, query: str, *, run_id: UUID,
                           parent_run_id: UUID | None = None, **kwargs: Any) -> None:
        span = self._open(run_id, parent_run_id, SpanName.RETRIEVAL, SpanName.RETRIEVAL, SpanKind.CLIENT)
        if span is not None:
            safe_set(span, Ctx.QUERY_HASH, hash_inputs(str(query)))

    def on_retriever_end(self, documents: Any, *, run_id: UUID, **kwargs: Any) -> None:
        entry = self._get(run_id)
        if entry is not None:
            count = len(documents) if hasattr(documents, "__len__") else 0
            safe_set(entry[0], Ctx.RESULT_COUNT, count)
            safe_set(entry[0], Ctx.EMPTY_RESULT, count == 0)
            safe_set(entry[0], Ctx.RETRIEVAL_DURATION_MS, round((time.perf_counter() - entry[1]) * 1000, 3))
        self._close(run_id)

    def on_retriever_error(self, error: BaseException, *, run_id: UUID, **kwargs: Any) -> None:
        self._close(run_id, error)

    def open_run_count(self) -> int:
        with self._lock:
            return len(self._spans)
