"""@trace_rag, @trace_retrieval, @trace_embedding (WP1-CORE-02c steps 1-3).

Query text is fingerprinted, never recorded. Result counts, emptiness and the
top ranking score are read from the returned value (list, tuple of
``(doc, score)``, dicts with ``score``, objects with ``.score``, or containers
exposing ``results``/``documents``/``points``).
"""
from __future__ import annotations

from typing import Any, Callable, TypeVar

from gernas.attributes import Ctx, Metric, SpanName
from gernas.decorators._core import Call, Spec, as_list, get_field, resolve, wrap
from gernas.decorators.llm import extract_usage
from gernas.metrics.agentic import record
from gernas.tracer import safe_set
from gernas.utils.hashing import hash_inputs

F = TypeVar("F", bound=Callable[..., Any])

_SCORE = ("score", "relevance_score", "rerank_score", "similarity", "distance")
_SOURCE = ("source_id", "doc_id", "document_id", "id", "chunk_id")


def first_score(item: Any) -> float | None:
    if isinstance(item, (tuple, list)) and len(item) >= 2 and isinstance(item[-1], (int, float)) \
            and not isinstance(item[-1], bool):
        return float(item[-1])
    value = get_field(item, *_SCORE)
    if value is None:
        value = get_field(get_field(item, "metadata"), *_SCORE)
    return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else None


def first_source(item: Any) -> str | None:
    if isinstance(item, (tuple, list)) and item:
        item = item[0]
    value = get_field(item, *_SOURCE)
    if value is None:
        value = get_field(get_field(item, "metadata"), *_SOURCE)
    return None if value is None else str(value)


class _SearchSpec(Spec):
    kind = "INTERNAL"

    def __init__(self, index_id: str | None, top_k: int | None, query_arg: str, strategy: str | None,
                 query_rewritten: bool | Callable[..., bool] | None, results_from: Callable[[Any], Any] | None,
                 knowledge_domain: str | None) -> None:
        self.index_id, self.top_k, self.query_arg, self.strategy = index_id, top_k, query_arg, strategy
        self.query_rewritten, self.results_from, self.knowledge_domain = query_rewritten, results_from, knowledge_domain

    def start(self, span: Any, call: Call) -> None:
        query = call.arg(self.query_arg, position=0 if not call.args or not hasattr(call.args[0], "__dict__") else 1)
        safe_set(span, Ctx.QUERY_HASH, hash_inputs(str(query)))
        top_k = self.top_k if self.top_k is not None else call.arg("top_k", default=call.arg("k"))
        if isinstance(top_k, int):
            safe_set(span, Ctx.TOP_K, top_k)
        safe_set(span, Ctx.RETRIEVAL_STRATEGY, self.strategy)
        safe_set(span, Ctx.INDEX_ID, self.index_id)
        safe_set(span, Ctx.KNOWLEDGE_DOMAIN, self.knowledge_domain)
        if self.query_rewritten is not None and not callable(self.query_rewritten):
            safe_set(span, Ctx.QUERY_REWRITTEN, bool(self.query_rewritten))

    def success(self, span: Any, call: Call, result: Any) -> None:
        raw = resolve(self.results_from, call, result) if self.results_from else result
        items = as_list(raw)
        count = len(items) if items is not None else (0 if raw is None else 1)
        call.state["count"] = count
        safe_set(span, Ctx.RESULT_COUNT, count)
        safe_set(span, Ctx.EMPTY_RESULT, count == 0)
        if items:
            safe_set(span, Ctx.RANKING_SCORE, first_score(items[0]))
            safe_set(span, Ctx.SOURCE_ID, first_source(items[0]))
        if callable(self.query_rewritten):
            flag = resolve(self.query_rewritten, call, result)
            if flag is not None:
                safe_set(span, Ctx.QUERY_REWRITTEN, bool(flag))

    def finish(self, span: Any, call: Call, exc: BaseException | None) -> None:
        safe_set(span, Ctx.RETRIEVAL_DURATION_MS, call.elapsed_ms())
        record(Metric.RETRIEVAL_CALLS, {Ctx.OPERATION: self.op, "result": "error" if exc else "success",
                                        Ctx.EMPTY_RESULT: call.state.get("count", 0) == 0})


class RAGSpec(_SearchSpec):
    op = SpanName.RAG


class RetrievalSpec(_SearchSpec):
    op = SpanName.RETRIEVAL
    kind = "CLIENT"


def trace_rag(*, index_id: str | None = None, top_k: int | None = None, query_arg: str = "query",
              strategy: str | None = None, query_rewritten: bool | Callable[..., bool] | None = None,
              results_from: Callable[[Any], Any] | None = None,
              knowledge_domain: str | None = None) -> Callable[[F], F]:
    """Trace a RAG retrieval step as ``rag.retrieve`` (empty results are flagged)."""
    def decorator(fn: F) -> F:
        return wrap(fn, RAGSpec(index_id, top_k, query_arg, strategy, query_rewritten, results_from, knowledge_domain))
    return decorator


def trace_retrieval(*, index_id: str | None = None, top_k: int | None = None, query_arg: str = "query",
                    strategy: str | None = None, results_from: Callable[[Any], Any] | None = None,
                    knowledge_domain: str | None = None) -> Callable[[F], F]:
    """Trace a search / vector-store query as ``retrieval.search``."""
    def decorator(fn: F) -> F:
        return wrap(fn, RetrievalSpec(index_id, top_k, query_arg, strategy, None, results_from, knowledge_domain))
    return decorator


class EmbeddingSpec(Spec):
    op = SpanName.EMBEDDING
    kind = "CLIENT"

    def __init__(self, model: str | None, input_arg: str) -> None:
        self.model, self.input_arg = model, input_arg

    def start(self, span: Any, call: Call) -> None:
        texts = call.arg(self.input_arg)
        if texts is None:
            for name in ("texts", "input", "inputs", "documents", "sentences"):
                texts = call.arg(name)
                if texts is not None:
                    break
        if texts is None:
            texts = next((a for a in call.args if isinstance(a, (list, tuple, str))), None)
        batch = len(texts) if isinstance(texts, (list, tuple)) else (1 if texts is not None else 0)
        safe_set(span, Ctx.EMBED_MODEL, self.model or call.arg("model") or "unknown")
        safe_set(span, Ctx.EMBED_BATCH_SIZE, batch)
        safe_set(span, Ctx.OPERATION_NAME, "embeddings")

    def success(self, span: Any, call: Call, result: Any) -> None:
        in_tok, _ = extract_usage(result)
        total = get_field(get_field(result, "usage"), "total_tokens")
        safe_set(span, Ctx.EMBED_TOKEN_COUNT, in_tok if in_tok is not None else total)

    def finish(self, span: Any, call: Call, exc: BaseException | None) -> None:
        safe_set(span, Ctx.EMBED_LATENCY_MS, call.elapsed_ms())


def trace_embedding(model: str | None = None, *, input_arg: str = "texts") -> Callable[[F], F]:
    """Trace an embedding call as ``embedding.create`` (batch size from the input)."""
    def decorator(fn: F) -> F:
        return wrap(fn, EmbeddingSpec(model, input_arg))
    return decorator
