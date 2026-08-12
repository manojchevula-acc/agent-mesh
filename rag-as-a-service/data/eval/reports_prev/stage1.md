# Stage 1 — Extraction & Layout Fidelity

_2026-08-11 08:31 UTC_

| | |
|---|---|
| extractor | `auto` |
| image_backend | `auto` |
| documents | `8` |

## Metrics

| Metric | Score | Bar | Verdict | Detail |
|---|---|---|---|---|
| heading_recall | 96.6% | ≥ 90% | pass | 86/89 |
| figure_recall | 100.0% | ≥ 95% | pass | 25/25 |
| table_recall | 92.0% | ≥ 95% | **FAIL** | 23/25 |
| page_accuracy | 100.0% | ≥ 95% | pass | figures+tables are matched per-page, so recall already encodes it |

## Detail

| document | headings | figures | tables | excluded | note |
|---|---|---|---|---|---|
| CBUAE_Circular_2024_BSE_047_AI_Governance | 22/22 | 4/4 | 2/2 |  |  |
| FAB_Corporate_Term_Loan_Product_Manual_v2_0 | 13/13 | 3/3 | 4/4 |  |  |
| FAB_Credit_Approval_Governance_Diagrams | 4/4 | 3/3 | 1/1 |  |  |
| FAB_Credit_Concentration_Limits_Policy_v1_8 | 9/9 | 3/3 | 4/4 |  |  |
| FAB_Credit_Pricing_Policy_v2_4 | 20/23 | 5/5 | 8/10 |  | missed: 7. Concentration and Exposure Limits; 7.1 Single Obligor Limits; 8. Policy Administration and Review |
| FAB_Executed_Term_Sheet_Scanned_Sample | 3/3 | 1/1 | 0/0 | 2 |  |
| FAB_Model_Risk_Management_Framework_v3_1 | 10/10 | 2/2 | 3/3 |  |  |
| FAB_Portfolio_Risk_Analytics_Report_Q2_2024 | 5/5 | 4/4 | 1/1 |  |  |

## Notes

- 2 figure region(s) carry must_detect=false and are excluded from figure_recall's denominator — regions this pipeline's ImageFilter is correct to drop (see each manifest entry's why_optional). They remain listed so the manifest still describes the page truthfully.
