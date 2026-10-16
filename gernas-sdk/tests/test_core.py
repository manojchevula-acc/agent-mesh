"""WP1-CORE-01: config, idempotent init, exporters, safe_set, propagation primitives."""
from __future__ import annotations

import threading

import pytest
from opentelemetry import baggage, trace
from opentelemetry.sdk.trace.export import SpanExportResult

import gernas
from gernas import Ctx
from gernas.config import GernasConfig
from gernas.exporters import NoOpSpanExporter, build_metric_exporter, build_span_exporter
from gernas.tracer import CountingSpanExporter, get_dropped_attribute_count, get_dropped_span_count


# ── Step 2: config ───────────────────────────────────────────────────────────

def test_missing_endpoint_raises_keyerror_naming_the_variable():
    with pytest.raises(KeyError, match="GERNAS_OTLP_ENDPOINT"):
        GernasConfig.from_env(environ={})


def test_local_sink_needs_no_endpoint():
    cfg = GernasConfig.from_env(environ={"GERNAS_EXPORTER": "console"})
    assert cfg.exporter == "console" and cfg.otlp_endpoint == ""


def test_from_env_reads_every_documented_variable():
    env = {
        "GERNAS_OTLP_ENDPOINT": "http://collector:4317", "GERNAS_SERVICE_NAME": "svc", "GERNAS_SERVICE_VERSION": "1.2",
        "GERNAS_USE_CASE_ID": "UC-1", "GERNAS_OTLP_API_KEY": "k", "GERNAS_OTLP_TRANSPORT": "http",
        "GERNAS_BUSINESS_DOMAIN": "pricing", "GERNAS_APPLICATION_ID": "app", "GERNAS_ENVIRONMENT": "uat",
        "GERNAS_LOOP_THRESHOLD": "4", "GERNAS_MAX_STEPS": "9", "GERNAS_EMIT_OPIK": "false",
        "GERNAS_EMIT_DATAPLANE": "true", "GERNAS_SAMPLING_RATE": "0.5", "GERNAS_RATE_CARD_PATH": "rc.json",
        "GERNAS_COMMIT_SHA": "4a9de17",
    }
    cfg = GernasConfig.from_env(environ=env)
    assert (cfg.otlp_endpoint, cfg.service_name, cfg.service_version, cfg.use_case_id) == \
        ("http://collector:4317", "svc", "1.2", "UC-1")
    assert (cfg.otlp_transport, cfg.business_domain, cfg.application_id, cfg.environment) == \
        ("http", "pricing", "app", "uat")
    assert (cfg.loop_threshold, cfg.max_steps, cfg.emit_opik, cfg.emit_dataplane, cfg.sampling_rate) == \
        (4, 9, False, True, 0.5)
    assert cfg.agent_version == "1.2"  # defaults to the service version


@pytest.mark.parametrize("env,match", [
    ({"GERNAS_EXPORTER": "bogus"}, "GERNAS_EXPORTER"),
    ({"GERNAS_EXPORTER": "off", "GERNAS_OTLP_TRANSPORT": "smtp"}, "GERNAS_OTLP_TRANSPORT"),
    ({"GERNAS_EXPORTER": "off", "GERNAS_SAMPLING_RATE": "2"}, "GERNAS_SAMPLING_RATE"),
    ({"GERNAS_EXPORTER": "off", "GERNAS_MAX_STEPS": "many"}, "GERNAS_MAX_STEPS"),
])
def test_invalid_values_are_rejected(env, match):
    with pytest.raises(ValueError, match=match):
        GernasConfig.from_env(environ=env)


def test_resource_carries_envelope_kill_switches_and_sdk_version():
    cfg = GernasConfig.from_env(environ={"GERNAS_EXPORTER": "off", "GERNAS_USE_CASE_ID": "UC-9",
                                         "GERNAS_EMIT_OPIK": "false", "GERNAS_COMMIT_SHA": "abc"})
    attrs = cfg.resource.attributes
    assert attrs[Ctx.USE_CASE_ID] == "UC-9"
    assert attrs[Ctx.ROUTE_OPIK] is False and attrs[Ctx.ROUTE_DATAPLANE] is True
    assert attrs[Ctx.SDK_VERSION] == gernas.__version__
    assert attrs[Ctx.COMMIT_SHA] == "abc"


def test_compliance_key_must_be_secure_outside_dev():
    with pytest.raises(ValueError, match="HMAC"):
        GernasConfig(exporter="off", environment="prod", enable_compliance=True)


# ── Step 3: idempotent init ──────────────────────────────────────────────────

def test_init_twice_returns_the_same_provider(sdk):
    again = gernas.init_gernas()
    assert again is sdk
    assert again.tracer_provider is trace.get_tracer_provider()
    assert gernas.is_initialized()


# ── Step 4: exporter factory ─────────────────────────────────────────────────

def test_grpc_is_the_default_transport():
    from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter as Grpc
    exporter = build_span_exporter(GernasConfig(otlp_endpoint="http://localhost:4317"))
    assert isinstance(exporter, Grpc)


def test_http_transport_targets_the_collector_http_port():
    from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter as Http
    exporter = build_span_exporter(GernasConfig(otlp_endpoint="http://localhost:4317", otlp_transport="http"))
    assert isinstance(exporter, Http)
    assert exporter._endpoint == "http://localhost:4318/v1/traces"  # noqa: SLF001


@pytest.mark.parametrize("kind", ["off", "memory", "console", "file"])
def test_local_sinks(kind, tmp_path):
    cfg = GernasConfig(exporter=kind, file_output_dir=str(tmp_path))
    exporter = build_span_exporter(cfg)
    assert exporter.export([]) == SpanExportResult.SUCCESS
    assert build_metric_exporter(cfg) is not None


def test_off_exporter_regression():
    """Beacon Kit's `off` exporter raised NameError on first export."""
    assert NoOpSpanExporter().export([object()]) == SpanExportResult.SUCCESS


# ── Step 5: safe_set ─────────────────────────────────────────────────────────

def test_safe_set_on_finished_span_does_not_raise():
    span = gernas.get_tracer().start_span("finished")
    span.end()
    before = get_dropped_attribute_count()
    gernas.safe_set(span, Ctx.AGENT_ID, "x")
    assert get_dropped_attribute_count() == before + 1


def test_safe_set_failure_increments_counter_and_never_raises():
    class Exploding:
        end_time = None

        def set_attribute(self, key, value):
            raise RuntimeError("boom")

    before = get_dropped_attribute_count()
    gernas.safe_set(Exploding(), Ctx.AGENT_ID, "x")
    assert get_dropped_attribute_count() == before + 1


def test_safe_set_counter_is_thread_safe():
    class Exploding:
        end_time = None

        def set_attribute(self, key, value):
            raise RuntimeError

    before = get_dropped_attribute_count()
    threads = [threading.Thread(target=lambda: [gernas.safe_set(Exploding(), "k", 1) for _ in range(500)])
               for _ in range(8)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert get_dropped_attribute_count() == before + 4000


def test_safe_set_skips_none_and_applies_pdpl(spans):
    with gernas.get_tracer().start_as_current_span("pdpl") as span:
        gernas.safe_set(span, Ctx.AGENT_ID, None)
        gernas.safe_set(span, Ctx.PROMPT, "my secret prompt")
        gernas.safe_set(span, Ctx.USER_ID, "alice")
        gernas.safe_set(span, Ctx.TOKENISED_USER_ID, "alice")
        gernas.safe_set(span, "app.note", "call me on alice@example.com")
    attrs = spans("pdpl")[0].attributes
    assert Ctx.AGENT_ID not in attrs and Ctx.PROMPT not in attrs and Ctx.USER_ID not in attrs
    assert attrs[Ctx.TOKENISED_USER_ID] != "alice" and len(attrs[Ctx.TOKENISED_USER_ID]) == 64
    assert attrs["app.note"] == "call me on [EMAIL]"
    assert attrs[Ctx.REDACTION_APPLIED] is True


def test_redaction_hook_runs_first(spans):
    gernas.set_redaction_hook(lambda k, v: gernas.DROP if k == "app.secret" else v)
    try:
        with gernas.get_tracer().start_as_current_span("hook") as span:
            gernas.safe_set(span, "app.secret", "x")
            gernas.safe_set(span, "app.public", "y")
    finally:
        gernas.set_redaction_hook(None)
    attrs = spans("hook")[0].attributes
    assert "app.secret" not in attrs and attrs["app.public"] == "y"


def test_counting_exporter_counts_failed_exports():
    class Failing(NoOpSpanExporter):
        def export(self, spans):
            raise ConnectionError("collector down")

    before = get_dropped_span_count()
    assert CountingSpanExporter(Failing()).export([1, 2, 3]) == SpanExportResult.FAILURE  # type: ignore[list-item]
    assert get_dropped_span_count() == before + 3


# ── Step 6/8: propagation primitives ─────────────────────────────────────────

def test_context_round_trips_through_headers():
    tracer = gernas.get_tracer()
    with tracer.start_as_current_span("producer") as span:
        headers = gernas.inject_context({}, session_id="sess-1")
        expected = span.get_span_context()
    assert "traceparent" in headers
    ctx = gernas.extract_context(headers)
    remote = trace.get_current_span(ctx).get_span_context()
    assert remote.trace_id == expected.trace_id and remote.span_id == expected.span_id
    assert baggage.get_baggage(Ctx.SESSION_ID, ctx) == "sess-1"


def test_extract_is_case_insensitive_and_tolerates_garbage():
    with gernas.get_tracer().start_as_current_span("p"):
        headers = gernas.inject_context({})
    upper = {k.upper(): v for k, v in headers.items()}
    assert trace.get_current_span(gernas.extract_context(upper)).get_span_context().is_valid
    assert not trace.get_current_span(gernas.extract_context({"traceparent": "garbage"})).get_span_context().is_valid
    assert gernas.extract_context(None) is not None


def test_thread_hop_produces_one_connected_record(spans):
    tracer = gernas.get_tracer()
    with tracer.start_as_current_span("parent") as parent:
        headers = gernas.inject_context({})
        parent_trace = parent.get_span_context().trace_id

    def worker(h):
        with gernas.attach_headers(h):
            with tracer.start_as_current_span("child-thread"):
                pass

    t = threading.Thread(target=worker, args=(headers,))
    t.start()
    t.join()
    child = spans("child-thread")[0]
    assert child.context.trace_id == parent_trace
    assert child.parent.span_id == spans("parent")[0].context.span_id


def test_run_in_context_keeps_the_trace_on_a_thread(spans):
    tracer = gernas.get_tracer()
    with tracer.start_as_current_span("owner") as owner:
        fn = gernas.run_in_context(lambda: tracer.start_span("bg").end())
        t = threading.Thread(target=fn)
        t.start()
        t.join()
    assert spans("bg")[0].context.trace_id == owner.get_span_context().trace_id
