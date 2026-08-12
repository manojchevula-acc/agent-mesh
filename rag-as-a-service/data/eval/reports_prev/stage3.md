# Stage 3 — Retrieval Quality & Ordering

_2026-08-11 08:37 UTC_

| | |
|---|---|
| final_top_k | `5` |
| reranker | `BAAI/bge-reranker-v2-m3` |
| multimodal | `True` |
| image_intent | `always` |
| cases | `2` |

## Metrics

| Metric | Score | Bar | Verdict | Detail |
|---|---|---|---|---|
| hit_rate_at_k | 100.0% | ≥ 95% | pass | 2/2 at k=5 |
| mrr | 100.0% | ≥ 80% | pass | 1/rank of first chunk from an expected document |
| context_precision | 100.0% | ≥ 70% | pass | share of retrieved chunks from an expected document |
| recall_at_k | 62.5% | ≥ 85% | **FAIL** | gold facts present in the retrieved context |
| image_relevancy | n/a | ≥ 70% (watch) | n/a | needs the judge pass — not yet implemented |
| score_gate_agreement | n/a | ≥ 75% (watch) | n/a | needs image_relevancy first |

> 2 metric(s) reported **n/a** — no verified ground truth, or a dependency was unavailable. n/a never fails a run; it means the check did not happen.

## Detail

| id | name | rank | facts | imgs | modality | missing |
|---|---|---|---|---|---|---|
| 1 | cbuae-text-dates | 1 | 2/2 | 1 | text |  |
| 2 | cbuae-image-timeline | 1 | 1/4 | 1 | figure | 30-Aug-2024, 30-Jun-2025, 31-Dec-2024 |

## Notes

- Image branch returned results on 2/2 queries (image_intent=always).
- Modality mismatches are reported in the table, never scored: a 'figure' gold source served as a table-text chunk is the D8 dual representation working as designed.
