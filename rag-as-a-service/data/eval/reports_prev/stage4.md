# Stage 4 — Answer Quality

_2026-08-11 08:42 UTC_

| | |
|---|---|
| generator_text | `openai/gpt-oss-120b` |
| generator_vision | `qwen/qwen3.6-27b` |
| final_top_k | `5` |
| cases | `2` |
| judge | `llama-3.3-70b-versatile` |

## Metrics

| Metric | Score | Bar | Verdict | Detail |
|---|---|---|---|---|
| answer_correctness | 100.0% | ≥ 85% | pass | grade==2 over 2 cases |
| span_correctness | 87.5% | ≥ 85% | pass | per objective span, 2 cases |
| groundedness | 87.5% | ≥ 95% | **FAIL** | objective spans supported by context |
| answer_relevancy | 100.0% | ≥ 85% | pass | answer addresses the question asked |
| citation_validity | 100.0% | ≥ 100% | pass | [N]/[IN] resolve to retrieved items |
| citation_support | n/a | ≥ 90% | n/a | needs per-citation judging — not yet implemented |
| numeric_recall | 91.7% | ≥ 90% | pass | over 2 cases with numbers |
| abstention_accuracy | n/a | ≥ 100% | n/a | 0/0 unanswerable cases correctly declined |
| subjective_span_ratio | 0.0% | ≤ 30% (watch) | pass | share of hedged spans |

> 2 metric(s) reported **n/a** — no verified ground truth, or a dependency was unavailable. n/a never fails a run; it means the check did not happen.

## Detail

| id | name | spans | cites | numeric | supported | correct | why |
|---|---|---|---|---|---|---|---|
| 1 | cbuae-text-dates | 2(0subj) | 5/5 | 100% | 2/2 | 2 | The answer correctly identifies the superseded circular and provides the issue a |
| 2 | cbuae-image-timeline | 4(0subj) | 2/2 | 83% | 3/4 | 2 | The answer accurately matches the gold answer on every material fact, including  |

## Notes

- No unanswerable cases in this selection — abstention_accuracy is n/a. pipeline_suite.yaml's out-of-corpus cases are the intended source.
- span_correctness and groundedness are computed over OBJECTIVE spans only; hedged/subjective statements have no truth value to score (RAG-Check III-A).
