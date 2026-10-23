"""CorrelationLogFilter: trace / run / request ids on application log records."""

from __future__ import annotations

import logging

import gernas
from gernas import SpanName

from conftest import attrs


def _record() -> logging.LogRecord:
    return logging.LogRecord("app", logging.INFO, __file__, 1, "msg", None, None)


def test_filter_stamps_correlation_and_app_fields(spans):
    flt = gernas.CorrelationLogFilter(app_fields=("request_id",))
    seen = {}

    @gernas.trace_agent("log_agent", agent_type="worker")
    def run():
        rec = _record()
        assert flt.filter(rec) is True
        seen.update(vars(rec))

    with gernas.request_context(session_id="s-l", task_id="t-l"), gernas.app_context(request_id="r-l"):
        run()
    agent = spans(SpanName.AGENT)[0]
    assert seen["trace_id"] == format(agent.context.trace_id, "032x")
    assert seen["agent_run_id"] == attrs(agent)[gernas.Ctx.AGENT_RUN_ID]
    assert (seen["session_id"], seen["task_id"], seen["request_id"]) == ("s-l", "t-l", "r-l")


def test_filter_keeps_explicit_extra_values():
    rec = _record()
    rec.session_id, rec.request_id = "s-explicit", "r-explicit"
    with gernas.request_context(session_id="s-ctx"), gernas.app_context(request_id="r-ctx"):
        gernas.CorrelationLogFilter(app_fields=("request_id",)).filter(rec)
    assert (rec.session_id, rec.request_id) == ("s-explicit", "r-explicit")


def test_filter_defaults_to_dash_outside_a_trace():
    rec = _record()
    gernas.CorrelationLogFilter(app_fields=("request_id",)).filter(rec)
    assert {rec.trace_id, rec.span_id, rec.agent_run_id, rec.task_id, rec.request_id} == {"-"}
