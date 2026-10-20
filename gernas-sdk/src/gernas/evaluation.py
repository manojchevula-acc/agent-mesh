"""Quality evaluation scores (Blueprint §6 Quality: groundedness, relevance, faithfulness,
answer completeness, citation quality, hallucination indicators, toxicity / safety,
refusal rate — "where evaluation is enabled").

Evaluators (online judges or offline evaluation jobs) record results through the SDK
instead of writing their own spans or metrics:

* an ``evaluation.score`` span — child of the current context, so an in-process judge
  lands inside the evaluated run; an offline job continues the evaluated trace by
  attaching its headers (``gernas.attach_headers``) or passes ``agent_run_id`` so the
  data plane can join on it;
* the ``gernas.evaluation.score`` histogram (dims: metric, agent, passed).

No evaluated text (question, answer, context) is ever recorded — only the score.
"""

from __future__ import annotations

from opentelemetry.trace import SpanKind, Status, StatusCode

from gernas._metrics import observe
from gernas.attributes import REGISTRY, Ctx, Metric, SpanName
from gernas.decorators._core import stamp_context, stamp_outcome
from gernas.run_state import current_run
from gernas.tracer import get_tracer, safe_set

EVALUATION_METRICS: tuple[str, ...] = REGISTRY[Ctx.EVALUATION_METRIC].enum


def record_evaluation(
    metric: str,
    score: float,
    *,
    passed: bool | None = None,
    evaluator: str | None = None,
    agent_id: str | None = None,
    agent_run_id: str | None = None,
) -> None:
    """Record one quality score in [0, 1] for the current (or the given) agent run.

    ``metric`` must be one of :data:`EVALUATION_METRICS`. Raises ``ValueError`` for an
    unknown metric or an out-of-range score (a caller bug, not a telemetry failure).
    """
    if metric not in EVALUATION_METRICS:
        raise ValueError(f"evaluation metric must be one of {EVALUATION_METRICS}")
    if not 0.0 <= float(score) <= 1.0:
        raise ValueError("evaluation score must be between 0 and 1")
    try:
        run = current_run()
        span = get_tracer().start_span(SpanName.EVALUATION, kind=SpanKind.INTERNAL)
        try:
            stamp_context(span, SpanName.EVALUATION)
            if run is None:  # offline evaluator: identify the evaluated run explicitly
                safe_set(span, Ctx.AGENT_ID, agent_id)
                safe_set(span, Ctx.AGENT_RUN_ID, agent_run_id)
            safe_set(span, Ctx.EVALUATION_METRIC, metric)
            safe_set(span, Ctx.EVALUATION_SCORE, float(score))
            safe_set(span, Ctx.EVALUATION_PASSED, passed)
            safe_set(span, Ctx.EVALUATOR, evaluator)
            stamp_outcome(span, None)
            span.set_status(Status(StatusCode.OK))
        finally:
            span.end()
        observe(
            Metric.EVALUATION_SCORE,
            float(score),
            {
                Ctx.EVALUATION_METRIC: metric,
                Ctx.AGENT_ID: run.agent_id if run else agent_id,
                Ctx.EVALUATION_PASSED: passed,
            },
        )
    except Exception:  # noqa: BLE001 - telemetry must never break the evaluator
        pass
