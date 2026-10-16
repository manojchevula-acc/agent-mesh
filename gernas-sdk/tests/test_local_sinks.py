"""Carried-over Beacon Kit modules (no tests existed): JSONL sinks, structured logger,
log/metric decorators, request logging, hashing and checkpoint context helpers."""
from __future__ import annotations

import asyncio
import json
import logging

import pytest
from opentelemetry import baggage, context, trace
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import InMemoryMetricReader

import gernas
from gernas import Ctx
from gernas.core.file_exporters import FileLogExporter, FileMetricExporter, FileSpanExporter
from gernas.logconfig.otel_handler import GernasLogHandler

from conftest import EXPORTER, run_python


def test_file_span_exporter_writes_jsonl(tmp_path):
    EXPORTER.clear()

    @gernas.trace_tool("disk")
    def tool():
        trace.get_current_span().add_event("evt", {"k": 1})
        return 1

    tool()
    path = tmp_path / "traces" / "t.jsonl"
    exporter = FileSpanExporter(str(path))
    exporter.export(EXPORTER.get_finished_spans())
    exporter.shutdown()
    record = json.loads(path.read_text().splitlines()[0])
    assert record["name"] == "tool.disk" and record["attributes"][Ctx.TOOL_ID] == "disk"
    assert record["events"][0]["name"] == "evt" and record["resource"]["service.name"] == "gernas-tests"
    assert gernas.conformance.load_file(path)[0].attributes[Ctx.TOOL_ID] == "disk"


def test_file_metric_exporter_writes_counters_and_histograms(tmp_path):
    reader = InMemoryMetricReader()
    meter = MeterProvider(metric_readers=[reader]).get_meter("t")
    meter.create_counter("c").add(2, {"a": "b"})
    meter.create_histogram("h").record(5.0)
    path = tmp_path / "m.jsonl"
    exporter = FileMetricExporter(str(path))
    exporter.export(reader.get_metrics_data())
    assert exporter.force_flush()
    exporter.shutdown()
    rows = {r["name"]: r for r in map(json.loads, path.read_text().splitlines())}
    assert rows["c"]["value"] == 2 and rows["h"]["value"] == {"sum": 5.0, "count": 1}


def test_file_log_exporter_via_gernas_log_handler(tmp_path):
    from opentelemetry.sdk._logs import LoggerProvider
    from opentelemetry.sdk._logs.export import SimpleLogRecordProcessor

    path = tmp_path / "l.jsonl"
    exporter = FileLogExporter(str(path))
    provider = LoggerProvider()
    provider.add_log_record_processor(SimpleLogRecordProcessor(exporter))
    logger = logging.getLogger("gernas.test.sink")
    handler = GernasLogHandler(provider)
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)
    try:
        with gernas.get_tracer().start_as_current_span("logged"):
            logger.warning("customer alice@fab.ae missing")
            try:
                raise ValueError("boom")
            except ValueError:
                logger.exception("failed")
        handler.flush()
    finally:
        logger.removeHandler(handler)
        exporter.shutdown()
    lines = [json.loads(line) for line in path.read_text().splitlines()]
    assert "[EMAIL]" in json.dumps(lines[0]) and "alice@fab.ae" not in path.read_text()
    assert any("ValueError" in json.dumps(line) for line in lines)


def test_unwritable_sinks_never_raise(tmp_path):
    blocker = tmp_path / "file"
    blocker.write_text("x")
    bad = str(blocker / "sub" / "x.jsonl")  # parent is a file
    assert FileSpanExporter(bad).export([]) is not None
    FileMetricExporter(bad).shutdown()
    assert FileLogExporter(bad).export([]) == 0


def test_structured_logger_never_raises(caplog):
    log = gernas.get_logger("gernas.structured")
    with caplog.at_level(logging.DEBUG, logger="gernas.structured"):
        log.debug("d", a=1)
        log.info("i", tokens=3)
        log.warning("w")
        try:
            raise RuntimeError("x")
        except RuntimeError:
            log.error("e", exc_info=True)
        log.critical("c")


def test_log_calls_sync_and_async(caplog):
    @gernas.log_calls("gernas.calls")
    def ok(x):
        return x

    @gernas.log_calls()
    async def bad():
        raise KeyError("k")

    with caplog.at_level(logging.DEBUG):
        assert ok(1) == 1
        with pytest.raises(KeyError):
            asyncio.run(bad())
    assert any("ok" in r.getMessage() for r in caplog.records)


def test_track_latency_and_count_calls():
    @gernas.track_latency("t.latency", attribute_keys=["kind"])
    def timed(kind=None):
        return 1

    @gernas.count_calls("t.calls", attributes={"static": "v"}, attribute_keys=["kind"])
    async def counted(kind=None):
        return 2

    @gernas.count_calls("t.calls.dyn", attributes=lambda *a, **k: {"x": "y"})
    def failing():
        raise ValueError

    @gernas.track_latency("t.latency.async")
    async def atimed(state):
        return state

    assert timed(kind="a") == 1 and asyncio.run(counted(kind="b")) == 2
    assert asyncio.run(atimed({"kind": "c"})) == {"kind": "c"}
    with pytest.raises(ValueError):
        failing()


def test_setup_request_logging_stamps_trace_ids(tmp_path):
    logger = gernas.setup_request_logging(tmp_path, service_name="gernas-req-test")
    token = context.attach(baggage.set_baggage(Ctx.REQUEST_ID, "req-9"))
    try:
        with gernas.get_tracer().start_as_current_span("req"):
            logger.info("hello")
        logger.info("outside")
    finally:
        context.detach(token)
    for h in logger.handlers:
        h.flush()
    text = (tmp_path / "gernas-req-test.log").read_text()
    assert "req=req-9" in text and "trace=" + "0" * 32 in text
    assert gernas.setup_request_logging(tmp_path, service_name="gernas-req-test") is logger


def test_hashing_and_summaries():
    assert gernas.hash_inputs({"b": 1, "a": 2}) == gernas.hash_inputs({"a": 2, "b": 1})
    assert gernas.summarize_output({"error": "x", "version": 2})["has_error"] is True
    assert gernas.summarize_output([1, 2])["field_count"] == 2
    assert gernas.summarize_output(None)["has_error"] is True
    assert gernas.summarize_output(object())["size_bytes"] > 0


def test_checkpoint_serialise_restore_and_baggage_helpers():
    assert gernas.serialize_context() == {} and gernas.get_current_trace_id() == "0" * 32
    with gernas.get_tracer().start_as_current_span("checkpoint") as span:
        saved = gernas.serialize_context()
        assert gernas.get_current_span_id() == format(span.get_span_context().span_id, "016x")
    restored = gernas.restore_context(saved)
    assert trace.get_current_span(restored).get_span_context().trace_id == span.get_span_context().trace_id
    assert gernas.restore_context({}) is not None
    carrier = gernas.inject_baggage({}, **{Ctx.SESSION_ID: "s"})
    assert gernas.extract_baggage(carrier)[Ctx.SESSION_ID] == "s"
    token = gernas.set_baggage_on_current_context(**{Ctx.TASK_ID: "t"})
    try:
        assert baggage.get_baggage(Ctx.TASK_ID) == "t"
    finally:
        context.detach(token)


def test_init_with_file_output_dir_writes_all_three_signals(tmp_path):
    code = """
import logging, gernas
h = gernas.init_gernas()
@gernas.trace_agent("filed")
def run():
    logging.getLogger("gernas").warning("hello from the agent")
run()
gernas.AgenticMetrics().record_tool_call("success", "t")
h.flush(); h.shutdown()
"""
    res = run_python(code, env={"GERNAS_EXPORTER": "console", "GERNAS_SERVICE_NAME": "filed",
                                "GERNAS_FILE_OUTPUT_DIR": str(tmp_path), "GERNAS_METRIC_INTERVAL_MS": "1000"})
    assert res.returncode == 0, res.stderr
    traces = list((tmp_path / "traces").glob("*.jsonl"))
    assert traces and "agent.run" in traces[0].read_text()
    assert list((tmp_path / "metrics").glob("*.jsonl")) and list((tmp_path / "logs").glob("*.jsonl"))


@pytest.mark.parametrize("kind", ["console", "pretty", "file"])
def test_init_with_each_local_sink(tmp_path, kind):
    code = """
import gernas
h = gernas.init_gernas()
@gernas.trace_llm("m", provider="p")
def call():
    return {"usage": {"prompt_tokens": 1, "completion_tokens": 1}}
call(); h.flush(); h.shutdown()
print("OK")
"""
    res = run_python(code, env={"GERNAS_EXPORTER": kind, "GERNAS_FILE_OUTPUT_DIR": str(tmp_path)})
    assert res.returncode == 0 and "OK" in res.stdout, res.stderr


def test_init_http_transport_against_dead_collector():
    code = """
import gernas
h = gernas.init_gernas()
gernas.trace_tool("x")(lambda: 1)()
h.flush(2000); h.shutdown()
print(gernas.get_dropped_span_count())
"""
    res = run_python(code, env={"GERNAS_OTLP_ENDPOINT": "http://127.0.0.1:1", "GERNAS_OTLP_TRANSPORT": "http",
                                "GERNAS_OTLP_TIMEOUT_S": "1", "GERNAS_OTLP_API_KEY": "k"})
    assert res.returncode == 0, res.stderr
    assert int(res.stdout.strip().splitlines()[-1]) >= 1
