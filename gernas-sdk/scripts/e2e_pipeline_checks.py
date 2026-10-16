"""End-to-end Collector checks: PDPL controls, kill switches, signal routing, Opik delivery.

Requires the local Collector to be running (scripts/run_collector.py). Emits
probe telemetry from three processes:

* ``e2e-probe``       — normal routing; leaks a raw ``gen_ai.prompt`` and an
  unregistered attribute through the *raw OTel API* (simulating a framework that
  bypasses the SDK), plus a registered field carrying an Emirates ID;
* ``e2e-kill-opik``   — ``GERNAS_EMIT_OPIK=false``;
* ``e2e-kill-dataplane`` — ``GERNAS_EMIT_DATAPLANE=false``.

Then inspects collector/out/*.json and (if OPIK_* env is present) the Opik REST
API, printing PASS/FAIL per check and writing reports/e2e_pipeline_checks.json.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "collector" / "out"

PROBE = r'''
import json, logging, sys, gernas
from opentelemetry import trace
h = gernas.init_gernas()
log = logging.getLogger("gernas.e2e")

@gernas.trace_agent("e2e_probe_agent")
def run():
    span = trace.get_current_span()
    span.set_attribute("gen_ai.prompt", "RAW PROMPT customer alice@fab.ae")      # bypasses the SDK hook
    span.set_attribute("app.unregistered_note", "should never reach a store")     # not in the registry
    span.set_attribute("gernas.tool.failure_reason", "id 784-1985-1234567-1")      # registered key, PII value
    log.warning("e2e probe log record for %s", gernas.current_run().agent_run_id)
    return gernas.current_run().agent_run_id

run_id = run()
gernas.AgenticMetrics().record_tool_call("success", "e2e_tool")
h.flush(10000); h.shutdown()
print(json.dumps({"run_id": run_id}))
'''


def emit(service: str, extra: dict) -> str:
    env = {k: v for k, v in os.environ.items() if not k.startswith("GERNAS_")}
    env.update({"GERNAS_OTLP_ENDPOINT": "http://127.0.0.1:4317", "GERNAS_SERVICE_NAME": service,
                "GERNAS_USE_CASE_ID": "UC-E2E-001", "GERNAS_APPLICATION_ID": "gernas-e2e",
                "GERNAS_BUSINESS_DOMAIN": "testing", "GERNAS_ENVIRONMENT": "dev",
                "GERNAS_METRIC_INTERVAL_MS": "1000", "PYTHONPATH": str(ROOT), **extra})
    res = subprocess.run([sys.executable, "-c", PROBE], capture_output=True, text=True, env=env, timeout=120)
    if res.returncode != 0:
        raise SystemExit(f"probe {service} failed: {res.stderr[-2000:]}")
    return json.loads(res.stdout.strip().splitlines()[-1])["run_id"]


def _attrs(items):
    out = {}
    for a in items or []:
        v = a.get("value", {})
        out[a["key"]] = next(iter(v.values())) if v else None
    return out


def read_spans() -> list[dict]:
    spans = []
    path = OUT / "dataplane-traces.json"
    if not path.exists():
        return spans
    for line in path.read_text(encoding="utf-8").splitlines():
        for rs in json.loads(line).get("resourceSpans", []):
            res = _attrs(rs.get("resource", {}).get("attributes"))
            for ss in rs.get("scopeSpans", []):
                for sp in ss.get("spans", []):
                    spans.append({"name": sp["name"], "resource": res, "attributes": _attrs(sp.get("attributes"))})
    return spans


def opik_contains(run_id: str, timeout_s: float = 60) -> bool | None:
    key, ws = os.getenv("OPIK_API_KEY"), os.getenv("OPIK_WORKSPACE")
    project = os.getenv("OPIK_PROJECT_NAME")
    if not (key and ws and project):
        return None
    url = (f"https://www.comet.com/opik/api/v1/private/spans?project_name={urllib.parse.quote(project)}"
           f"&size=200")
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        try:
            req = urllib.request.Request(url, headers={"authorization": key, "Comet-Workspace": ws})
            with urllib.request.urlopen(req, timeout=15) as resp:  # noqa: S310 - fixed https URL
                if run_id in resp.read().decode("utf-8", "replace"):
                    return True
        except Exception:  # noqa: BLE001
            pass
        time.sleep(4)
    return False


def main() -> int:
    ids = {
        "normal": emit("e2e-probe", {}),
        "kill_opik": emit("e2e-kill-opik", {"GERNAS_EMIT_OPIK": "false"}),
        "kill_dataplane": emit("e2e-kill-dataplane", {"GERNAS_EMIT_DATAPLANE": "false"}),
    }
    time.sleep(15)  # tail-sampling decision_wait + batch + file flush
    spans = read_spans()
    probe = [s for s in spans if s["attributes"].get("gernas.agent_run_id") == ids["normal"]]
    metrics_text = (OUT / "dataplane-metrics.json").read_text(encoding="utf-8") if (OUT / "dataplane-metrics.json").exists() else ""
    logs_text = (OUT / "dataplane-logs.json").read_text(encoding="utf-8") if (OUT / "dataplane-logs.json").exists() else ""
    traces_text = (OUT / "dataplane-traces.json").read_text(encoding="utf-8")
    checks: list[tuple[str, bool, str]] = []

    def check(name: str, ok: bool, detail: str = "") -> None:
        checks.append((name, bool(ok), detail))

    check("probe span reached the data plane", len(probe) == 1, f"{len(probe)} span(s)")
    a = probe[0]["attributes"] if probe else {}
    check("raw gen_ai.prompt removed by the Collector (PDPL)", "gen_ai.prompt" not in a)
    check("unregistered attribute removed (deny-by-default)", "app.unregistered_note" not in a)
    check("Emirates ID masked in a registered field", "784-1985-1234567-1" not in traces_text,
          str(a.get("gernas.tool.failure_reason")))
    check("redaction summary recorded", any(k.startswith("redaction.") for k in a), "redaction.* attributes")
    check("envelope + resource preserved", a.get("gernas.use_case_id") == "UC-E2E-001" and
          probe[0]["resource"].get("service.name") == "e2e-probe" if probe else False)
    check("kill switch: dataplane=false keeps spans out of the data plane",
          not any(s["attributes"].get("gernas.agent_run_id") == ids["kill_dataplane"] for s in spans))
    check("kill switch: opik=false still reaches the data plane",
          any(s["attributes"].get("gernas.agent_run_id") == ids["kill_opik"] for s in spans))
    check("signal routing: metrics in the metrics file only",
          "gernas.tool.calls.total" in metrics_text and "gernas.tool.calls.total" not in traces_text)
    check("signal routing: logs in the logs file only",
          ids["normal"] in logs_text and "e2e probe log record" not in traces_text)
    for label, key, expected in (("normal run in Opik", "normal", True), ("opik=false run absent from Opik", "kill_opik", False),
                                 ("dataplane=false run still in Opik", "kill_dataplane", True)):
        found = opik_contains(ids[key], timeout_s=60 if expected else 25)
        if found is None:
            check(label, False, "OPIK_* not set — skipped")
        else:
            check(label, found is expected, f"found={found}")

    width = max(len(c[0]) for c in checks)
    for name, ok, detail in checks:
        print(f"[{'PASS' if ok else 'FAIL'}] {name:<{width}}  {detail}")
    report = {"run_ids": ids, "checks": [{"check": n, "ok": ok, "detail": d} for n, ok, d in checks]}
    (ROOT / "reports").mkdir(exist_ok=True)
    (ROOT / "reports" / "e2e_pipeline_checks.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    ok = all(c[1] for c in checks)
    print("RESULT: " + ("PASS" if ok else "FAIL"))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
