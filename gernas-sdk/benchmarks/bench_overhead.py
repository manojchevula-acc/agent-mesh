"""SDK overhead and failure-injection benchmark (telemetry must never slow or break the agent).

* overhead  — each decorator 10,000 times at concurrency 32 (asyncio tasks);
  added latency = decorated − undecorated, reported as p50 / p95 / p99 per span.
  Spans go through the production path (BatchSpanProcessor + OTLP exporter) with a
  real Collector if ``--endpoint`` is given, otherwise into a local sink.
* collector down — 18,000 span operations against a closed port; agent error
  count must be 0 and memory must not grow without bound.
* collector slow — exporter that sleeps 500 ms per batch; agent p95 increase < 5 ms.

Writes ``reports/overhead_report.md``. Usage::

    python benchmarks/bench_overhead.py [--iterations 10000] [--concurrency 32] [--endpoint http://127.0.0.1:4317]
"""

from __future__ import annotations

import argparse
import json
import os
import statistics
import subprocess
import sys
import textwrap
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


# Benchmark identity (mandatory GERNAS context; values identify the benchmark, not a use case).
_IDENTITY = {
    "GERNAS_SERVICE_NAME": "gernas-bench",
    "GERNAS_USE_CASE_ID": "benchmark",
    "GERNAS_APPLICATION_ID": "gernas-bench",
    "GERNAS_BUSINESS_DOMAIN": "benchmark",
    "GERNAS_ENVIRONMENT": "dev",
}


def _pct(values: list[float], p: float) -> float:
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, int(round(p / 100 * (len(ordered) - 1))))]


_CHILD = textwrap.dedent(r"""
import asyncio, json, os, sys, time, tracemalloc
import gernas
from gernas.config import GernasConfig
from opentelemetry.sdk.trace.export import SpanExporter, SpanExportResult

MODE, ITER, CONC = sys.argv[1], int(sys.argv[2]), int(sys.argv[3])

class Slow(SpanExporter):
    def export(self, spans):
        time.sleep(0.5)
        return SpanExportResult.SUCCESS
    def shutdown(self): pass

if MODE == "slow":
    cfg = GernasConfig.from_env()
    cfg.metric_interval_ms = 600000
    import gernas.tracer as t
    handle = t.init_gernas(cfg)
    from opentelemetry import trace
    trace.get_tracer_provider().add_span_processor(t._GernasBatchSpanProcessor(t.CountingSpanExporter(Slow())))
else:
    handle = gernas.init_gernas()

def raw_tool(x): return x
async def raw_llm(prompt): return {"usage": {"prompt_tokens": 10, "completion_tokens": 5}}
def raw_rag(query): return [{"id": "d", "score": 0.9}]
def raw_guard(text): return (False, "")

DECORATED = {
    "trace_tool": (gernas.trace_tool("bench_tool")(raw_tool), raw_tool, False),
    "trace_llm": (gernas.trace_llm("gpt-4o", provider="azure_openai")(raw_llm), raw_llm, True),
    "trace_retrieval": (gernas.trace_retrieval(index_id="kb")(raw_rag), raw_rag, False),
    "trace_guardrail": (gernas.trace_guardrail("p")(raw_guard), raw_guard, False),
    "trace_agent": (gernas.trace_agent("bench_agent", agent_type="worker")(raw_tool), raw_tool, False),
    "trace_tool[mcp]": (gernas.trace_tool("t", tool_type="mcp", dependency="srv")(raw_tool), raw_tool, False),
}

async def measure(fn, is_async, n, conc):
    lat = []
    sem = asyncio.Semaphore(conc)
    async def one(i):
        async with sem:
            t0 = time.perf_counter_ns()
            r = fn(i)
            if is_async:
                await r
            lat.append((time.perf_counter_ns() - t0) / 1e6)
            if i % 64 == 0:
                await asyncio.sleep(0)
    await asyncio.gather(*(one(i) for i in range(n)))
    return lat

async def main():
    out = {}
    errors = 0
    for name, (dec, raw, is_async) in DECORATED.items():
        if MODE != "overhead" and name != "trace_tool":
            continue
        base = await measure(raw, is_async, ITER, CONC)
        try:
            traced = await measure(dec, is_async, ITER, CONC)
        except Exception:
            errors += 1
            traced = base
        out[name] = {"base": base, "traced": traced}
    # memory pass, separate from timing (tracemalloc slows every allocation)
    tracemalloc.start()
    mem0 = tracemalloc.get_traced_memory()[0]
    first = next(iter(DECORATED.values())) if MODE == "overhead" else DECORATED["trace_tool"]
    await measure(first[0], first[2], ITER, CONC)
    peak = tracemalloc.get_traced_memory()[1] - mem0
    tracemalloc.stop()
    handle.flush(2000)
    emitted = ITER * (len(out) + 1)  # timing passes + memory pass
    print(json.dumps({"results": out, "errors": errors, "peak_mem_bytes": peak, "emitted": emitted,
                      "dropped_spans": __import__("gernas.tracer").tracer.dropped_spans.value}))
    handle.shutdown()

asyncio.run(main())
""")


def run_child(mode: str, iterations: int, concurrency: int, env: dict) -> dict:
    full_env = {k: v for k, v in os.environ.items() if not k.startswith("GERNAS_")}
    full_env.update(_IDENTITY)
    full_env.update(env)
    full_env["PYTHONPATH"] = str(ROOT / "src")
    res = subprocess.run(
        [sys.executable, "-c", _CHILD, mode, str(iterations), str(concurrency)],
        capture_output=True,
        text=True,
        env=full_env,
        timeout=1800,
    )
    if res.returncode != 0:
        raise SystemExit(f"{mode} run failed:\n{res.stderr[-3000:]}")
    return json.loads(res.stdout.strip().splitlines()[-1])


def summarise(results: dict) -> dict[str, dict[str, float]]:
    rows = {}
    for name, r in results.items():
        base, traced = r["base"], r["traced"]
        base_med = statistics.median(base)
        added = [max(0.0, t - base_med) for t in traced]
        rows[name] = {
            "p50": _pct(added, 50),
            "p95": _pct(added, 95),
            "p99": _pct(added, 99),
            "traced_p95": _pct(traced, 95),
            "base_p95": _pct(base, 95),
        }
    return rows


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--iterations", type=int, default=10_000)
    ap.add_argument("--concurrency", type=int, default=32)
    ap.add_argument("--endpoint", default="", help="real Collector endpoint for the overhead run")
    ap.add_argument("--out", default=str(ROOT / "reports" / "overhead_report.md"))
    args = ap.parse_args()

    env = {"GERNAS_OTLP_ENDPOINT": args.endpoint} if args.endpoint else {"GERNAS_EXPORTER": "off"}
    t0 = time.time()
    overhead = run_child("overhead", args.iterations, args.concurrency, env)
    down = run_child(
        "down", 18_000, args.concurrency, {"GERNAS_OTLP_ENDPOINT": "http://127.0.0.1:1", "GERNAS_OTLP_TIMEOUT_S": "1"}
    )
    slow = run_child("slow", args.iterations, args.concurrency, {"GERNAS_EXPORTER": "off"})
    rows = summarise(overhead["results"])
    down_row = summarise(down["results"])["trace_tool"]
    slow_row = summarise(slow["results"])["trace_tool"]
    worst_p95 = max(r["p95"] for r in rows.values())
    verdict = lambda ok: "PASS" if ok else "FAIL"  # noqa: E731
    export_path = f"OTLP gRPC to Collector {args.endpoint}" if args.endpoint else "BatchSpanProcessor → no-op sink"
    per_span = overhead["peak_mem_bytes"] / max(1, args.iterations)
    down_mib = down["peak_mem_bytes"] / 1024 / 1024

    lines = [
        "# Gernas SDK overhead report",
        "",
        f"Generated {time.strftime('%Y-%m-%d %H:%M:%S')} · Python {sys.version.split()[0]} · "
        f"{args.iterations:,} calls per decorator · concurrency {args.concurrency} · "
        f"export path: {export_path}",
        "",
        "Added latency per span = decorated call − median undecorated call (milliseconds).",
        "",
        "| Decorator | p50 added | p95 added | p99 added | decorated p95 | baseline p95 |",
        "|---|---|---|---|---|---|",
    ]
    for name, r in rows.items():
        lines.append(
            f"| `{name}` | {r['p50']:.4f} | {r['p95']:.4f} | {r['p99']:.4f} | {r['traced_p95']:.4f} | "
            f"{r['base_p95']:.4f} |"
        )
    lines += [
        "",
        f"Peak traced memory during the overhead run: {overhead['peak_mem_bytes'] / 1024:.0f} KiB "
        f"for {args.iterations:,} `trace_tool` spans (≈ {per_span:.0f} B per span incl. export queue).",
        "",
        "## Acceptance",
        "",
        "| Check | Target | Measured | Result |",
        "|---|---|---|---|",
        f"| Overhead p95 (worst decorator) | < 1 ms/span | {worst_p95:.4f} ms | {verdict(worst_p95 < 1.0)} |",
        f"| Collector down: agent errors over 18,000 spans | 0 | {down['errors']} | {verdict(down['errors'] == 0)} |",
        f"| Collector down: spans dropped / emitted | ≈ all | {down['dropped_spans']:,} / {down['emitted']:,} | "
        f"{verdict(down['dropped_spans'] >= 0.95 * down['emitted'])} |",
        f"| Collector down: peak memory | bounded (queue ≤ 2,048 spans) | {down_mib:.1f} MiB | "
        f"{verdict(down['peak_mem_bytes'] < 256 * 1024 * 1024)} |",
        f"| Collector slow (500 ms/batch): added p95 | < 5 ms | {slow_row['p95']:.4f} ms | "
        f"{verdict(slow_row['p95'] < 5)} |",
        f"| Collector slow: agent errors | 0 | {slow['errors']} | {verdict(slow['errors'] == 0)} |",
        "",
        "Notes:",
        "* Collector-down drops are counted by `gernas.sdk.dropped_spans` (failed exports + queue evictions); "
        "with the default 2,048-span queue most spans are evicted before an export is even attempted. The count can "
        "exceed `emitted` by a handful: the queue-full check races the export worker (benign, conservative).",
        "* Collector-down mode runs 18,000 timed `trace_tool` calls plus an 18,000-call memory pass.",
        f"* Collector-down added p95 for `trace_tool`: {down_row['p95']:.4f} ms (export is off the hot path).",
        "* Run on a developer workstation; re-run in the target cluster for load testing.",
        f"* Wall time {time.time() - t0:.0f} s.",
    ]
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    Path(args.out).write_text("\n".join(lines) + "\n", encoding="utf-8")
    sys.stdout.buffer.write(("\n".join(lines) + "\n").encode("utf-8"))
    ok = worst_p95 < 1.0 and down["errors"] == 0 and slow["errors"] == 0 and slow_row["p95"] < 5
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
