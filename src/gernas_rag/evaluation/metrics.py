"""Metric definitions and pass/fail thresholds."""

# Full evaluation: requires a ground-truth reference for every test case.
METRIC_THRESHOLDS: dict[str, float] = {
    "faithfulness": 0.85,
    "answer_relevancy": 0.80,
    "context_precision": 0.75,
    "context_recall": 0.80,
}

# Reference-free evaluation: no ground truth needed. Judges the answer against
# the question and the retrieved contexts only, so it can run over arbitrary
# (e.g. production) queries. context_utilization is the no-reference variant of
# context_precision; context_recall has no reference-free equivalent and is dropped.
REFERENCE_FREE_THRESHOLDS: dict[str, float] = {
    "faithfulness": 0.85,
    "answer_relevancy": 0.80,
    "context_utilization": 0.75,
}
