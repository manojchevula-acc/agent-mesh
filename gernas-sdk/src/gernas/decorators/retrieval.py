"""@trace_retrieval — RAG / search / vector queries (Blueprint §2 knowledge context, §3 RAG /
Search: retrieval duration, query rewriting, Top-K, result count, empty result, ranking score).

Query text and retrieved content are never recorded.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any, TypeVar

from gernas._metrics import record
from gernas.attributes import Ctx, Metric, SpanName
from gernas.decorators._core import Call, Spec, as_list, get_field, resolve, wrap
from gernas.tracer import safe_set

F = TypeVar("F", bound=Callable[..., Any])

_SCORE = ("score", "relevance_score", "rerank_score", "similarity")
_SOURCE = ("source_id", "doc_id", "document_id", "id", "source")
_VERSION = ("document_version", "doc_version", "version")


def _first(item: Any, names: tuple[str, ...]) -> Any:
    value = get_field(item, *names)
    return value if value is not None else get_field(get_field(item, "metadata"), *names)


def first_score(item: Any) -> float | None:
    if (
        isinstance(item, (tuple, list))
        and len(item) >= 2
        and isinstance(item[-1], (int, float))
        and not isinstance(item[-1], bool)
    ):
        return float(item[-1])
    value = _first(item, _SCORE)
    return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else None


def first_source(item: Any) -> str | None:
    value = _first(item[0] if isinstance(item, (tuple, list)) and item else item, _SOURCE)
    return None if value is None else str(value)


class RetrievalSpec(Spec):
    op = SpanName.RETRIEVAL
    kind = "CLIENT"

    def __init__(
        self,
        index_id: str | None,
        top_k: int | None,
        strategy: str | None,
        knowledge_domain: str | None,
        query_rewritten: bool | Callable[[Any], bool] | None,
        results_from: Callable[[Any], Any] | None,
        authorization_decision: str | Callable[[Any], str] | None = None,
    ) -> None:
        self.index_id, self.top_k, self.strategy, self.knowledge_domain = index_id, top_k, strategy, knowledge_domain
        self.query_rewritten, self.results_from = query_rewritten, results_from
        self.authorization_decision = authorization_decision

    def start(self, span: Any, call: Call) -> None:
        top_k = self.top_k if self.top_k is not None else call.arg("top_k", default=call.arg("k"))
        safe_set(span, Ctx.TOP_K, top_k if isinstance(top_k, int) else None)
        safe_set(span, Ctx.INDEX_ID, self.index_id)
        safe_set(span, Ctx.RETRIEVAL_STRATEGY, self.strategy)
        safe_set(span, Ctx.KNOWLEDGE_DOMAIN, self.knowledge_domain)
        if isinstance(self.query_rewritten, bool):
            safe_set(span, Ctx.QUERY_REWRITTEN, self.query_rewritten)

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
            version = _first(items[0][0] if isinstance(items[0], (tuple, list)) else items[0], _VERSION)
            safe_set(span, Ctx.DOCUMENT_VERSION, None if version is None else str(version))
        decision = (
            resolve(self.authorization_decision, call, result)
            if callable(self.authorization_decision)
            else self.authorization_decision
        )
        safe_set(span, Ctx.AUTHORIZATION_DECISION, decision)
        if callable(self.query_rewritten):
            flag = resolve(self.query_rewritten, call, result)
            if flag is not None:
                safe_set(span, Ctx.QUERY_REWRITTEN, bool(flag))

    def finish(self, span: Any, call: Call, exc: BaseException | None) -> None:
        record(
            Metric.RETRIEVAL_CALLS,
            {
                Ctx.INDEX_ID: self.index_id,
                Ctx.EXECUTION_STATUS: "error" if exc else "ok",
                Ctx.EMPTY_RESULT: call.state.get("count", 0) == 0 if exc is None else None,
            },
        )


def trace_retrieval(
    *,
    index_id: str | None = None,
    top_k: int | None = None,
    strategy: str | None = None,
    knowledge_domain: str | None = None,
    query_rewritten: bool | Callable[[Any], bool] | None = None,
    results_from: Callable[[Any], Any] | None = None,
    authorization_decision: str | Callable[[Any], str] | None = None,
) -> Callable[[F], F]:
    """Trace a retrieval / search as ``retrieval.search`` (empty results are flagged).

    Knowledge layer (Blueprint §3): ``source_id`` / ``document_version`` of the top result
    are read from common result shapes; ``authorization_decision`` records the knowledge
    access decision (``allow`` / ``deny``) when the store enforces document-level access.
    """

    def decorator(fn: F) -> F:
        return wrap(
            fn,
            RetrievalSpec(
                index_id, top_k, strategy, knowledge_domain, query_rewritten, results_from, authorization_decision
            ),
        )

    return decorator
