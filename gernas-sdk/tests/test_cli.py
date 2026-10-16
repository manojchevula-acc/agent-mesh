"""``gernas doctor`` and ``gernas conformance``."""
from __future__ import annotations

import json
import socket
import threading

from conftest import run_python


def _doctor(env: dict) -> tuple[int, dict]:
    result = run_python("import sys, gernas.cli as c; sys.exit(c.main(['doctor', '--json']))", env=env, timeout=120)
    return result.returncode, json.loads(result.stdout[result.stdout.index("{"):])


def test_doctor_fails_without_endpoint():
    code, report = _doctor({})
    assert code == 1 and report["checks"][0]["check"] == "configuration"
    assert "GERNAS_OTLP_ENDPOINT" in report["checks"][0]["detail"]


def test_doctor_reports_unreachable_collector():
    code, report = _doctor({"GERNAS_OTLP_ENDPOINT": "http://127.0.0.1:1", "GERNAS_OTLP_TIMEOUT_S": "1"})
    checks = {c["check"]: c for c in report["checks"]}
    assert code == 1 and checks["collector"]["ok"] is False and checks["export"]["ok"] is False
    assert checks["context"]["ok"] is True  # the span itself is well-formed


def test_doctor_passes_with_a_local_sink():
    code, report = _doctor({"GERNAS_EXPORTER": "memory", "GERNAS_SERVICE_NAME": "svc", "GERNAS_USE_CASE_ID": "UC",
                            "GERNAS_APPLICATION_ID": "app", "GERNAS_BUSINESS_DOMAIN": "b", "GERNAS_ENVIRONMENT": "dev"})
    assert code == 0, report
    assert {c["check"] for c in report["checks"]} >= {"configuration", "collector", "export", "context"}


def test_doctor_tcp_check_passes_when_port_open():
    server = socket.socket()
    server.bind(("127.0.0.1", 0))
    server.listen(5)
    port = server.getsockname()[1]
    threading.Thread(target=lambda: [server.accept() for _ in range(3)], daemon=True).start()
    code, report = _doctor({"GERNAS_OTLP_ENDPOINT": f"http://127.0.0.1:{port}", "GERNAS_OTLP_TIMEOUT_S": "1"})
    assert {c["check"]: c["ok"] for c in report["checks"]}["collector"] is True
    server.close()


def test_conformance_cli_on_sdk_jsonl(tmp_path):
    good = {"name": "agent.run", "trace_id": "0x1", "span_id": "0x2", "status": "OK", "resource": {},
            "attributes": {"gernas.operation": "agent.run", "gernas.business_domain": "b", "gernas.use_case_id": "u",
                           "gernas.application_id": "a", "gernas.environment": "dev", "gernas.agent_id": "x",
                           "gernas.agent_name": "x", "gernas.agent_version": "1", "gernas.framework": "f",
                           "gernas.agent_run_id": "r", "gernas.termination_reason": "completed",
                           "gernas.duration_ms": 1.0}}
    bad = json.loads(json.dumps(good))
    bad["attributes"]["gen_ai.prompt"] = "leaked"
    (tmp_path / "good.jsonl").write_text(json.dumps(good) + "\n")
    (tmp_path / "bad.jsonl").write_text(json.dumps(bad) + "\n")
    ok = run_python(f"import sys, gernas.cli as c; sys.exit(c.main(['conformance', '--file', r'{tmp_path / 'good.jsonl'}',"
                    f" '--require-op', 'agent.run']))")
    fail = run_python(f"import sys, gernas.cli as c; sys.exit(c.main(['conformance', '--json', '--file', "
                      f"r'{tmp_path / 'bad.jsonl'}']))")
    assert ok.returncode == 0, ok.stdout
    assert fail.returncode == 1 and json.loads(fail.stdout)["violations"][0]["rule"] == "restricted"


def test_conformance_cli_reads_collector_otlp_json(tmp_path):
    line = {"resourceSpans": [{"resource": {"attributes": [{"key": "service.name", "value": {"stringValue": "s"}}]},
                               "scopeSpans": [{"spans": [{"name": "x", "traceId": "t", "spanId": "s",
                                                          "status": {"code": 1},
                                                          "attributes": [{"key": "gernas.bogus",
                                                                          "value": {"intValue": "3"}}]}]}]}]}
    (tmp_path / "otlp.json").write_text(json.dumps(line) + "\n")
    res = run_python(f"import sys, gernas.cli as c; sys.exit(c.main(['conformance', '--json', '--service', 's', "
                     f"'--file', r'{tmp_path / 'otlp.json'}']))")
    assert res.returncode == 1 and json.loads(res.stdout)["violations"][0]["rule"] == "unregistered"
