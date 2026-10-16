# IMPLEMENTATION_LOG.md
## GERNAS POD2 — WP1 Agent Telemetry SDK

> Authoritative audit trail for all implementation steps.
> Every completed step must have an entry here before reporting to the reviewer.
> Never fabricate line numbers — use symbol names when line numbers are unstable.
> Do not edit past entries. Add entries only.

---

## How to Add an Entry

Copy the template below. Fill in every field. Do not leave fields blank — write "none" or "N/A" explicitly.

```
## [Task ID] · Step [N] — [Short title]

**Date:** YYYY-MM-DD HH:MM UTC  
**Task ID:** [e.g. WP1-CORE-01]  
**Step:** [N of total]  
**Status:** COMPLETED | FAILED | BLOCKED  

### Objective
[One sentence: what this step was supposed to accomplish]

### Design Decision (if any)
[Describe any non-obvious choice made. Include: context, options considered, chosen approach, reason, trade-offs.]

### Implementation

[Describe what was actually built. Plain English, 2–5 sentences. Do not paste the code — describe the approach.]

### Files Created
- [path] — [what it contains]

### Files Modified
- [path] — [what changed, e.g. "Added safe_set() helper, modified init_gernas() to call it"]

### Symbols Added or Changed
- `ClassName.method_name` — [purpose]
- `function_name(args)` — [purpose]

### Tests Added
- `tests/path/to/test_file.py::test_function_name` — [what it tests]

### Tests Executed
```
[exact pytest command]
```

### Test Result
PASS [N/N] — [any notable output]
OR
FAIL — [reason and which tests failed]

### Acceptance Criteria (from IMPLEMENTATION_PLAN.md)
> [Quote the "Done when" text verbatim]

### Criteria Met?
[Yes / Partial (explain) / No (explain)]

### Known Issues
[none — or list issues with severity and whether they block the next step]

### Git Diff Summary
[Summary of what changed: files, lines added/removed, no full diff needed]

### Follow-up Items
[Any items that arose during this step that need tracking — bugs found, risks surfaced, scope questions]
```

---

## Example Entry (for reference — do not delete)

## WP1-CORE-01 · Step 5 — Graceful degradation via safe_set()

**Date:** 2026-05-14 09:42 UTC  
**Task ID:** WP1-CORE-01  
**Step:** 5 of 8  
**Status:** COMPLETED  

### Objective
Implement `safe_set(span, key, value)` so that a failed span attribute write never propagates an exception to the calling agent.

### Design Decision
Wrapped in a bare `except Exception` rather than catching specific OTel exceptions, because OTel's internal exceptions are not part of the public API and may change between SDK versions. This is deliberately broad — the goal is total exception isolation, not selective handling.

Considered: raising a warning log instead of silently incrementing the counter. Rejected: warning logs in a tight loop (e.g., 10,000 spans/sec) would themselves become a performance issue and a log noise problem.

### Implementation
Added `safe_set()` as a module-level function in `gernas/tracer.py`. Uses a `threading.local()` dropped-attribute counter rather than a global so it is safe under concurrent use. Returns `None` on both success and failure — callers do not need to check the return value.

Updated all existing decorator code in `gernas/decorators.py` to route through `safe_set()` instead of calling `span.set_attribute()` directly.

### Files Created
- none

### Files Modified
- `gernas/tracer.py` — Added `safe_set()`, `_dropped_attrs` thread-local counter, `get_dropped_count()` helper
- `gernas/decorators.py` — Replaced all `span.set_attribute()` calls with `safe_set(span, ...)` (7 call sites)

### Symbols Added or Changed
- `safe_set(span, key, value) -> None` — wraps set_attribute with exception isolation
- `get_dropped_count() -> int` — returns current thread's dropped attribute count
- `_dropped_attrs: threading.local` — thread-local counter

### Tests Added
- `tests/test_core.py::test_safe_set_on_ended_span_does_not_raise` — asserts no exception when span is already ended
- `tests/test_core.py::test_safe_set_increments_counter_on_failure` — asserts counter goes up when attribute write fails
- `tests/test_core.py::test_safe_set_counter_is_per_thread` — asserts two threads have independent counters

### Tests Executed
```
pytest tests/test_core.py -v -k "safe_set"
```

### Test Result
PASS 3/3

### Acceptance Criteria (from IMPLEMENTATION_PLAN.md)
> With the collector switched off the agent reports no errors and we get a count of what was dropped.

### Criteria Met?
Partial — `safe_set()` is implemented and tested. The full "collector switched off" scenario is tested in Step 7 (the collector-down test). This step proves the attribute-level isolation mechanism; Step 7 proves the full end-to-end behaviour.

### Known Issues
None — Step 7 will complete the acceptance criteria.

### Git Diff Summary
- `gernas/tracer.py`: +28 lines (safe_set, counter, helper)
- `gernas/decorators.py`: +7 lines changed (safe_set wrapping), 0 net new
- `tests/test_core.py`: +42 lines (3 new test functions)

### Follow-up Items
- Consider exposing `get_dropped_count()` in `init_gernas()` return value so callers can monitor it — raise as a design question in the next review.

---

## Implementation Entries

*Entries appear below in chronological order. Most recent at the bottom.*

<!-- BEGIN ENTRIES -->

<!-- END ENTRIES -->
