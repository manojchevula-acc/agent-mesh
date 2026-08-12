# Stage 2b — Vision Perception Fidelity

_2026-08-11 08:32 UTC_

| | |
|---|---|
| vision_model | `qwen/qwen3.6-27b` |
| max_side_px | `256` |
| transcriptions | `27` |

## Metrics

| Metric | Score | Bar | Verdict | Detail |
|---|---|---|---|---|
| numeric_fidelity | 85.8% | ≥ 85% | pass | over 2 crops with numbers |
| entity_recall | 59.5% | ≥ 80% | **FAIL** | over 2 crops |
| no_fabrication_rate | 84.6% | ≥ 95% | **FAIL** | over 2 crops |

## Detail

| document | page | numeric | entity | clean | detail |
|---|---|---|---|---|---|
| CBUAE_Circular_2024_BSE_047_AI_Governance | 1 | 80% | 50% | 77% | missed 15, 26, 38, 41 \| invented 1, 2, 2024 \| no labels (arrow, 2020=12, 2020=3 |
| CBUAE_Circular_2024_BSE_047_AI_Governance | 2 | 92% | 69% | 92% | missed 60day \| invented 26 \| no labels (60, days, gap |

## Notes

- 25 transcription(s) skipped: unverified, or asset_id not yet mapped by `python -m eval remap`.
- Crops were read at max_side_px=256. If numeric_fidelity is low, raise it before touching retrieval or the prompt.
