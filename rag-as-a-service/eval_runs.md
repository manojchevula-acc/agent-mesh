# Evaluation Summary

_generator (text): `openai/gpt-oss-120b` · generator (vision): `qwen/qwen3.6-27b` · judge: `llama-3.3-70b-versatile` · multimodal: side_car_

**Score: 9 pass · 1 partial · 3 fail out of 14 runs**

| ID | Name | Type | Sufficient | Correct | Grounded | Result | Why Failed |
|---|---|---|---|---|---|---|---|
| 1 | cbuae-text-dates | text | ✅ | 2/2 | ✅ | ✅ pass | — |
| 2 | cbuae-image-timeline | figure-as-text | ✅ | 2/2 | ✅ | ✅ pass | — |
| 4 | term-loan-text-eligibility | text | ✅ | 1/2 | ✅ | ⚠️ PARTIAL | Generator omitted audited financials + KYC/AML status. Context had the answer — generation failure. |
| 5 | term-loan-image-fee-schedule | figure-as-text | ✅ | 2/2 | ✅ | ✅ pass | — |
| 6 | approval-diagrams-image-escalation | true image | ✅ | 2/2 | ✅ | ✅ pass | — |
| 9 | conc-limits-image-single-obligor | figure-as-text | ✅ | 2/2 | ✅ | ✅ pass | — |
| 10 | conc-limits-image-sector | true image | ✅ | 2/2 | ✅ | ✅ pass | — |
| 11 | pricing-image-ftp-curve | figure-as-text | ✅ | 2/2 | ✅ | ✅ pass | — |
| 12 | mrm-image-dashboard | true image | ❌ | 1/2 | ✅ | ❌ RETRIEVAL | Chart extracted but unreadable at 256px — monthly bar labels (21% April) are sub-pixel. Needs higher resolution. |
| 13 | text-rcf-floor | text | ❌ | 0/2 | ✅ | ❌ RETRIEVAL | Table 3.2 (RCF floors) not retrieved — term loan Table 3.1 won the reranking. Wrong table selected. |
| 14 | text-mrm-tier | text | ✅ | 2/2 | ✅ | ✅ pass | — |
| 18 | text-worked-example | text | ✅ | 2/2 | ✅ | ✅ pass | — |
| 31 | ui-concentration-limits-corporate | figure-as-text | ❌ | 0/2 | ❌ | ❌ RETRIEVAL | Data chunk ranked below position 2 — cut off by `final_top_k=2` workaround. Passes with `final_top_k=5`. Eval config issue, not a pipeline failure. |
| 35 | unanswerable-retail-mortgage-ltv | unanswerable | — | — | — | ✅ correct refusal | — |

---

## Failure Analysis

### ⚠️ ID 4 — PARTIAL (generation failure)
**Missing from answer:** "at least two years of audited financial statements" and "clean KYC/AML status"  
**Fix:** Context was sufficient. Generator stopped after the first two criteria. Prompt should instruct the model to enumerate all listed criteria.

### ❌ ID 12 — RETRIEVAL (image resolution)
**Root cause:** Dashboard is a time-series bar chart. Ingested correctly (`pictures=2`) but at 256px the monthly value labels (~3px per character) are unreadable by Qwen. Text chunks have the threshold structure only, not the monthly values.  
**Fix:** Increase `RAG__LLM__VISION_IMAGE_MAX_SIDE_PX` to 512+ with `final_top_k=1`, or upgrade to Groq Dev tier.

### ❌ ID 13 — RETRIEVAL (wrong table retrieved)
**Root cause:** Retrieved Table 3.1 (Term Loans) instead of Table 3.2 (RCF floors) from `FAB_Credit_Pricing_Policy_v2_4`. The RCF table lost reranking to the term loan table for this query wording.  
**Fix:** Re-ingest `FAB_Credit_Pricing_Policy_v2_4.pdf` to ensure Table 3.2 is a standalone indexed chunk, or increase `final_top_k` to 5.

### ❌ ID 31 — RETRIEVAL (eval config, not a pipeline bug)
**Root cause:** Run with `final_top_k=2`. The data chunk with limit values ranked at position 3+. Case is `pipeline_verified: true` — it passes with `final_top_k=5`.  
**Fix:** Restore `final_top_k` to 5 and re-run.

---

## Raw Run History

# LLM-as-judge evaluation

_2026-08-10 07:49 UTC_

|                    |                              |
| ------------------ | ---------------------------- |
| generator (text)   | `openai/gpt-oss-120b`      |
| generator (vision) | `qwen/qwen3.6-27b`         |
| **judge**    | `llama-3.3-70b-versatile`  |
| multimodal         | `True` / mode `side_car` |

## Diagnosis

| Outcome | Count | Means                                              |
| ------- | ----- | -------------------------------------------------- |
| pass    | 1     | retrieved the right context AND answered correctly |

## Metrics

| Metric                          | Score      |
| ------------------------------- | ---------- |
| Context sufficiency (retrieval) | 1/1 (100%) |
| Answer correctness (end-to-end) | 1/1 (100%) |
| Groundedness (no hallucination) | 1/1 (100%) |

## Cases

| id    | name                 | sufficient | correct | grounded | diagnosis |
| ----- | -------------------- | ---------- | ------- | -------- | --------- |
| `2` | cbuae-image-timeline | True       | 2       | True     | pass      |

---

# LLM-as-judge evaluation

_2026-08-10 07:52 UTC_

|                    |                              |
| ------------------ | ---------------------------- |
| generator (text)   | `openai/gpt-oss-120b`      |
| generator (vision) | `qwen/qwen3.6-27b`         |
| **judge**    | `llama-3.3-70b-versatile`  |
| multimodal         | `True` / mode `side_car` |

## Diagnosis

| Outcome | Count | Means                                              |
| ------- | ----- | -------------------------------------------------- |
| pass    | 1     | retrieved the right context AND answered correctly |

## Metrics

| Metric                          | Score      |
| ------------------------------- | ---------- |
| Context sufficiency (retrieval) | 1/1 (100%) |
| Answer correctness (end-to-end) | 1/1 (100%) |
| Groundedness (no hallucination) | 1/1 (100%) |

## Cases

| id    | name                         | sufficient | correct | grounded | diagnosis |
| ----- | ---------------------------- | ---------- | ------- | -------- | --------- |
| `5` | term-loan-image-fee-schedule | True       | 2       | True     | pass      |

---

# LLM-as-judge evaluation

_2026-08-10 07:54 UTC_

|                    |                              |
| ------------------ | ---------------------------- |
| generator (text)   | `openai/gpt-oss-120b`      |
| generator (vision) | `qwen/qwen3.6-27b`         |
| **judge**    | `llama-3.3-70b-versatile`  |
| multimodal         | `True` / mode `side_car` |

## Diagnosis

| Outcome | Count | Means                                              |
| ------- | ----- | -------------------------------------------------- |
| pass    | 1     | retrieved the right context AND answered correctly |

## Metrics

| Metric                          | Score      |
| ------------------------------- | ---------- |
| Context sufficiency (retrieval) | 1/1 (100%) |
| Answer correctness (end-to-end) | 1/1 (100%) |
| Groundedness (no hallucination) | 1/1 (100%) |

## Cases

| id    | name                             | sufficient | correct | grounded | diagnosis |
| ----- | -------------------------------- | ---------- | ------- | -------- | --------- |
| `9` | conc-limits-image-single-obligor | True       | 2       | True     | pass      |

---

# LLM-as-judge evaluation

_2026-08-10 07:56 UTC_

|                    |                              |
| ------------------ | ---------------------------- |
| generator (text)   | `openai/gpt-oss-120b`      |
| generator (vision) | `qwen/qwen3.6-27b`         |
| **judge**    | `llama-3.3-70b-versatile`  |
| multimodal         | `True` / mode `side_car` |

## Diagnosis

| Outcome | Count | Means                                              |
| ------- | ----- | -------------------------------------------------- |
| pass    | 1     | retrieved the right context AND answered correctly |

## Metrics

| Metric                          | Score      |
| ------------------------------- | ---------- |
| Context sufficiency (retrieval) | 1/1 (100%) |
| Answer correctness (end-to-end) | 1/1 (100%) |
| Groundedness (no hallucination) | 1/1 (100%) |

## Cases

| id     | name                    | sufficient | correct | grounded | diagnosis |
| ------ | ----------------------- | ---------- | ------- | -------- | --------- |
| `11` | pricing-image-ftp-curve | True       | 2       | True     | pass      |

---

# LLM-as-judge evaluation

_2026-08-10 08:00 UTC_

|                    |                              |
| ------------------ | ---------------------------- |
| generator (text)   | `openai/gpt-oss-120b`      |
| generator (vision) | `qwen/qwen3.6-27b`         |
| **judge**    | `llama-3.3-70b-versatile`  |
| multimodal         | `True` / mode `side_car` |

## Diagnosis

| Outcome   | Count | Means                                                                |
| --------- | ----- | -------------------------------------------------------------------- |
| RETRIEVAL | 1     | **chunks did not contain the answer** — fix chunking/indexing |

## Metrics

| Metric                          | Score    |
| ------------------------------- | -------- |
| Context sufficiency (retrieval) | 0/1 (0%) |
| Answer correctness (end-to-end) | 0/1 (0%) |
| Groundedness (no hallucination) | 0/1 (0%) |

## Cases

| id     | name                              | sufficient | correct | grounded | diagnosis           |
| ------ | --------------------------------- | ---------- | ------- | -------- | ------------------- |
| `31` | ui-concentration-limits-corporate | False      | 0       | False    | **RETRIEVAL** |

## Failures in detail

### `31` ui-concentration-limits-corporate — RETRIEVAL

**Question:** What are the credit concentration limits for corporate counterparties?

**Gold answer:** Limits vary by classification, e.g. Corporate Investment Grade (AAA-BBB) up to AED 3.5bn (14% of Tier 1 capital), Corporate Sub-Investment Grade (BB) up to AED 1.2bn (5% T1), and Corporate Speculative (B and below) up to AED 0.4bn (1.6% T1).

**Generated:**
<think></think>
The user is asking for the credit concentration limits for corporate counterparties based on the provided context.

1. **Analyze the Request:**

   * Target: Credit concentration limits for corporate counterparties.
   * Source Material: Context [1], [2] and Figure [I1].
   * Constraints: Cite text with [N], figures with [IN], end with 'Sources:' block.
2. **Scan Context [1] and [2]:**

   * Section 2.2 mentions "Single-Obligor Limits Table" and references "Figure 2.1 - Maximum exposure by counterparty classification (AED billion, % of Tier 1 capital)." However, the actual table content is not in the text, only the reference to the figure.
   * Section 3 mentions "Sector Concentration Limits" and references "Figure 3.1 - Current sector utilisation vs. soft and hard

**Judge:** The context chunks do not provide sufficient information to determine the credit concentration limits for corporate counterparties, as the Single-Obligor Limits Table is referenced but not provided.

**Missing facts:** Single-Obligor Limits for Corporate Investment Grade, Single-Obligor Limits for Corporate Sub-Investment Grade, Single-Obligor Limits for Corporate Speculative

**Retrieved:**

| # | type     | source                         | text                                                                                                                    |
| - | -------- | ------------------------------ | ----------------------------------------------------------------------------------------------------------------------- |
| 1 | `text` | FAB_Credit_Concentration_Limit | ## FIRST ABU DHABI BANK  Group Risk Management - Credit Risk Division  ## CREDIT CONCENTRATION&amp; EXPOSURE LIMITS POL |
| 2 | `text` | FAB_Credit_Concentration_Limit | ## 2.2 Single-Obligor Limits Table  Figure 2.1 - Maximum exposure by counterparty classification (AED bi                |

Images: `unknown` p4

---


# LLM-as-judge evaluation

_2026-08-10 08:46 UTC_

|                    |                              |
| ------------------ | ---------------------------- |
| generator (text)   | `openai/gpt-oss-120b`      |
| generator (vision) | `qwen/qwen3.6-27b`         |
| **judge**    | `llama-3.3-70b-versatile`  |
| multimodal         | `True` / mode `side_car` |

## Diagnosis

| Outcome | Count | Means                                              |
| ------- | ----- | -------------------------------------------------- |
| pass    | 1     | retrieved the right context AND answered correctly |

## Metrics

| Metric                          | Score      |
| ------------------------------- | ---------- |
| Context sufficiency (retrieval) | 1/1 (100%) |
| Answer correctness (end-to-end) | 1/1 (100%) |
| Groundedness (no hallucination) | 1/1 (100%) |

## Cases

| id    | name                               | sufficient | correct | grounded | diagnosis |
| ----- | ---------------------------------- | ---------- | ------- | -------- | --------- |
| `6` | approval-diagrams-image-escalation | True       | 2       | True     | pass      |


---

# LLM-as-judge evaluation

_2026-08-10 08:49 UTC_

| | |
|---|---|
| generator (text) | `openai/gpt-oss-120b` |
| generator (vision) | `qwen/qwen3.6-27b` |
| **judge** | `llama-3.3-70b-versatile` |
| multimodal | `True` / mode `side_car` |

## Diagnosis

| Outcome | Count | Means |
|---|---|---|
| pass | 1 | retrieved the right context AND answered correctly |

## Metrics

| Metric | Score |
|---|---|
| Context sufficiency (retrieval) | 1/1 (100%) |
| Answer correctness (end-to-end) | 1/1 (100%) |
| Groundedness (no hallucination) | 1/1 (100%) |

## Cases

| id | name | sufficient | correct | grounded | diagnosis |
|---|---|---|---|---|---|
| `10` | conc-limits-image-sector | True | 2 | True | pass |

---

# LLM-as-judge evaluation

_2026-08-10 08:53 UTC_

| | |
|---|---|
| generator (text) | `openai/gpt-oss-120b` |
| generator (vision) | `qwen/qwen3.6-27b` |
| **judge** | `llama-3.3-70b-versatile` |
| multimodal | `True` / mode `side_car` |

## Diagnosis

| Outcome | Count | Means |
|---|---|---|
| RETRIEVAL | 1 | **chunks did not contain the answer** — fix chunking/indexing |

## Metrics

| Metric | Score |
|---|---|
| Context sufficiency (retrieval) | 0/1 (0%) |
| Answer correctness (end-to-end) | 0/1 (0%) |
| Groundedness (no hallucination) | 1/1 (100%) |

## Cases

| id | name | sufficient | correct | grounded | diagnosis |
|---|---|---|---|---|---|
| `12` | mrm-image-dashboard | False | 0 | True | **RETRIEVAL** |

## Failures in detail

### `12` mrm-image-dashboard — RETRIEVAL

**Question:** In the §4.1 Performance Monitoring dashboard, what was the Human Override Rate in April, and does it breach the 20% threshold?

**Gold answer:** 21% in April — this breaches the 20% reference threshold and is the highest point in the six-month series.

**Generated:** 
<think>
The user is asking for the Human Override Rate in April from the §4.1 Performance Monitoring dashboard and whether it breaches the 20% threshold.

1.  **Analyze the Request:**
    *   Target: Human Override Rate in April.
    *   Condition: Check against 20% threshold.
    *   Source: §4.1 Performance Monitoring dashboard (Figure 4.1).
    *   Constraint: Use only provided context and figures. Cite sources.

2.  **Scan Context:**
    *   Context [1] contains a table labeled "Figure 4.1 - Ongoing monitoring dashboard, H1 2024".
    *   The table lists metrics, monitoring frequency, and alert thresholds.
    *   Row: "Human override rate".
    *   Monitoring Frequency: "Monthly".
    *   Alert Threshold: "> 20% override rate triggers model review".
    *   *Crucially*, the table in 

**Judge:** The context does not contain the specific value for the Human Override Rate in April, only the threshold.

**Missing facts:** Human Override Rate in April

**Retrieved:**

| # | type | source | text |
|---|---|---|---|
| 1 | `table` | FAB_Model_Risk_Management_Fram | [Table] Figure 4.1 - Ongoing monitoring dashboard, H1 2024 (red markers indicate threshold breaches).  \| Metric          |
| 2 | `text` | FAB_Model_Risk_Management_Fram | ## 4. Ongoing Monitoring Requirements  ## 4.1 Performance Monitoring  All  production  AI/ML  pricing  models  must  be  |

Images: `table_image` p1


---

# LLM-as-judge evaluation

_2026-08-10 09:23 UTC_

| | |
|---|---|
| generator (text) | `openai/gpt-oss-120b` |
| generator (vision) | `qwen/qwen3.6-27b` |
| **judge** | `llama-3.3-70b-versatile` |
| multimodal | `True` / mode `side_car` |

## Diagnosis

| Outcome | Count | Means |
|---|---|---|
| RETRIEVAL | 1 | **chunks did not contain the answer** — fix chunking/indexing |

## Metrics

| Metric | Score |
|---|---|
| Context sufficiency (retrieval) | 0/1 (0%) |
| Answer correctness (end-to-end) | 0/1 (0%) |
| Groundedness (no hallucination) | 1/1 (100%) |

## Cases

| id | name | sufficient | correct | grounded | diagnosis |
|---|---|---|---|---|---|
| `12` | mrm-image-dashboard | False | 1 | True | **RETRIEVAL** |

## Failures in detail

### `12` mrm-image-dashboard — RETRIEVAL

**Question:** In the §4.1 Performance Monitoring dashboard, what was the Human Override Rate in April, and does it breach the 20% threshold?

**Gold answer:** 21% in April — this breaches the 20% reference threshold and is the highest point in the six-month series.

**Generated:** 
<think>
The user is asking for the Human Override Rate in April from the §4.1 Performance Monitoring dashboard and whether it breaches the 20% threshold.

1.  **Analyze the Request:**
    *   Target: Human Override Rate in April.
    *   Condition: Check against 20% threshold.
    *   Source: §4.1 Performance Monitoring dashboard (Figure 4.1).
    *   Constraint: Use only provided context and figures. Cite sources.

2.  **Scan Context:**
    *   Context [1] contains a table labeled "Figure 4.1 - Ongoing monitoring dashboard, H1 2024".
    *   The table lists metrics, monitoring frequency, and alert thresholds.
    *   Row: "Human override rate".
    *   Threshold: "> 20% override rate triggers model review".
    *   However, the table in Context [1] *only* lists the thresholds and frequen

**Judge:** The context does not contain the specific value for the Human Override Rate in April, only the threshold and a reference to a missing figure.

**Missing facts:** Human Override Rate in April

**Retrieved:**

| # | type | source | text |
|---|---|---|---|
| 1 | `table` | FAB_Model_Risk_Management_Fram | [Table] Figure 4.1 - Ongoing monitoring dashboard, H1 2024 (red markers indicate threshold breaches).  \| Metric          |
| 2 | `text` | FAB_Model_Risk_Management_Fram | ## 4. Ongoing Monitoring Requirements  ## 4.1 Performance Monitoring  All  production  AI/ML  pricing  models  must  be  |

Images: `table_image` p1


---

# LLM-as-judge evaluation

_2026-08-10 09:36 UTC_

| | |
|---|---|
| generator (text) | `openai/gpt-oss-120b` |
| generator (vision) | `qwen/qwen3.6-27b` |
| **judge** | `llama-3.3-70b-versatile` |
| multimodal | `True` / mode `side_car` |

## Diagnosis

| Outcome | Count | Means |
|---|---|---|
| pass | 1 | retrieved the right context AND answered correctly |

## Metrics

| Metric | Score |
|---|---|
| Context sufficiency (retrieval) | 1/1 (100%) |
| Answer correctness (end-to-end) | 1/1 (100%) |
| Groundedness (no hallucination) | 1/1 (100%) |

## Cases

| id | name | sufficient | correct | grounded | diagnosis |
|---|---|---|---|---|---|
| `1` | cbuae-text-dates | True | 2 | True | pass |

---

# LLM-as-judge evaluation

_2026-08-10 09:41 UTC_

| | |
|---|---|
| generator (text) | `openai/gpt-oss-120b` |
| generator (vision) | `qwen/qwen3.6-27b` |
| **judge** | `llama-3.3-70b-versatile` |
| multimodal | `True` / mode `side_car` |

## Diagnosis

| Outcome | Count | Means |
|---|---|---|
| PARTIAL | 1 | answer missing material facts |

## Metrics

| Metric | Score |
|---|---|
| Context sufficiency (retrieval) | 1/1 (100%) |
| Answer correctness (end-to-end) | 0/1 (0%) |
| Groundedness (no hallucination) | 1/1 (100%) |

## Cases

| id | name | sufficient | correct | grounded | diagnosis |
|---|---|---|---|---|---|
| `4` | term-loan-text-eligibility | True | 1 | True | **PARTIAL** |

## Failures in detail

### `4` term-loan-text-eligibility — PARTIAL

**Question:** What minimum internal credit rating and turnover does a client need to be eligible for FAB's Corporate Term Loan?

**Gold answer:** Internal credit rating of B or above (CCC and below are not eligible without a GCC waiver); annual turnover exceeding AED 50 million; at least two years of audited financial statements; and clean KYC/AML status.

**Generated:** A client must have:

* **FAB internal credit rating:** B or higher (ratings of CCC or below are not eligible without a GCC waiver).  
* **Annual turnover:** more than AED 50 million (or the equivalent in foreign currency).  

**Sources:**  
[1] – Eligibility criteria list the rating requirement and the AED 50 million turnover threshold.  
[2] – Repeats the same eligibility requirements for rating and turnover.

**Judge:** The generated answer is partially correct but misses material facts such as the requirement for audited financial statements and clean KYC/AML status.

**Missing facts:** at least two years of audited financial statements, clean KYC/AML status

**Retrieved:**

| # | type | source | text |
|---|---|---|---|
| 1 | `text` | FAB_Corporate_Term_Loan_Produc | ## FIRST ABU DHABI BANK  Corporate Banking - Product Management  ## CORPORATE TERM LOAN - PRODUCT MANUAL  Product Struct |
| 2 | `text` | FAB_Corporate_Term_Loan_Produc | ## FIRST ABU DHABI BANK  Corporate Banking - Product Management  ## CORPORATE TERM LOAN - PRODUCT MANUAL  Product Struct |

Images: `table_image` p1


---

# LLM-as-judge evaluation

_2026-08-10 09:50 UTC_

| | |
|---|---|
| generator (text) | `openai/gpt-oss-120b` |
| generator (vision) | `qwen/qwen3.6-27b` |
| **judge** | `llama-3.3-70b-versatile` |
| multimodal | `True` / mode `side_car` |

## Diagnosis

| Outcome | Count | Means |
|---|---|---|
| RETRIEVAL | 1 | **chunks did not contain the answer** — fix chunking/indexing |

## Metrics

| Metric | Score |
|---|---|
| Context sufficiency (retrieval) | 0/1 (0%) |
| Answer correctness (end-to-end) | 0/1 (0%) |
| Groundedness (no hallucination) | 1/1 (100%) |

## Cases

| id | name | sufficient | correct | grounded | diagnosis |
|---|---|---|---|---|---|
| `13` | text-rcf-floor | False | 0 | True | **RETRIEVAL** |

## Failures in detail

### `13` text-rcf-floor — RETRIEVAL

**Question:** What's the minimum drawn margin floor and commitment fee floor for a revolving credit facility with a BB-rated client?

**Gold answer:** 160 bps drawn margin floor and 40 bps commitment fee floor (plus a 20 bps facility fee floor).

**Generated:** 
<think>
The user is asking for the minimum drawn margin floor and commitment fee floor for a revolving credit facility (RCF) with a BB-rated client.

1.  **Scan Context [1] (FAB_Credit_Pricing_Policy_v2_4):**
    *   Section 3.2 is titled "Revolving Credit Facilities (RCFs)".
    *   It states: "Revolving credit facilities are subject to the pricing floors in Table 3.2."
    *   It mentions: "Commitment fees on undrawn portions are in addition to the drawn margin and are not included in the floor calculations below."
    *   However, the text provided in Context [1] *does not contain Table 3.2*. It jumps from Section 3.2 to Section 3.3.
    *   Section 3.1 contains Table 3.1 for Corporate Term Loans, but the question is about RCFs.
    *   Figure 3.1 is for Corporate Term Loans.

2.  **Sc

**Judge:** The context does not contain Table 3.2, which is referenced as the source for Revolving Credit Facility pricing floors.

**Missing facts:** minimum drawn margin floor, commitment fee floor

**Retrieved:**

| # | type | source | text |
|---|---|---|---|
| 1 | `text` | FAB_Credit_Pricing_Policy_v2_4 | ## 3.1 Corporate Term Loans  Table 3.1 sets out minimum pricing floors for standard corporate term loans, segmented by c |
| 2 | `table` | FAB_Corporate_Term_Loan_Produc | [Table] The all-in pricing for a Corporate Term Loan comprises the following components, all of which must be included i |

Images: `table_image` p7


---

# LLM-as-judge evaluation

_2026-08-10 09:56 UTC_

| | |
|---|---|
| generator (text) | `openai/gpt-oss-120b` |
| generator (vision) | `qwen/qwen3.6-27b` |
| **judge** | `llama-3.3-70b-versatile` |
| multimodal | `True` / mode `side_car` |

## Diagnosis

| Outcome | Count | Means |
|---|---|---|
| pass | 1 | retrieved the right context AND answered correctly |

## Metrics

| Metric | Score |
|---|---|
| Context sufficiency (retrieval) | 1/1 (100%) |
| Answer correctness (end-to-end) | 1/1 (100%) |
| Groundedness (no hallucination) | 1/1 (100%) |

## Cases

| id | name | sufficient | correct | grounded | diagnosis |
|---|---|---|---|---|---|
| `14` | text-mrm-tier | True | 2 | True | pass |

---

# LLM-as-judge evaluation

_2026-08-10 10:04 UTC_

| | |
|---|---|
| generator (text) | `openai/gpt-oss-120b` |
| generator (vision) | `qwen/qwen3.6-27b` |
| **judge** | `llama-3.3-70b-versatile` |
| multimodal | `True` / mode `side_car` |

## Diagnosis

| Outcome | Count | Means |
|---|---|---|
| pass | 1 | retrieved the right context AND answered correctly |

## Metrics

| Metric | Score |
|---|---|
| Context sufficiency (retrieval) | 1/1 (100%) |
| Answer correctness (end-to-end) | 1/1 (100%) |
| Groundedness (no hallucination) | 1/1 (100%) |

## Cases

| id | name | sufficient | correct | grounded | diagnosis |
|---|---|---|---|---|---|
| `18` | text-worked-example | True | 2 | True | pass |

---

# LLM-as-judge evaluation

_2026-08-10 10:13 UTC_

| | |
|---|---|
| generator (text) | `openai/gpt-oss-120b` |
| generator (vision) | `qwen/qwen3.6-27b` |
| **judge** | `llama-3.3-70b-versatile` |
| multimodal | `True` / mode `side_car` |

## Diagnosis

| Outcome | Count | Means |
|---|---|---|
| pass | 1 | retrieved the right context AND answered correctly |
| Correct refusals | 1/1 |

## Cases

| id | name | sufficient | correct | grounded | diagnosis |
|---|---|---|---|---|---|
| `35` | unanswerable-retail-mortgage-ltv | — | — | — | pass |