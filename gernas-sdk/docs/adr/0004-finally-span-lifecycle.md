# ADR 0004 — `finally`-based span lifecycle for streams

**Status:** accepted (2026-10-16)

**Context.** Beacon Kit closed a generator's span as soon as the generator object was created, so it recorded nothing. A stream can end in four ways: exhaustion, an error, a client disconnect (`close()` or `aclose()`), or garbage collection.

**Decision.**
- The span starts on the first iteration.
- The span's context is attached only while the wrapped generator runs. It is detached before each `yield`, so the caller's context never leaks.
- The span ends in a `finally` block that covers all four exits.
- The async wrapper explicitly closes the inner iterator, so `aclose()` closes the span immediately rather than waiting for garbage collection.
- OTel has no CANCELLED status code. A cancellation is recorded as `gernas.llm.stream_outcome=cancelled` with status UNSET. It is neither OK nor ERROR, so error-rate SLIs are not polluted.

**Consequences.**
- TTFT is recorded only when a first chunk is produced.
- Tokens per second is recorded only for completed streams.
- `tests/test_streaming.py` covers every exit path, including garbage collection.
