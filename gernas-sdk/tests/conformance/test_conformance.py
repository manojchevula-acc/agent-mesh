"""Conformance checker and CLI (doctor, conformance)."""

from __future__ import annotations

import json

import gernas
from gernas import Ctx, SpanName
from gernas.cli import main as cli
from gernas.conformance import NormalSpan, check, from_readable, load_file

from conftest import IDENTITY, run_python


def _span(name, attributes, span_id="1", parent=None, resource=None):
    return NormalSpan(name, "t", span_id, parent, "OK", attributes, resource or {})


_ENVELOPE = {
    Ctx.BUSINESS_DOMAIN: "b",
    Ctx.USE_CASE_ID: "u",
    Ctx.APPLICATION_ID: "a",
    Ctx.ENVIRONMENT: "dev",
    Ctx.EXECUTION_STATUS: "ok",
}


def _rules(report):
    return sorted({(v.rule, v.key) for v in report.violations})


def test_sdk_output_is_conformant(spans):
    @gernas.trace_tool("t")
    def tool():
        return 1

    @gernas.trace_agent("a", agent_type="worker")
    def run():
        tool()

    run()
    report = check(from_readable(spans()))
    assert report.passed and report.operations == {SpanName.AGENT: 1, SpanName.TOOL: 1}


def test_detects_missing_required_restricted_unregistered_and_enum():
    bad = _span(
        SpanName.TOOL,
        {
            **_ENVELOPE,
            Ctx.OPERATION: SpanName.TOOL,
            Ctx.TOOL_ID: "x",
            Ctx.AUTHORIZATION_DECISION: "maybe",
            "gen_ai.prompt": "raw",
            "gernas.made_up": 1,
        },
    )
    assert _rules(check([bad])) == [
        ("enum", Ctx.AUTHORIZATION_DECISION),
        ("required", Ctx.TOOL_TYPE),
        ("restricted", "gen_ai.prompt"),
        ("unregistered", "gernas.made_up"),
    ]


def test_detects_missing_run_context_and_duplicate_emission():
    agent = _span(
        SpanName.AGENT,
        {
            **_ENVELOPE,
            Ctx.OPERATION: SpanName.AGENT,
            Ctx.AGENT_ID: "a",
            Ctx.AGENT_NAME: "a",
            Ctx.AGENT_VERSION: "1",
            Ctx.AGENT_TYPE: "w",
            Ctx.AGENT_RUN_ID: "r",
            Ctx.TERMINATION_REASON: "completed",
            Ctx.STEP_COUNT: 1,
            Ctx.RETRY_COUNT: 0,
        },
        span_id="1",
    )
    tool = {
        **_ENVELOPE,
        Ctx.OPERATION: SpanName.TOOL,
        Ctx.TOOL_ID: "x",
        Ctx.TOOL_TYPE: "function",
        Ctx.AUTHORIZATION_DECISION: "allow",
    }
    outer = _span(SpanName.TOOL, tool, span_id="2", parent="1")
    inner = _span(SpanName.TOOL, tool, span_id="3", parent="2")
    assert _rules(check([agent, outer, inner])) == [
        ("duplicate", Ctx.TOOL_ID),
        ("run_context", Ctx.AGENT_ID),
        ("run_context", Ctx.AGENT_RUN_ID),
    ]


def test_envelope_may_sit_on_the_resource():
    span = _span(
        SpanName.MEMORY,
        {Ctx.OPERATION: SpanName.MEMORY, Ctx.MEMORY_OPERATION: "read", Ctx.EXECUTION_STATUS: "ok"},
        resource=_ENVELOPE,
    )
    assert check([span]).passed


def test_load_collector_file_and_cli(tmp_path, capsys):
    doc = {
        "resourceSpans": [
            {
                "resource": {"attributes": [{"key": k, "value": {"stringValue": v}} for k, v in _ENVELOPE.items()]},
                "scopeSpans": [
                    {
                        "spans": [
                            {
                                "name": SpanName.MEMORY,
                                "traceId": "t",
                                "spanId": "s",
                                "status": {"code": 1},
                                "attributes": [
                                    {"key": Ctx.OPERATION, "value": {"stringValue": SpanName.MEMORY}},
                                    {"key": Ctx.MEMORY_OPERATION, "value": {"stringValue": "read"}},
                                ],
                            }
                        ]
                    }
                ],
            }
        ]
    }
    path = tmp_path / "traces.json"
    path.write_text(json.dumps(doc) + "\n", encoding="utf-8")
    spans = load_file(path)
    assert spans[0].attributes[Ctx.MEMORY_OPERATION] == "read" and spans[0].status == "OK"
    assert cli(["conformance", "--file", str(path)]) == 0
    assert cli(["conformance", "--file", str(path), "--require-op", SpanName.AGENT]) == 1
    assert cli(["conformance", "--file", str(tmp_path / "missing.json")]) == 2
    assert cli(["conformance", "--file", str(path), "--json"]) == 0
    assert json.loads(capsys.readouterr().out.split("RESULT")[-1].split("\n", 1)[-1] or "{}") is not None


def test_cli_usage_and_unknown_command(capsys):
    assert cli([]) == 0 and "doctor" in capsys.readouterr().out
    assert cli(["nope"]) == 2


def test_doctor_reports_missing_configuration():
    result = run_python("from gernas.cli import main; import sys; sys.exit(main(['doctor']))")
    assert result.returncode == 1 and "GERNAS_SERVICE_NAME" in result.stdout


def test_doctor_passes_with_console_exporter():
    result = run_python(
        "from gernas.cli import main; import sys; sys.exit(main(['doctor', '--json']))",
        env={**IDENTITY, "GERNAS_EXPORTER": "console"},
    )
    assert result.returncode == 0, result.stdout + result.stderr
    report = json.loads(result.stdout[result.stdout.index('{\n  "passed"') :])
    assert report["passed"] and {c["check"] for c in report["checks"]} == {
        "configuration",
        "collector",
        "export",
        "context",
    }


def test_doctor_fails_when_the_collector_is_unreachable():
    result = run_python(
        "from gernas.cli import main; import sys; sys.exit(main(['doctor']))",
        env={**IDENTITY, "GERNAS_OTLP_ENDPOINT": "http://127.0.0.1:1", "GERNAS_OTLP_TIMEOUT_S": "1"},
        timeout=180,
    )
    assert result.returncode == 1 and "[FAIL] collector" in result.stdout


def test_client_server_pair_is_not_a_duplicate():
    tool = {
        **_ENVELOPE,
        Ctx.OPERATION: SpanName.TOOL,
        Ctx.TOOL_ID: "x",
        Ctx.TOOL_TYPE: "function",
        Ctx.AUTHORIZATION_DECISION: "allow",
    }
    client = _span(SpanName.TOOL, tool, span_id="1")
    client.kind = "CLIENT"
    server = _span(SpanName.TOOL, tool, span_id="2", parent="1")
    server.kind = "SERVER"
    assert check([client, server]).passed
    # without kinds (e.g. Opik's copy) the MCP client/server pair is recognised by tool_type
    mcp = {**tool, Ctx.TOOL_TYPE: "mcp"}
    assert check([_span(SpanName.TOOL, mcp, span_id="1"), _span(SpanName.TOOL, mcp, span_id="2", parent="1")]).passed
