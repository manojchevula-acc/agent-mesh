"""Blueprint §3 Document Processing and Knowledge Layer signals."""

from __future__ import annotations

import pytest
from opentelemetry.trace import StatusCode

import gernas
from gernas import Ctx, Metric, SpanName
from gernas.conformance import check, from_readable

from conftest import attrs


class Result:
    def __init__(self, status, chunks):
        self.status, self.chunks_created = status, chunks


def _outcome(r):
    return {"success": "parsed", "rejected": "rejected"}.get(r.status, "failed")


def test_parsed_document_counts_chunks_and_nests_extraction(spans, metric_points):
    @gernas.trace_step("extract", step_type="document_extraction")
    def extract(path):
        return "text"

    @gernas.trace_document(outcome_from=_outcome, chunks_from=lambda r: r.chunks_created)
    def ingest_file(path, document_type):
        extract(path)
        return Result("success", 42)

    ingest_file("/secret/path/policy.pdf", "pricing_policy")
    doc, step = spans(SpanName.DOCUMENT)[0], spans(SpanName.STEP)[0]
    a = attrs(doc)
    assert a[Ctx.DOCUMENT_TYPE] == "pricing_policy" and a[Ctx.DOCUMENT_STATUS] == "parsed" and a[Ctx.CHUNK_COUNT] == 42
    assert step.parent.span_id == doc.context.span_id and attrs(step)[Ctx.STEP_TYPE] == "document_extraction"
    assert not [v for v in a.values() if "policy.pdf" in str(v)]  # no file names / paths
    m = metric_points()
    assert m[Metric.DOCUMENTS] == [({Ctx.DOCUMENT_TYPE: "pricing_policy", Ctx.DOCUMENT_STATUS: "parsed"}, 1)]
    assert m[Metric.DOCUMENT_CHUNKS] == [({Ctx.DOCUMENT_TYPE: "pricing_policy"}, 1)]
    assert check(from_readable(spans())).passed


def test_caught_failure_is_a_parsing_failure(spans):
    @gernas.trace_document("faq", outcome_from=_outcome)
    def ingest_file(path):
        return Result("error", 0)

    ingest_file("x")
    span = spans(SpanName.DOCUMENT)[0]
    assert attrs(span)[Ctx.DOCUMENT_STATUS] == "failed" and attrs(span)[Ctx.EXECUTION_STATUS] == "error"
    assert span.status.status_code == StatusCode.ERROR


def test_rejected_document_keeps_status_ok(spans):
    @gernas.trace_document("faq", outcome_from=_outcome)
    def ingest_file(path):
        return Result("rejected", 0)

    ingest_file("x")
    span = spans(SpanName.DOCUMENT)[0]
    assert attrs(span)[Ctx.DOCUMENT_STATUS] == "rejected" and span.status.status_code == StatusCode.OK


def test_exception_is_failed(spans):
    @gernas.trace_document("faq")
    def ingest_file(path):
        raise ValueError("corrupt")

    with pytest.raises(ValueError):
        ingest_file("x")
    assert attrs(spans(SpanName.DOCUMENT)[0])[Ctx.DOCUMENT_STATUS] == "failed"


def test_retrieval_records_document_version_and_authorization(spans):
    @gernas.trace_retrieval(index_id="policies", authorization_decision="allow")
    def search(query):
        return [{"id": "doc-7", "score": 0.9, "metadata": {"document_version": "v2.4"}}]

    search("q")
    a = attrs(spans(SpanName.RETRIEVAL)[0])
    assert (
        a[Ctx.DOCUMENT_VERSION] == "v2.4" and a[Ctx.AUTHORIZATION_DECISION] == "allow" and a[Ctx.SOURCE_ID] == "doc-7"
    )
