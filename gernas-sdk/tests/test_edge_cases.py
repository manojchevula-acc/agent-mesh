"""Edge cases: decision shapes, cost lookup, exporter selection, log bridge, HITL bounds."""

from __future__ import annotations

import json
import logging

import pytest

import gernas
from gernas import Ctx, SpanName
from gernas.config import GernasConfig
from gernas.cost import estimate_cost
from gernas.decorators.safety import infer_decision

from conftest import attrs


@pytest.mark.parametrize(
    "result,expected",
    [
        (("warn", "tone"), ("warn", "tone")),
        ((False, None), ("allow", None)),
        (True, ("deny", None)),
        ({"decision": "escalate", "category": "fraud"}, ("escalate", "fraud")),
        ({"allowed": False}, ("deny", None)),
        ({"blocked": False}, ("allow", None)),
        (object(), ("allow", None)),
    ],
)
def test_guardrail_decision_shapes(result, expected):
    assert infer_decision(result) == expected


def test_guardrail_decision_from_and_non_blocking(spans):
    @gernas.trace_guardrail("rbac", decision_from=lambda r: r["verdict"], blocks_run=False)
    def check(role):
        return {"verdict": "deny"}

    @gernas.trace_agent("a", agent_type="worker")
    def run():
        check("customer")

    run()
    assert attrs(spans(SpanName.GUARDRAIL)[0])[Ctx.POLICY_DECISION] == "deny"
    assert attrs(spans(SpanName.AGENT)[0])[Ctx.TERMINATION_REASON] == "completed"


def test_guardrail_unexpected_error_escalates(spans):
    @gernas.trace_guardrail("pii")
    def check():
        raise RuntimeError("model unavailable")

    with pytest.raises(RuntimeError):
        check()
    span = spans(SpanName.GUARDRAIL)[0]
    assert attrs(span)[Ctx.POLICY_DECISION] == "escalate" and attrs(span)[Ctx.EXECUTION_STATUS] == "error"


@pytest.mark.parametrize("result,blocked,level", [((True, "toxicity", "high"), True, "high"), (False, False, "safe")])
def test_content_safety_shapes(spans, result, blocked, level):
    @gernas.trace_content_safety("toxicity")
    def check():
        return result

    check()
    a = attrs(spans(SpanName.CONTENT_SAFETY)[0])
    assert a[Ctx.CONTENT_BLOCKED] is blocked and a[Ctx.RISK_LEVEL] == level


def test_content_safety_block_exception(spans):
    class ContentBlockedError(Exception):
        pass

    @gernas.trace_content_safety("pii", policy="dlp")
    def check():
        raise ContentBlockedError()

    with pytest.raises(ContentBlockedError):
        check()
    a = attrs(spans(SpanName.CONTENT_SAFETY)[0])
    assert a[Ctx.CONTENT_BLOCKED] is True and a[Ctx.POLICY_TRIGGERED] == "dlp"


def test_cost_lookup_edges(tmp_path, rate_card):
    assert estimate_cost("claude-x", "anyone", 1000, 1000, str(rate_card)) == pytest.approx(0.066)
    assert estimate_cost("gpt-4o", "other_provider", 1000, 0, str(rate_card)) is None
    assert estimate_cost("gpt-4o", "azure_openai", None, None, str(rate_card)) is None
    assert estimate_cost("gpt-4o", "azure_openai", 1, 1, "") is None
    assert estimate_cost("gpt-4o", "azure_openai", 1, 1, str(tmp_path / "missing.json")) is None
    broken = tmp_path / "broken.json"
    broken.write_text("{not json", encoding="utf-8")
    assert estimate_cost("gpt-4o", "azure_openai", 1, 1, str(broken)) is None
    bad_rate = tmp_path / "bad.json"
    bad_rate.write_text(json.dumps({"rates": [{"model": "m", "input_per_1k": "x"}]}), encoding="utf-8")
    assert estimate_cost("m", "p", 1, 1, str(bad_rate)) is None


@pytest.mark.parametrize(
    "exporter,transport,endpoint,expected",
    [
        ("off", "grpc", "", "NoOpSpanExporter"),
        ("console", "grpc", "", "ConsoleSpanExporter"),
        ("otlp", "grpc", "http://127.0.0.1:4317", "OTLPSpanExporter"),
        ("otlp", "http", "http://127.0.0.1:4317", "OTLPSpanExporter"),
    ],
)
def test_exporter_selection(exporter, transport, endpoint, expected):
    from gernas.exporters import _http_url, build_log_exporter, build_metric_exporter, build_span_exporter

    cfg = GernasConfig(
        service_name="s",
        use_case_id="u",
        application_id="a",
        business_domain="b",
        environment="dev",
        exporter=exporter,
        otlp_transport=transport,
        otlp_endpoint=endpoint,
    )
    assert type(build_span_exporter(cfg)).__name__ == expected
    assert build_metric_exporter(cfg) is not None and build_log_exporter(cfg) is not None
    if exporter == "otlp":
        assert _http_url(cfg, "traces") == "http://127.0.0.1:4318/v1/traces"


def test_log_records_are_scrubbed_and_correlated():
    from gernas.logs import GernasLogHandler

    emitted = []

    class Logger:
        def emit(self, **kw):
            emitted.append(kw)

    class Provider:
        def get_logger(self, name):
            return Logger()

        def force_flush(self):
            return True

    handler = GernasLogHandler(Provider())
    log = logging.getLogger("gernas.test.logs")
    log.addHandler(handler)
    try:
        try:
            raise ValueError("bad card 4111 1111 1111 1111")
        except ValueError:
            log.exception("customer jane@example.com failed")
    finally:
        log.removeHandler(handler)
    handler.flush()
    (record,) = emitted
    assert "jane@example.com" not in record["body"] and "4111" not in record["attributes"]["exception.message"]
    assert record["severity_text"] == "ERROR"


def test_hitl_wait_mode_timeout_and_sync(spans):
    @gernas.trace_hitl("approve", outcome_from=lambda r: r)
    def wait():
        raise TimeoutError

    with pytest.raises(TimeoutError):
        wait()

    @gernas.trace_hitl("approve2", outcome_from=lambda r: r)
    def decide():
        return "approved"

    decide()
    timed_out, approved = sorted(spans(SpanName.HITL), key=lambda s: s.start_time)
    assert attrs(timed_out)[Ctx.HITL_OUTCOME] == "timed_out" and attrs(approved)[Ctx.HITL_OUTCOME] == "approved"


def test_memory_custom_hit_and_stale(spans):
    @gernas.trace_memory("read", hit_from=lambda r: r["found"], stale_from=lambda r: r["age"] > 10)
    def load():
        return {"found": True, "age": 99}

    load()
    a = attrs(spans(SpanName.MEMORY)[0])
    assert a[Ctx.MEMORY_HIT] is True and a[Ctx.MEMORY_STALE] is True


def test_use_case_failure_and_cancellation_outcomes(spans):
    @gernas.trace_use_case()
    def handle():
        raise RuntimeError("x")

    with pytest.raises(RuntimeError):
        handle()
    assert attrs(spans(SpanName.USE_CASE)[0])[Ctx.BUSINESS_OUTCOME] == "failure"


def test_async_generator_agent_and_dynamic_ids(spans):
    import asyncio

    @gernas.trace_agent(lambda kind: f"{kind}_agent", agent_type="worker", name=lambda kind: kind.title())
    async def run(kind):
        yield 1
        yield 2

    async def consume():
        return [x async for x in run("pricing")]

    assert asyncio.run(consume()) == [1, 2]
    a = attrs(spans(SpanName.AGENT)[0])
    assert a[Ctx.AGENT_ID] == "pricing_agent" and a[Ctx.AGENT_NAME] == "Pricing"


def test_suppressed_scopes_are_not_exported():
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import SimpleSpanProcessor
    from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

    from gernas.tracer import CountingSpanExporter

    sink = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(CountingSpanExporter(sink, ("fastmcp",))))
    provider.get_tracer("fastmcp").start_span("tools/call x").end()
    provider.get_tracer("gernas").start_span("tool.call").end()
    assert [s.name for s in sink.get_finished_spans()] == ["tool.call"]
    cfg = GernasConfig.from_env(
        environ={
            "GERNAS_SERVICE_NAME": "s",
            "GERNAS_USE_CASE_ID": "u",
            "GERNAS_APPLICATION_ID": "a",
            "GERNAS_BUSINESS_DOMAIN": "b",
            "GERNAS_ENVIRONMENT": "dev",
            "GERNAS_EXPORTER": "off",
            "GERNAS_SUPPRESSED_SCOPES": "fastmcp, other",
        }
    )
    assert cfg.suppressed_scopes == ("fastmcp", "other")
