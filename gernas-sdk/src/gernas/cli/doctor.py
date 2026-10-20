"""``gernas doctor`` — onboarding preflight.

Checks, in the order a team onboards:

1. configuration — GERNAS_* variables parse and every mandatory one is present;
2. collector     — the OTLP endpoint accepts a TCP connection (with latency);
3. export        — a test ``agent.run`` span is exported and accepted;
4. context       — the test span carries the GERNAS identity / execution context;
5. opik          — (optional, ``--opik``) the test trace is visible in Opik.

Prints PASS/FAIL per check; exit code 0 = all pass, 1 = any fail.
"""

from __future__ import annotations

import argparse
import json
import os
import socket
import sys
import time
import urllib.parse
import urllib.request
from collections.abc import Sequence
from typing import Any

from opentelemetry.sdk.trace import ReadableSpan
from opentelemetry.sdk.trace.export import SpanExporter, SpanExportResult

from gernas.attributes import DOCTOR_CONTEXT


class _Probe(SpanExporter):
    """Exports to the real exporter and remembers what happened."""

    def __init__(self, inner: SpanExporter) -> None:
        self.inner = inner
        self.spans: list[ReadableSpan] = []
        self.results: list[Any] = []

    def export(self, spans: Sequence[ReadableSpan]) -> SpanExportResult:
        self.spans.extend(spans)
        try:
            result = self.inner.export(spans)
        except Exception as exc:  # noqa: BLE001
            self.results.append(repr(exc))
            return SpanExportResult.FAILURE
        self.results.append(result)
        return result

    def shutdown(self) -> None:
        self.inner.shutdown()


def _host_port(endpoint: str, transport: str) -> tuple[str, int]:
    parsed = urllib.parse.urlparse(endpoint if "://" in endpoint else f"http://{endpoint}")
    default = 4318 if transport == "http" else 4317
    return parsed.hostname or "localhost", parsed.port or default


def _opik_has(run_id: str, timeout_s: float) -> tuple[bool, str]:
    key, workspace = os.getenv("OPIK_API_KEY"), os.getenv("OPIK_WORKSPACE")
    project = os.getenv("OPIK_PROJECT_NAME", "Default Project")
    base = os.getenv("OPIK_URL_OVERRIDE", "https://www.comet.com/opik/api").rstrip("/")
    if not key or not workspace:
        return False, "OPIK_API_KEY / OPIK_WORKSPACE not set"
    url = f"{base}/v1/private/spans?project_name={urllib.parse.quote(project)}&size=100"
    deadline = time.monotonic() + timeout_s
    last = ""
    while time.monotonic() < deadline:
        try:
            req = urllib.request.Request(url, headers={"authorization": key, "Comet-Workspace": workspace})
            with urllib.request.urlopen(req, timeout=10) as resp:  # noqa: S310 - fixed https URL
                body = resp.read().decode("utf-8", "replace")
            if run_id in body:
                return True, f"found in project {project!r}"
        except Exception as exc:  # noqa: BLE001
            last = str(exc)
        time.sleep(3)
    return False, f"not found in project {project!r} within {timeout_s:.0f}s" + (
        f" (last error: {last})" if last else ""
    )


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="gernas doctor", description=__doc__.splitlines()[0])
    ap.add_argument("--opik", action="store_true", help="also verify the test trace reached Opik")
    ap.add_argument("--opik-timeout", type=float, default=60.0)
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)
    results: list[dict[str, Any]] = []

    def record(name: str, ok: bool, detail: str) -> None:
        results.append({"check": name, "ok": ok, "detail": detail})

    # 1. configuration
    from gernas.config import GernasConfig, GernasConfigError

    try:
        cfg = GernasConfig.from_env()
        record("configuration", True, f"GERNAS_* valid (service {cfg.service_name!r}, exporter {cfg.exporter})")
    except GernasConfigError as exc:
        record("configuration", False, str(exc))
        return _finish(results, args.json)

    # 2. collector reachability
    if cfg.exporter == "otlp":
        host, port = _host_port(cfg.otlp_endpoint, cfg.otlp_transport)
        t0 = time.perf_counter()
        try:
            with socket.create_connection((host, port), timeout=3):
                record("collector", True, f"{host}:{port} reachable in {(time.perf_counter() - t0) * 1000:.1f} ms")
        except OSError as exc:
            record("collector", False, f"{host}:{port} unreachable: {exc}")
    else:
        record("collector", True, f"skipped (GERNAS_EXPORTER={cfg.exporter})")

    # 3. test span export
    import gernas
    from gernas.exporters import build_span_exporter
    from gernas.run_state import current_run
    from gernas.tracer import get_handle

    probe = _Probe(build_span_exporter(cfg))
    gernas.init_gernas(cfg, span_exporter=probe)
    run_id: dict[str, str] = {}

    @gernas.trace_agent("gernas-doctor", agent_type="diagnostic", name="Gernas Doctor")
    def _doctor_run() -> str:
        run = current_run()
        run_id["id"] = run.agent_run_id if run is not None else ""
        return "ok"

    _doctor_run()
    handle = get_handle()
    if handle is not None:
        handle.flush(10_000)
    ok = bool(probe.results) and all(r is SpanExportResult.SUCCESS for r in probe.results)
    record(
        "export", ok, f"test span {'accepted' if ok else 'rejected'} ({probe.results}) agent_run_id={run_id.get('id')}"
    )

    # 4. canonical context
    span = probe.spans[-1] if probe.spans else None
    if span is None:
        record("context", False, "no test span captured")
    else:
        present = {**dict(span.resource.attributes), **dict(span.attributes or {})}
        absent = [k for k in DOCTOR_CONTEXT if k not in present]
        record(
            "context",
            not absent,
            f"{len(DOCTOR_CONTEXT) - len(absent)}/{len(DOCTOR_CONTEXT)} context fields present"
            + (f"; missing {absent}" if absent else ""),
        )

    # 5. opik (optional)
    if args.opik:
        found, detail = _opik_has(run_id.get("id", "?"), args.opik_timeout)
        record("opik", found, detail)

    if handle is not None:
        handle.shutdown()
    return _finish(results, args.json)


def _finish(results: list[dict[str, Any]], as_json: bool) -> int:
    ok = all(r["ok"] for r in results)
    if as_json:
        print(json.dumps({"passed": ok, "checks": results}, indent=2))
    else:
        for r in results:
            print(f"[{'PASS' if r['ok'] else 'FAIL'}] {r['check']:<14} {r['detail']}")
        print("RESULT: " + ("PASS" if ok else "FAIL"))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
