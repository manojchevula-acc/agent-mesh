"""@trace_document — document processing (Blueprint §3 Document Processing: documents
received / parsed / rejected, OCR latency, chunk count, parsing failures).

One ``document.process`` span per received document; ``gernas.documents`` counts them by
type and status, ``gernas.document.chunks`` records the chunk count. Extraction / OCR
latency is the ``agent.step`` with ``step_type="document_extraction"`` inside it. Document
content, file names and paths are never recorded.

Pipelines that catch their own errors and return a result object report the outcome
through ``outcome_from(result) -> "parsed" | "rejected" | "failed"``; an exception is
``failed``. ``rejected`` (e.g. unsupported or policy-blocked document) keeps status OK.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any, TypeVar

from opentelemetry.trace import StatusCode

from gernas._metrics import observe, record
from gernas.attributes import Ctx, Metric, SpanName
from gernas.decorators._core import Call, Spec, mark_status, resolve, wrap
from gernas.tracer import safe_set

F = TypeVar("F", bound=Callable[..., Any])

STATUSES = ("parsed", "rejected", "failed")


class DocumentSpec(Spec):
    op = SpanName.DOCUMENT

    def __init__(
        self,
        document_type: Any,
        outcome_from: Callable[[Any], str] | None,
        chunks_from: Callable[[Any], int] | None,
    ) -> None:
        self.document_type, self.outcome_from, self.chunks_from = document_type, outcome_from, chunks_from

    def start(self, span: Any, call: Call) -> None:
        dtype = resolve(self.document_type, call) if callable(self.document_type) else self.document_type
        dtype = dtype or call.arg("document_type")
        call.state["type"] = None if dtype is None else str(dtype)
        safe_set(span, Ctx.DOCUMENT_TYPE, call.state["type"])

    def success(self, span: Any, call: Call, result: Any) -> None:
        status = resolve(self.outcome_from, call, result) if self.outcome_from else "parsed"
        status = status if status in STATUSES else "parsed"
        call.state["status"] = status
        safe_set(span, Ctx.DOCUMENT_STATUS, status)
        chunks = resolve(self.chunks_from, call, result) if self.chunks_from else None
        if isinstance(chunks, int):
            call.state["chunks"] = chunks
            safe_set(span, Ctx.CHUNK_COUNT, chunks)
        if status == "failed":  # caught inside the pipeline: still a parsing failure
            call.state["execution_status"] = "error"
            safe_set(span, Ctx.EXECUTION_STATUS, "error")
            safe_set(span, Ctx.ERROR_CATEGORY, "internal")
            mark_status(span, StatusCode.ERROR, "document processing failed")

    def failure(self, span: Any, call: Call, exc: BaseException) -> bool:
        call.state["status"] = "failed"
        safe_set(span, Ctx.DOCUMENT_STATUS, "failed")
        return super().failure(span, call, exc)

    def finish(self, span: Any, call: Call, exc: BaseException | None) -> None:
        dtype = call.state.get("type")
        record(Metric.DOCUMENTS, {Ctx.DOCUMENT_TYPE: dtype, Ctx.DOCUMENT_STATUS: call.state.get("status", "failed")})
        if "chunks" in call.state:
            observe(Metric.DOCUMENT_CHUNKS, call.state["chunks"], {Ctx.DOCUMENT_TYPE: dtype})


def trace_document(
    document_type: str | Callable[..., str] | None = None,
    *,
    outcome_from: Callable[[Any], str] | None = None,
    chunks_from: Callable[[Any], int] | None = None,
) -> Callable[[F], F]:
    """Trace the processing of one received document as ``document.process``.

    ``document_type`` defaults to a ``document_type`` argument of the call.
    """

    def decorator(fn: F) -> F:
        return wrap(fn, DocumentSpec(document_type, outcome_from, chunks_from))

    return decorator
