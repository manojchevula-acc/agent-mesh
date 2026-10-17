# Gernas SDK overhead report

Generated 2026-10-17 00:37:39 · Python 3.14.6 · 3,000 calls per decorator · concurrency 32 · export path: BatchSpanProcessor → no-op sink

Added latency per span = decorated call − median undecorated call (milliseconds).

| Decorator | p50 added | p95 added | p99 added | decorated p95 | baseline p95 |
|---|---|---|---|---|---|
| `trace_tool` | 0.1611 | 0.3859 | 0.5710 | 0.3861 | 0.0002 |
| `trace_llm` | 0.2364 | 0.4418 | 0.7905 | 0.4423 | 0.0008 |
| `trace_retrieval` | 0.0949 | 0.1997 | 0.3346 | 0.2000 | 0.0005 |
| `trace_guardrail` | 0.0749 | 0.1449 | 0.2740 | 0.1451 | 0.0003 |
| `trace_agent` | 0.1274 | 0.2638 | 0.4389 | 0.2639 | 0.0002 |
| `trace_tool[mcp]` | 0.1174 | 0.2150 | 0.3464 | 0.2152 | 0.0004 |

Peak traced memory during the overhead run: 4601 KiB for 3,000 `trace_tool` spans (≈ 1570 B per span incl. export queue).

## Acceptance

| Check | Target | Measured | Result |
|---|---|---|---|
| Overhead p95 (worst decorator) | < 1 ms/span | 0.4418 ms | PASS |
| Collector down: agent errors over 18,000 spans | 0 | 0 | PASS |
| Collector down: spans dropped / emitted | ≈ all | 36,000 / 36,000 | PASS |
| Collector down: peak memory | bounded (queue ≤ 2,048 spans) | 31.3 MiB | PASS |
| Collector slow (500 ms/batch): added p95 | < 5 ms | 0.2230 ms | PASS |
| Collector slow: agent errors | 0 | 0 | PASS |

Notes:
* Collector-down drops are counted by `gernas.sdk.dropped_spans` (failed exports + queue evictions); with the default 2,048-span queue most spans are evicted before an export is even attempted. The count can exceed `emitted` by a handful: the queue-full check races the export worker (benign, conservative).
* Collector-down mode runs 18,000 timed `trace_tool` calls plus an 18,000-call memory pass.
* Collector-down added p95 for `trace_tool`: 0.2463 ms (export is off the hot path).
* Run on a developer workstation; re-run in the target cluster for load testing.
* Wall time 34 s.
