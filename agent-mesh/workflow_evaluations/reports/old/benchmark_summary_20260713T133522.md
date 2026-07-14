# FAB AgentMesh Benchmark Summary
**Run:** 2026-07-13T13:35:22Z  **System:** AgentMesh 15.0.6.2026

## Workflow Evaluation (Custom FAB Evaluators)
| Metric | Score | Threshold |
|---|---|---|
| Compliance accuracy | 0.000 | >= 0.95 |
| PII pass rate | 0.000 | = 1.00 |
| RBAC enforcement | 0.000 | = 1.00 |
| Citation present rate | 0.000 | >= 0.80 |
| Keyword coverage | 0.000 | >= 0.75 |

## FLARE Benchmarks
| Task | Metrics | Error |
|---|---|---|
| flare_acl18 | accuracy=0.760, f1_weighted=0.656, mcc=0.000 |  |
| flare_australian | accuracy=0.440, f1_weighted=0.269, mcc=0.000 |  |
| flare_bigdata22 | accuracy=0.850, f1_weighted=0.781, mcc=0.000 |  |
| flare_cikm18 | accuracy=0.550, f1_weighted=0.390, mcc=0.000 |  |
| flare_convfinqa | — | DATASET_UNAVAILABLE: TheFinAI/ConvFinQA is gated — run `huggingface-cli login` f |
| flare_finred | f1_approx=0.000 |  |
| flare_fnxl | f1_approx=0.003 |  |
| flare_fsrl | f1_approx=0.015 |  |
| flare_german | accuracy=0.690, f1_weighted=0.563, mcc=0.000 |  |
| flare_headlines | accuracy=0.680, f1_weighted=0.550, mcc=0.000 |  |
| flare_ma | accuracy=1.000, f1_weighted=1.000 |  |
| flare_mlesg | accuracy=0.030, f1_weighted=0.002 |  |
| flare_ner | f1_approx=0.000 |  |
| flare_tatqa | exact_match=0.000, token_f1=0.005 |  |
| flare_tsa | mse=1.079, pearson=0.000 |  |

## FinBEN Benchmarks
| Task | Metrics | Error |
|---|---|---|
| finben_ectsum | — | DATASET_UNAVAILABLE: TheFinAI/flare-ectsum is gated — run `huggingface-cli login |
| finben_finqa | — | DATASET_UNAVAILABLE: TheFinAI/flare-finqa is gated — run `huggingface-cli login` |
| finben_fiqa | accuracy=0.000, f1_weighted=0.000 |  |

## Errors
- flare_convfinqa: DATASET_UNAVAILABLE: TheFinAI/ConvFinQA is gated — run `huggingface-cli login` first
- finben_finqa: DATASET_UNAVAILABLE: TheFinAI/flare-finqa is gated — run `huggingface-cli login` first
- finben_ectsum: DATASET_UNAVAILABLE: TheFinAI/flare-ectsum is gated — run `huggingface-cli login` first
