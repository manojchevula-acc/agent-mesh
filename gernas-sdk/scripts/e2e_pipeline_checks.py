"""End-to-end Collector checks: PDPL controls, signal routing, Opik delivery.

Requires the local Collector to be running (scripts/run_collector.py). Emits probe
telemetry from a separate process that leaks a raw ``gen_ai.prompt``, an unregistered
attribute and an Emirates ID in a registered field through the *raw OTel API*
(simulating a framework that bypasses the SDK), then inspects collector/out/*.json and
(if OPIK_* env is present) the Opik REST API. Prints PASS/FAIL per check and writes
reports/e2e_pipeline_checks.json.
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
sys.path.insert(0, str(ROOT / "src"))

from gernas.attributes import Ctx, Metric, SpanName  # noqa: E402

PROBE = r"""
import json, logging, gernas
from opentelemetry import trace
from gernas import Ctx
from gernas.run_state import current_run
h = gernas.init_gernas()
log = logging.getLogger("gernas.e2e")

@gernas.trace_tool("e2e_tool")
def tool():
    return 1

@gernas.trace_llm("gpt-4o", provider="azure_openai")
def chat():
    return {"usage": {"prompt_tokens": 12, "completion_tokens": 4}}

@gernas.trace_agent("e2e_probe_agent", agent_type="diagnostic")
def run():
    span = trace.get_current_span()
    span.set_attribute("gen_ai.prompt", "RAW PROMPT customer alice@fab.ae")      # bypasses the SDK hook
    span.set_attribute("app.unregistered_note", "should never reach a store")     # not in the registry
    span.set_attribute(Ctx.API_OPERATION, "id 784-1985-1234567-1")                # registered key, PII value
    tool()
    chat()
    log.warning("e2e probe log record for %s", current_run().agent_run_id)
    return current_run().agent_run_id

run_id = run()
h.flush(10000); h.shutdown()
print(json.dumps({"run_id": run_id}))
"""


def emit(service: str) -> str:
    env = {k: v for k, v in os.environ.items() if not k.startswith("GERNAS_")}
    env.update(
        {
            "GERNAS_OTLP_ENDPOINT": "http://127.0.0.1:4317",
            "GERNAS_SERVICE_NAME": service,
            "GERNAS_USE_CASE_ID": "UC-E2E-001",
            "GERNAS_APPLICATION_ID": "gernas-e2e",
            "GERNAS_BUSINESS_DOMAIN": "testing",
            "GERNAS_ENVIRONMENT": "dev",
            "GERNAS_METRIC_INTERVAL_MS": "1000",
            "PYTHONPATH": str(ROOT / "src"),
        }
    )
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
    url = f"https://www.comet.com/opik/api/v1/private/spans?project_name={urllib.parse.quote(project)}&size=200"
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


def _read(name: str) -> str:
    path = OUT / name
    return path.read_text(encoding="utf-8") if path.exists() else ""


def main() -> int:
    run_id = emit("e2e-probe")
    time.sleep(15)  # tail-sampling decision_wait + batch + file flush
    spans = [s for s in read_spans() if s["attributes"].get(Ctx.AGENT_RUN_ID) == run_id]
    agent = [s for s in spans if s["name"] == SpanName.AGENT]
    metrics_text, logs_text, traces_text = (_read(f"dataplane-{k}.json") for k in ("metrics", "logs", "traces"))
    checks: list[tuple[str, bool, str]] = []

    def check(name: str, ok: bool, detail: str = "") -> None:
        checks.append((name, bool(ok), detail))

    check("probe run reached the data plane", len(agent) == 1, f"{len(spans)} span(s) for the run")
    a = agent[0]["attributes"] if agent else {}
    check("raw gen_ai.prompt removed by the Collector (PDPL)", "gen_ai.prompt" not in a)
    check("unregistered attribute removed (deny-by-default)", "app.unregistered_note" not in a)
    check(
        "Emirates ID masked in a registered field",
        "784-1985-1234567-1" not in traces_text,
        str(a.get(Ctx.API_OPERATION)),
    )
    check(
        "envelope + resource preserved",
        bool(agent)
        and a.get(Ctx.USE_CASE_ID) == "UC-E2E-001"
        and agent[0]["resource"].get("service.name") == "e2e-probe",
    )
    check("tool and llm spans carry the run context", {s["name"] for s in spans} >= {SpanName.TOOL, SpanName.LLM})
    check(
        "signal routing: metrics in the metrics file only",
        Metric.TOOL_CALLS in metrics_text and Metric.TOOL_CALLS not in traces_text,
    )
    check(
        "signal routing: logs in the logs file only", run_id in logs_text and "e2e probe log record" not in traces_text
    )
    found = opik_contains(run_id, timeout_s=60)
    check("probe run delivered to Opik", bool(found), "OPIK_* not set - skipped" if found is None else "")

    width = max(len(c[0]) for c in checks)
    for name, ok, detail in checks:
        print(f"[{'PASS' if ok else 'FAIL'}] {name:<{width}}  {detail}")
    report = {"run_id": run_id, "checks": [{"check": n, "ok": ok, "detail": d} for n, ok, d in checks]}
    (ROOT / "reports").mkdir(exist_ok=True)
    (ROOT / "reports" / "e2e_pipeline_checks.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    ok = all(c[1] for c in checks)
    print("RESULT: " + ("PASS" if ok else "FAIL"))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
