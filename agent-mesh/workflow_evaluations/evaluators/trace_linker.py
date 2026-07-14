"""Attaches evaluation scores to their originating OTel spans.

Evaluation results written to reports/ are correlated with OTel trace IDs
so they appear alongside the trace in Grafana Tempo / Azure Monitor.
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict

# Allow importing src.config from the agent-mesh root
_ROOT = Path(__file__).resolve().parents[3]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

try:
    from src.config import Config
    _METRICS_ENABLED = Config.ENABLE_BUSINESS_METRICS
except Exception:
    _METRICS_ENABLED = True


class EvalTraceLinker:
    """Attaches evaluation scores to their originating OTel spans."""

    def record_eval_result(
        self,
        trace_id: str,
        eval_name: str,
        score: float,
        passed: bool,
        details: Dict[str, Any],
    ) -> None:
        """Emit an OTel span event with evaluation results as attributes.

        Uses the existing agent_framework tracer — does not create a new provider.
        Falls back silently if OTel is not configured (offline CI mode).
        """
        if not _METRICS_ENABLED:
            return
        try:
            self._emit_otel_event(trace_id, eval_name, score, passed, details)
        except Exception:
            pass

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _emit_otel_event(
        self,
        trace_id: str,
        eval_name: str,
        score: float,
        passed: bool,
        details: Dict[str, Any],
    ) -> None:
        from opentelemetry import trace
        from opentelemetry.trace import NonRecordingSpan, SpanContext, TraceFlags

        tracer = trace.get_tracer("agent_mesh", "1.0.0")

        # If a real trace_id (hex 32-char) is supplied, attempt to parent under it.
        span_context: SpanContext | None = None
        if trace_id and len(trace_id) == 32:
            try:
                span_context = SpanContext(
                    trace_id=int(trace_id, 16),
                    span_id=0,
                    is_remote=True,
                    trace_flags=TraceFlags(TraceFlags.SAMPLED),
                )
            except (ValueError, TypeError):
                span_context = None

        ctx = (
            trace.set_span_in_context(NonRecordingSpan(span_context))
            if span_context
            else None
        )

        with tracer.start_as_current_span(
            f"fab.eval.{eval_name}",
            context=ctx,
            kind=trace.SpanKind.INTERNAL,
        ) as span:
            span.set_attribute("fab.eval.name", eval_name)
            span.set_attribute("fab.eval.score", float(score))
            span.set_attribute("fab.eval.passed", bool(passed))
            span.set_attribute("fab.eval.details", json.dumps(details))
            span.set_attribute(
                "fab.eval.timestamp",
                datetime.now(timezone.utc).isoformat(),
            )
            span.set_attribute("fab.eval.trace_id", trace_id or "")
