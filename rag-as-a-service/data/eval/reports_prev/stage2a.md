# Stage 2a — Index & Artifact Integrity

_2026-08-11 08:31 UTC_

| | |
|---|---|
| vectordb | `VectorDBProvider.QDRANT` |
| text_collection | `fab_gernas_docs (117 chunks)` |
| image_collection | `fab_gernas_images__siglip2_base_patch16_224__d768 (45 vectors)` |

## Metrics

| Metric | Score | Bar | Verdict | Detail |
|---|---|---|---|---|
| asset_resolvable_rate | 100.0% | ≥ 100% | pass | 45/45 vectors have bytes on disk |
| stub_coverage | 100.0% | ≥ 95% | pass | 45/45 images have a caption stub chunk |
| table_atomicity | 100.0% | ≥ 100% | pass | 22/22 table chunks retain a header row |
| orphan_rate | 30.8% | ≤ 10% (watch) | warn | 20/65 stored assets have no vector |

## Notes

- Structural invariants only: no ground truth needed, so any failure here is a defect rather than a tuning question.
