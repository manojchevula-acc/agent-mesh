# Gernas SDK overhead report (WP1-CORE-07)

Generated 2026-10-16 20:43:08 · Python 3.14.6 · 10,000 calls per decorator · concurrency 32 · export path: BatchSpanProcessor → no-op sink

Added latency per span = decorated call − median undecorated call (milliseconds).

| Decorator | p50 added | p95 added | p99 added | decorated p95 | baseline p95 |
|---|---|---|---|---|---|
| `trace_tool` | 0.0994 | 0.1515 | 0.3085 | 0.1517 | 0.0003 |
| `trace_llm` | 0.1440 | 0.2032 | 0.4130 | 0.2036 | 0.0006 |
| `trace_rag` | 0.0914 | 0.1298 | 0.3010 | 0.1301 | 0.0004 |
| `trace_guardrail` | 0.0600 | 0.0764 | 0.1862 | 0.0766 | 0.0002 |
| `trace_agent` | 0.1225 | 0.1525 | 0.3353 | 0.1526 | 0.0002 |
| `trace_mcp` | 0.0906 | 0.1111 | 0.2561 | 0.1113 | 0.0002 |

Peak traced memory during the overhead run: 14768 KiB for 10,000 `trace_tool` spans (≈ 1512 B per span incl. export queue).

## Acceptance

| Check | Target | Measured | Result |
|---|---|---|---|
| Overhead p95 (worst decorator) | < 1 ms/span | 0.2032 ms | PASS |
| Collector down: agent errors over 18,000 spans | 0 | 0 | PASS |
| Collector down: spans dropped / emitted | ≈ all | 36,000 / 36,000 | PASS |
| Collector down: peak memory | bounded (queue ≤ 2,048 spans) | 31.6 MiB | PASS |
| Collector slow (500 ms/batch): added p95 | < 5 ms | 0.0967 ms | PASS |
| Collector slow: agent errors | 0 | 0 | PASS |

Notes:
* Collector-down drops are counted by `gernas.sdk.dropped_spans` (failed exports + queue evictions); with the default 2,048-span queue most spans are evicted before an export is even attempted. The count can exceed `emitted` by a handful: the queue-full check races the export worker (benign, conservative).
* Collector-down mode runs 18,000 timed `trace_tool` calls plus an 18,000-call memory pass.
* Collector-down added p95 for `trace_tool`: 0.1574 ms (export is off the hot path).
* Run on a developer workstation; re-run in AKS staging for the WP1-E2E-03 load test.
* Wall time 40 s.
