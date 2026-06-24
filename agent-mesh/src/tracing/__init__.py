"""Execution Trace Layer — public API.

Import these into CLI, API, or observability adapters.
The ContextVar-based design means any layer on the call stack
can emit events without changing function signatures.
"""
from src.tracing.execution_trace import (
    ExecutionEvent,
    ExecutionSummary,
    ExecutionTracer,
    get_active_tracer,
    set_active_tracer,
    clear_active_tracer,
    infer_route_and_scores,
)

__all__ = [
    "ExecutionEvent",
    "ExecutionSummary",
    "ExecutionTracer",
    "get_active_tracer",
    "set_active_tracer",
    "clear_active_tracer",
    "infer_route_and_scores",
]
