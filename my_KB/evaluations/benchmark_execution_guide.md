# FAB AgentMesh — FinBEN / FLARE Benchmark Execution Guide

**Refer to this document for**: step-by-step commands to run the benchmark, what each task evaluates, the exact sample query sent to the mesh, the expected gold answer, the metric computed, and what that metric means in plain English.

**Companion document**: `workflow_evaluations.md` (architectural overview and folder structure)

---

## Quick Reference — What Document to Use

| I want to... | Use |
|---|---|
| Understand the folder/file structure | `workflow_evaluations.md` |
| Run the demo step by step | **This document** |
| See sample queries + expected answers | **This document** |
| Understand what each metric measures | **This document** |
| Understand FAB-specific evaluators (PII, RBAC, compliance) | `workflow_evaluations.md` → Layer 2 section |

---

## Part 1 — Step-by-Step Execution

### Step 1: Install dependencies

Run from `agent-mesh/` directory:

```bash
cd agent-mesh
pip install "datasets>=5.0.0" "huggingface-hub>=1.20.1" rouge-score
```

### Step 2: (Optional) HuggingFace login — only for Tier 2 datasets
token: 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
19 datasets are public (Tier 1) — no login needed.
17 datasets are gated (Tier 2) — require login:

```bash
huggingface-cli login
# Paste your HuggingFace access token when prompted
# Get token from: https://huggingface.co/settings/tokens
```

### Step 3: Start the FAB AgentMesh (required for live runs)

```bash
# Terminal 1 — start all agents
python launch_mesh.py
# Wait until you see all 4 agents ready (compliance, data, rag, price_assist)
```

### Step 4: Choose your run mode

```bash
# Terminal 2 — pick ONE of the following:

# A) Dry-run — no agents needed, just verify all datasets load
python workflow_evaluations/run_evaluation.py --mode demo --dry-run

# B) Demo (Tier 1 only) — 19 public datasets, ~95 API calls, ~5 min
python workflow_evaluations/run_evaluation.py --mode demo --tier 1

# C) Demo (All 36) — needs HF login from Step 2, ~180 API calls, ~10 min
python workflow_evaluations/run_evaluation.py --mode demo --tier 2

# D) Single task — run just one dataset against the mesh
python workflow_evaluations/run_evaluation.py --mode single --agent api --task flare_fpb

# E) Full benchmark (large sample sizes, production run)
python workflow_evaluations/run_evaluation.py --mode benchmarks

# F) CI smoke test — no agents, no HF, just evaluator unit checks
python workflow_evaluations/run_evaluation.py --mode ci
```

### Step 5: Find your reports

All output files land in `agent-mesh/workflow_evaluations/reports/` with UTC timestamps:

| File | What it contains |
|---|---|
| `demo_report_{ts}.json` | Full per-task + per-sample breakdown |
| `demo_report_{ts}.md` | Summary table (paste into PR or slide) |
| `benchmark_report_{ts}.json` | Full FLARE + FinBEN results |
| `benchmark_summary_{ts}.md` | Markdown table for PR comments |
| `benchmark_scores_{ts}.csv` | Score vs threshold pass/fail |

---

## Part 2 — Metric Glossary (Plain English)

Before the task reference, here is what each metric means:

| Metric | Full Name | What it measures | Range | Better = |
|---|---|---|---|---|
| **Accuracy** | Exact classification accuracy | % of samples where the model picked the correct label | 0–1 | Higher |
| **Weighted F1** | Weighted F1-score | Harmonic mean of Precision and Recall, weighted by class frequency. Better than accuracy when classes are imbalanced (e.g. 90% negative, 10% positive) | 0–1 | Higher |
| **MCC** | Matthews Correlation Coefficient | A balanced metric for binary classification that accounts for all four cells of the confusion matrix. +1 = perfect, 0 = random, -1 = inverse | -1 to +1 | Higher |
| **AUROC** | Area Under the ROC Curve | Probability that the model ranks a positive sample higher than a negative one. 0.5 = random, 1.0 = perfect | 0–1 | Higher |
| **Exact Match (EM)** | Exact Match | 1 if the normalised response exactly equals the gold answer, 0 otherwise. Very strict — even "30%" vs "30.0%" fails | 0–1 | Higher |
| **Token F1** | Token-level F1 | F1 computed on shared word tokens between response and gold. More lenient than EM — partial credit for partially correct answers | 0–1 | Higher |
| **F1 (entity)** | Entity token overlap F1 | Overlap of capitalised entity tokens between the model's response and the gold entity list. Proxy for formal entity-level F1 without span-exact matching | 0–1 | Higher |
| **ROUGE-1** | ROUGE unigram overlap | % of gold summary words that appear in the generated summary | 0–1 | Higher |
| **ROUGE-2** | ROUGE bigram overlap | % of consecutive gold word pairs that appear in the generated summary | 0–1 | Higher |
| **ROUGE-L** | ROUGE longest common subsequence | Longest in-order word sequence shared between generated and gold summary. Captures sentence structure better than ROUGE-1 | 0–1 | Higher |
| **MSE** | Mean Squared Error | Average squared difference between predicted sentiment score and gold score. Penalises large errors heavily | 0 to infinity | Lower |
| **Pearson R** | Pearson Correlation | Linear correlation between predicted and gold sentiment scores. +1 = perfect agreement, 0 = no relationship, -1 = inverse | -1 to +1 | Higher |

### Demo Pass Thresholds

These are the minimum scores for a task to show "PASS" in demo mode:

| Task type | Metric checked | Threshold | Why this threshold |
|---|---|---|---|
| Multiple choice (mc) | Weighted F1 | >= 0.50 | Zero-shot models should at least beat random on financial text |
| Free-form QA (freeform) | Token F1 | >= 0.30 | Numerical QA is hard zero-shot; partial overlap is meaningful |
| Sequence labelling (sequence) | F1 approx | >= 0.20 | Proxy metric — even extracting half the entities is useful |
| Summarisation (summarize) | ROUGE-1 | >= 0.20 | Minimum lexical overlap with reference summary |
| Regression (regression) | Pearson R | >= 0.10 | Any weak positive correlation with gold scores is non-trivial |

---

## Part 3 — All 37 Tasks: Sample Query, Expected Answer, Metric

### CATEGORY 1: Information Extraction (7 tasks)

---

#### 1. `flare_ner` — Named Entity Recognition
- **Dataset**: `TheFinAI/flare-ner` | **Tier**: 1 (public) | **Agent**: RAGAgent
- **Task type**: sequence | **Runner**: `run_sequence_task`

**What it tests**: Can the mesh extract company names, people, and locations from financial text?

**Sample query sent to mesh**:
```
Identify named entities (PER/ORG/LOC) from: 'Silicium de Provence SAS agreed to supply Evergreen Solar Inc under a long-term contract.'
```

**Expected gold answer**:
```
Silicium de Provence SAS, ORG
Evergreen Solar Inc, ORG
```

**Metric**: `f1_approx` — token overlap F1 on capitalised words between the model's extracted entities and the gold list.

**What a good score looks like**: F1 = 0.70+ means the model correctly identifies most company names and person names. F1 < 0.20 means it is not extracting named entities at all.

---

#### 2. `flare_finer_ord` — Numeric Expression Recognition *(Tier 2)*
- **Dataset**: `TheFinAI/flare-finer-ord` | **Tier**: 2 (HF login) | **Agent**: RAGAgent
- **Task type**: sequence

**What it tests**: Can the mesh identify and label financial numbers (monetary amounts, percentages, dates)?

**Sample query sent to mesh**:
```
Identify and label numeric expressions in: 'Revenue grew 12.5% to $4.3 billion in Q3 2023.'
```

**Expected gold answer**:
```
12.5%, PERCENT
$4.3 billion, MONEY
Q3 2023, DATE
```

**Metric**: `f1_approx` — token overlap F1 on labeled numeric spans.

---

#### 3. `flare_finred` — Financial Relation Extraction
- **Dataset**: `TheFinAI/flare-finred` | **Tier**: 1 (public) | **Agent**: RAGAgent
- **Task type**: sequence

**What it tests**: Can the mesh identify the relationship between two entities in a financial sentence (e.g. "acquired", "subsidiary of", "CEO of")?

**Sample query sent to mesh**:
```
Extract the relation between entities in: 'Apple acquired Intel's smartphone modem business for $1 billion.'
```

**Expected gold answer**:
```
Apple, acquired, Intel
```

**Metric**: `f1_approx` — token overlap F1 on extracted relation triples.

---

#### 4. `flare_causal_sc` — Causal Sentence Classification *(Tier 2)*
- **Dataset**: `TheFinAI/flare-causal20-sc` | **Tier**: 2 | **Agent**: ComplianceAgent
- **Task type**: mc (yes/no)

**What it tests**: Can the mesh determine whether a financial sentence contains a cause-and-effect relationship?

**Sample query sent to mesh**:
```
Does this sentence describe a financial causal relationship? 'Higher interest rates led to a slowdown in mortgage applications.' Answer: yes or no
```

**Expected gold answer**: `yes`

**Metric**: `f1_weighted` + `accuracy`.

**What a good score looks like**: Accuracy > 0.70 means the model reliably detects causal language in financial text.

---

#### 5. `flare_causal_cd` — Causal Span Detection *(Tier 2)*
- **Dataset**: `TheFinAI/flare-cd` | **Tier**: 2 | **Agent**: ComplianceAgent
- **Task type**: sequence

**What it tests**: Can the mesh extract the specific cause and effect spans from a financial sentence?

**Sample query sent to mesh**:
```
Identify cause and effect in: 'The Fed raised rates by 75bps, causing mortgage demand to drop 20%.'
```

**Expected gold answer**:
```
cause: raised rates 75bps | effect: mortgage demand dropped 20%
```

**Metric**: `f1_approx` — token overlap F1 on extracted cause/effect spans.

---

#### 6. `flare_fnxl` — XBRL Numeric Labelling
- **Dataset**: `TheFinAI/flare-fnxl` | **Tier**: 1 (public) | **Agent**: DataAgent
- **Task type**: sequence

**What it tests**: Can the mesh classify numeric expressions from XBRL financial filings into types (MONETARY, PERCENT, DATE, CARDINAL)?

**Sample query sent to mesh**:
```
Label numeric expressions in: 'Net income was $2.1 billion, up 15%, in fiscal year 2022.'
```

**Expected gold answer**:
```
$2.1 billion, MONETARY
15%, PERCENT
2022, DATE
```

**Metric**: `f1_approx` — token overlap F1 on labelled numeric spans.

---

#### 7. `flare_fsrl` — Financial Span Role Labelling
- **Dataset**: `TheFinAI/flare-fsrl` | **Tier**: 1 (public) | **Agent**: DataAgent
- **Task type**: sequence

**What it tests**: Can the mesh label the subject, relation, and object in a financial statement (similar to semantic role labelling)?

**Sample query sent to mesh**:
```
Label the financial span roles (subject, relation, object) in: 'Apple's revenue increased to $90 billion.'
```

**Expected gold answer**:
```
subject: Apple's revenue | relation: increased to | object: $90 billion
```

**Metric**: `f1_approx` — token overlap F1 on labelled spans.

---

### CATEGORY 2: Textual Analysis (10 tasks)

---

#### 8. `flare_fpb` — Financial PhraseBank Sentiment
- **Dataset**: `TheFinAI/en-fpb` | **Tier**: 2 (HF login) | **Agent**: RAGAgent
- **Task type**: mc (positive/negative/neutral)

**What it tests**: Can the mesh correctly classify the sentiment of financial news sentences?

**Sample query sent to mesh**:
```
Classify sentiment of this financial news as positive, negative, or neutral: 'Profit rose 12% in Q3, beating analyst estimates.'
```

**Expected gold answer**: `positive`

**Metric**: `f1_weighted` (primary) + `accuracy`. Weighted F1 accounts for the fact that "neutral" is the most common class.

**What a good score looks like**: Weighted F1 >= 0.70 is the production threshold. Zero-shot LLMs typically score 0.65–0.80.

---

#### 9. `finben_fiqa` — FiQA Aspect-Based Sentiment
- **Dataset**: `TheFinAI/fiqa-sentiment-classification` | **Tier**: 1 (public) | **Agent**: RAGAgent
- **Task type**: mc (positive/negative/neutral)

**What it tests**: Harder than FPB — this classifies sentiment in financial microblogs and news headlines, which are more informal and ambiguous.

**Sample query sent to mesh**:
```
Classify the financial sentiment as positive, negative, or neutral: 'Weak iPhone sales weigh on Apple earnings outlook.'
```

**Expected gold answer**: `negative`

**Metric**: `f1_weighted` — weighted F1. Scores are typically 5–10 points lower than FPB due to higher text ambiguity.

---

#### 10. `flare_tsa` — Target-Specific Sentiment Scoring
- **Dataset**: `TheFinAI/flare-tsa` | **Tier**: 1 (public) | **Agent**: RAGAgent
- **Task type**: regression (unique — not classification)

**What it tests**: Instead of a discrete label, the model must return a continuous score from -1.0 (very negative) to +1.0 (very positive) for a *specific named company* mentioned in the text.

**Sample query sent to mesh**:
```
Return a sentiment score from -1 to 1 for Ashtead: 'Ashtead to buy back shares, full-year profit beats estimates.'
```

**Expected gold answer**: `0.588`

**How scoring works**: The runner extracts the first float from the model's response using regex, clamps it to [-1, 1], then computes:
- **MSE** (Mean Squared Error) — how far off the score is on average. Lower = better. A well-calibrated model scores MSE < 0.10.
- **Pearson R** — whether the model's scores correlate with gold scores across samples. R > 0.5 means the model understands the sentiment direction.

---

#### 11. `flare_headlines` — Price-Sensitive Headline Classification
- **Dataset**: `TheFinAI/flare-headlines` | **Tier**: 1 (public) | **Agent**: ComplianceAgent
- **Task type**: mc (yes/no)

**What it tests**: Can the mesh identify whether a financial headline is likely to move a stock price? Maps to ComplianceAgent's role in flagging market-sensitive information.

**Sample query sent to mesh**:
```
Is this financial headline price-sensitive? 'Fed signals three rate cuts in 2024.' Answer yes or no.
```

**Expected gold answer**: `yes`

**Metric**: `f1_weighted` + `accuracy`. This is a binary task — MCC is also computed.

---

#### 12. `flare_fomc` — FOMC Policy Stance *(Tier 2)*
- **Dataset**: `TheFinAI/flare-fomc` | **Tier**: 2 | **Agent**: ComplianceAgent
- **Task type**: mc (hawkish/dovish/neutral)

**What it tests**: Can the mesh interpret Federal Reserve communications and classify the monetary policy stance? Highly relevant to FAB's interest rate and pricing decisions.

**Sample query sent to mesh**:
```
Classify this Federal Reserve statement as hawkish, dovish, or neutral: 'The Committee decided to maintain the target range for the federal funds rate at 5.25-5.5%.'
```

**Expected gold answer**: `neutral`

**Metric**: `f1_weighted` + `accuracy`.

**Hawkish vs Dovish**: hawkish = signals rate hike / tighter policy; dovish = signals rate cut / looser policy; neutral = holding steady.

---

#### 13. `flare_finarg_auc` — Argument Unit Classification *(Tier 2)*
- **Dataset**: `TheFinAI/flare-finarg-ecc-auc` | **Tier**: 2 | **Agent**: ComplianceAgent
- **Task type**: mc (claim/premise/other)

**What it tests**: In earnings call transcripts, can the mesh classify whether a statement is a **claim** (a forward-looking assertion) or a **premise** (supporting evidence)?

**Sample query sent to mesh**:
```
Classify this earnings call segment as claim, premise, or other: 'We expect revenue growth of 15% driven by cloud adoption.'
```

**Expected gold answer**: `claim`

**Metric**: `f1_weighted` + `accuracy`.

---

#### 14. `flare_finarg_arc` — Argument Relation Classification *(Tier 2)*
- **Dataset**: `TheFinAI/flare-finarg-ecc-arc` | **Tier**: 2 | **Agent**: ComplianceAgent
- **Task type**: mc (support/attack)

**What it tests**: Given two statements from an earnings call, does the second support or challenge the first? Tests reasoning over financial arguments.

**Sample query sent to mesh**:
```
Does this statement support or attack the claim 'Revenue will grow 15%'? Statement: 'Our new product line launched last quarter and already shows strong traction.'
```

**Expected gold answer**: `support`

**Metric**: `f1_weighted` + `accuracy`.

---

#### 15. `flare_multifin` — MultiFin Headline Topic *(Tier 2)*
- **Dataset**: `TheFinAI/flare-multifin-en` | **Tier**: 2 | **Agent**: RAGAgent
- **Task type**: mc (12 topic classes)

**What it tests**: Multi-class topic classification across 12 financial domains. Tests the model's broad financial vocabulary.

**Sample query sent to mesh**:
```
What financial topic does this headline cover? 'ECB holds rates steady as inflation falls toward target.' Options: monetary policy, earnings, M&A, IPO, regulation, credit, dividend, macro, analyst, legal, ESG, other
```

**Expected gold answer**: `monetary policy`

**Metric**: `f1_weighted`. 12-class F1 is harder than binary — scores of 0.50+ are considered strong zero-shot.

---

#### 16. `flare_ma` — M&A Deal Status
- **Dataset**: `TheFinAI/flare-ma` | **Tier**: 1 (public) | **Agent**: ComplianceAgent
- **Task type**: mc (rumour/complete)

**What it tests**: Can the mesh determine whether an M&A news item describes a confirmed completed deal or an unconfirmed rumour? Relevant to FAB's transaction compliance checks.

**Sample query sent to mesh**:
```
Will this M&A deal be completed or is it still a rumour? 'Berkshire Hathaway is reportedly in preliminary talks to acquire Southwest Airlines at $75/share, though Southwest declined to comment.' Choices: rumour, complete
```

**Expected gold answer**: `rumour`

**How the dataset works**: `choices` = `["rumour", "complete"]`, `gold` = 0 (index of "rumour"). The runner appends "Answer with one of: rumour, complete" if not already in the query.

**Metric**: `f1_weighted` + `accuracy` + `MCC` (binary task).

---

#### 17. `flare_mlesg` — ESG Issue Classification
- **Dataset**: `TheFinAI/flare-mlesg` | **Tier**: 1 (public) | **Agent**: ComplianceAgent
- **Task type**: mc (33 ESG classes)

**What it tests**: Given a news article, can the mesh classify which of the 33 MSCI ESG categories it relates to? The hardest classification task in the suite — 33 classes.

**Sample query sent to mesh**:
```
Classify this article by ESG issue (MSCI framework): 'AT&T and LA Unified School District partnered to provide no-cost broadband to students via FCC Emergency Connectivity Fund.'
```

**Expected gold answer**: `Access to Communications`

**The 33 categories** include: Access to Communications, Biodiversity & Land Use, Carbon Emissions, Pay, Board Diversity, Anti-Corruption, etc.

**Metric**: `f1_weighted`. With 33 classes, random baseline is ~3%. A score of 0.40+ is strong.

---

### CATEGORY 3: Question Answering (4 tasks)

---

#### 18. `finben_finqa` — FinQA Numerical Reasoning
- **Dataset**: `TheFinAI/flare-finqa` | **Tier**: 1 (public) | **Agent**: PriceAssistAgent
- **Task type**: freeform | **Runner**: `run_freeform_task`

**What it tests**: Multi-step numerical reasoning over financial reports. Requires reading a table, extracting numbers, and computing the answer (e.g. growth rates, ratios). Directly maps to PriceAssistAgent's pricing calculations.

**Sample query sent to mesh** (the `query` field already contains full context):
```
Based on this financial context, answer precisely. Context: Revenue in 2020 was $50M; in 2021 it was $65M. Question: What was the revenue growth rate from 2020 to 2021?
```

**Expected gold answer**: `30%`

**How scoring works**:
- **Exact Match (EM)**: Is the normalised response exactly `30%`? Even `30.0%` or `0.30` would fail EM.
- **Token F1**: Do the words in the response overlap with the gold? "30%" vs "30 percent growth" would score partial credit.

**Note**: EM will be low (often 0.1–0.3) because the model may express correct answers differently. Token F1 is the more meaningful metric here.

---

#### 19. `flare_tatqa` — TatQA Table + Text QA
- **Dataset**: `TheFinAI/flare-tatqa` | **Tier**: 1 (public) | **Agent**: DataAgent
- **Task type**: freeform

**What it tests**: QA that requires reading BOTH a financial table AND a prose paragraph together to answer correctly. Models that only read text fail — they need to cross-reference table values. Maps to DataAgent's structured data queries.

**Sample query sent to mesh** (the full `query` field includes the table):
```
Please answer the given financial question based on the context. Context: [table: 2019 sales $1,496.5M | 2018 sales $1,412.2M | ...]. Question: What is the amount of total sales in 2019?
```

**Expected gold answer**: `$1,496.5`

**Metric**: `exact_match` + `token_f1`. EM rewards exact extraction; Token F1 gives partial credit.

---

#### 20. `flare_convfinqa` — ConvFinQA Multi-turn
- **Dataset**: `TheFinAI/ConvFinQA` | **Tier**: 1 (public) | **Agent**: RAGAgent
- **Task type**: freeform (multi-turn)

**What it tests**: Multi-turn conversational QA — each question in a conversation depends on the previous answer. Tests whether the mesh carries context across turns.

**Sample query sent to mesh**:
```
Financial context: [income statement table]. Q1: What was net income in 2019? (answer: $21.2M). Q2: How does that compare to 2018?
```

**Expected gold answer per turn**:
- Turn 1: `$21.2M`
- Turn 2: `Net income grew from $18.4M in 2018 to $21.2M in 2019, an increase of 15.2%.`

**Metric**: `exact_match` and `token_f1` computed per turn, averaged across all turns in all conversations.

---

#### 21. `flare_regulations` — Financial Regulatory QA *(Tier 2)*
- **Dataset**: `TheFinAI/flare-regulations` | **Tier**: 2 | **Agent**: RAGAgent
- **Task type**: freeform

**What it tests**: Long-form QA about financial regulations (Basel III, MiFID II, DORA, etc.). Tests deep regulatory knowledge — directly relevant to FAB's compliance checks.

**Sample query sent to mesh**:
```
What is the minimum Common Equity Tier 1 (CET1) capital ratio required under Basel III for systemically important banks?
```

**Expected gold answer**: `7% (4.5% minimum + 2.5% capital conservation buffer)`

**Metric**: `exact_match` + `token_f1`. EM will be low (regulatory answers are verbose); Token F1 is the meaningful metric.

---

### CATEGORY 4: Text Generation (2 tasks)

---

#### 22. `finben_ectsum` — Earnings Call Summarisation
- **Dataset**: `TheFinAI/flare-ectsum` | **Tier**: 1 (public) | **Agent**: PriceAssistAgent
- **Task type**: summarize | **Runner**: `run_summarization_task`

**What it tests**: Can the mesh extract the key facts from an earnings call transcript into concise bullet points? Directly relevant to PriceAssistAgent's synthesis of financial documents.

**Sample query sent to mesh**:
```
Summarise this earnings call in 3-5 bullet points: 'Good morning. Q3 revenue reached $4.2 billion, up 18% year-over-year driven by cloud services. Operating margin expanded 200bps to 28%. We are raising full-year guidance to $16-16.5 billion...'
```

**Expected gold answer** (reference summary):
```
• Q3 revenue $4.2B, +18% YoY
• Cloud services primary growth driver
• Operating margin 28% (+200bps)
• FY guidance raised to $16-16.5B
```

**Metric**: Three ROUGE scores computed against the reference summary:
- **ROUGE-1** (unigram): Does the summary contain the right words? ("revenue", "billion", "cloud") — threshold >= 0.35
- **ROUGE-2** (bigram): Does it contain the right word pairs? ("cloud services", "operating margin") — threshold >= 0.10
- **ROUGE-L** (LCS): Does it preserve sentence structure from the original?

---

#### 23. `flare_edtsum` — Financial News Summarisation *(Tier 2)*
- **Dataset**: `TheFinAI/flare-edtsum` | **Tier**: 2 | **Agent**: RAGAgent
- **Task type**: summarize

**What it tests**: Abstractive summarisation of financial news articles — the model must produce a fluent 2-3 sentence summary, not just extract sentences.

**Sample query sent to mesh**:
```
Summarise this financial news in 2-3 sentences: 'The Federal Reserve raised its benchmark interest rate by 25 basis points on Wednesday, marking the tenth consecutive increase since March 2022, bringing rates to their highest level in 22 years...'
```

**Expected gold answer**:
```
The Fed raised rates 25bps to a 22-year high. This marks the tenth straight hike since March 2022.
```

**Metric**: ROUGE-1, ROUGE-2, ROUGE-L (same as ECTSum above).

---

### CATEGORY 5: Risk Management (9 tasks)

---

#### 24. `flare_german` — German Credit Scoring
- **Dataset**: `TheFinAI/flare-german` | **Tier**: 1 (public) | **Agent**: ComplianceAgent
- **Task type**: mc (good/bad)

**What it tests**: Binary creditworthiness assessment from 20 encoded attributes (account status, loan duration, credit history, purpose, employment, etc.). Maps to ComplianceAgent's risk-scoring role.

**Sample query sent to mesh** (the full `query` field describes all 20 attributes):
```
Assess creditworthiness from attributes: checking_account=no_account, duration=6mo, credit_history=all_paid, purpose=furniture, amount=1169, savings=unknown, employment=7+yrs. Answer good or bad.
```

**Expected gold answer**: `good`

**Metric**: `f1_weighted` + `accuracy` + **MCC** (Matthews Correlation Coefficient). MCC is the primary metric because this is a binary imbalanced classification — the German dataset has 700 good, 300 bad.

**MCC interpretation**: MCC = +1 is perfect. MCC = 0 is random. A zero-shot LLM typically scores MCC = 0.1–0.3 on this task.

---

#### 25. `flare_australian` — Australian Credit Scoring
- **Dataset**: `TheFinAI/flare-australian` | **Tier**: 1 (public) | **Agent**: ComplianceAgent
- **Task type**: mc (yes/no approve)

**What it tests**: Credit application approval from 14 attributes (encoded as A1–A14). Similar to German but different feature set and slightly more balanced classes (307 approved, 383 rejected).

**Sample query sent to mesh**:
```
Given applicant attributes: A1=b, A2=30.83, A3=0, A4=u, A5=g, A6=w, A7=v, A8=1.25, A9=t, A10=t, A11=01, A12=f, A13=g, A14=202. Approve credit? Answer yes or no.
```

**Expected gold answer**: `yes`

**Metric**: `f1_weighted` + `accuracy` + `MCC`.

---

#### 26. `flare_lendingclub` — LendingClub Loan Default *(Tier 2)*
- **Dataset**: `TheFinAI/flare-cra-lendingclub` | **Tier**: 2 | **Agent**: ComplianceAgent
- **Task type**: mc (yes/no default)

**What it tests**: Will this loan default? Real P2P lending data with features like loan amount, interest rate, employment, income, debt-to-income ratio.

**Sample query sent to mesh**:
```
Given this loan application: loan_amount=$10,000, term=36mo, interest_rate=11.44%, grade=B, employment=10+yrs, home=MORTGAGE, income=$65,000, dti=15.6. Will this loan default? Answer yes or no.
```

**Expected gold answer**: `no`

**Metric**: `f1_weighted` + `accuracy` + **AUROC** (Area Under ROC Curve). AUROC is preferred for imbalanced fraud/default datasets (most loans don't default).

---

#### 27. `flare_ccf` — Credit Card Fraud Detection *(Tier 2)*
- **Dataset**: `TheFinAI/flare-cra-ccf` | **Tier**: 2 | **Agent**: ComplianceAgent
- **Task type**: mc (yes/no fraudulent)

**What it tests**: Binary fraud detection from PCA-transformed transaction features (V1–V28) plus Amount and Time. This is highly imbalanced — only 0.17% of transactions are fraud.

**Sample query sent to mesh**:
```
Given transaction features: V1=-1.36, V2=0.07, V3=2.54, V4=1.38, V5=-0.34, Amount=$149.62, Time=0s. Is this transaction fraudulent? Answer yes or no.
```

**Expected gold answer**: `no`

**Metric**: `f1_weighted` + `AUROC`. AUROC is critical here — a model that always says "no" gets 99.83% accuracy but AUROC = 0.5 (random).

---

#### 28. `flare_ccfraud` — CCFraud Transaction Classification *(Tier 2)*
- **Dataset**: `TheFinAI/flare-cra-ccfraud` | **Tier**: 2 | **Agent**: ComplianceAgent
- **Task type**: mc (fraudulent/legitimate)

**What it tests**: Like CCF but uses descriptive transaction features (category, amount, merchant location, city population, transaction hour) rather than PCA components.

**Sample query sent to mesh**:
```
Classify this credit card transaction: category=grocery_pos, amount=$149.46, merchant_lat=36.01, merchant_long=-81.07, city_pop=523, transaction_hour=14. Is it fraudulent or legitimate?
```

**Expected gold answer**: `legitimate`

**Metric**: `f1_weighted` + `AUROC`.

---

#### 29. `flare_polish` — Polish Financial Distress *(Tier 2)*
- **Dataset**: `TheFinAI/flare-cra-polish` | **Tier**: 2 | **Agent**: ComplianceAgent
- **Task type**: mc (yes/no distress)

**What it tests**: Will this Polish company face bankruptcy? Uses 5-year financial ratio data (ROA, debt ratio, working capital, current ratio, EBITDA).

**Sample query sent to mesh**:
```
Given company financial ratios: net_profit/total_assets=0.12, total_liabilities/total_assets=0.48, working_capital/total_assets=0.31, current_ratio=2.1, EBITDA/total_assets=0.18. Will this company face financial distress? Answer yes or no.
```

**Expected gold answer**: `no`

**Metric**: `f1_weighted` + `accuracy`.

---

#### 30. `flare_taiwan` — Taiwan Corporate Default *(Tier 2)*
- **Dataset**: `TheFinAI/flare-cra-taiwan` | **Tier**: 2 | **Agent**: ComplianceAgent
- **Task type**: mc (yes/no default)

**What it tests**: Corporate default prediction from the Taiwan Economic Journal dataset using 95 financial indicators, described in the query as key ratios.

**Sample query sent to mesh**:
```
Given a Taiwanese company's indicators: X1=0.23 (ROA), X2=0.51 (debt_ratio), X3=0.78 (current_ratio), X4=0.12 (cash_flow_ratio), X5=0.04 (EPS). Will this company default? Answer yes or no.
```

**Expected gold answer**: `no`

**Metric**: `f1_weighted` + `accuracy`.

---

#### 31. `flare_portoseguro` — Porto Seguro Insurance Claim *(Tier 2)*
- **Dataset**: `TheFinAI/flare-cra-portoseguro` | **Tier**: 2 | **Agent**: ComplianceAgent
- **Task type**: mc (yes/no claim)

**What it tests**: Will this driver file an auto insurance claim in the next year? Real-world Brazilian insurer data.

**Sample query sent to mesh**:
```
Given driver attributes: ps_ind_01=2, ps_ind_02_cat=2, ps_ind_03=5, ps_ind_04_cat=1, vehicle_age=3, annual_premium=1800. Will this driver file an insurance claim? Answer yes or no.
```

**Expected gold answer**: `no`

**Metric**: `f1_weighted` + `accuracy`.

---

#### 32. `flare_travelinsurance` — Travel Insurance Claim *(Tier 2)*
- **Dataset**: `TheFinAI/flare-cra-travelinsurance` | **Tier**: 2 | **Agent**: ComplianceAgent
- **Task type**: mc (yes/no claim)

**What it tests**: Will this traveller file a travel insurance claim? Uses readable features (age, employment, income, chronic disease, frequent flyer status).

**Sample query sent to mesh**:
```
Given traveller profile: age=35, employment=private_sector, annual_income=$50000, chronic_disease=no, frequent_flyer=yes, abroad_travel=yes, travel_insurance_plan=basic. Will they file a claim? Answer yes or no.
```

**Expected gold answer**: `yes`

**Metric**: `f1_weighted` + `accuracy`.

---

### CATEGORY 6: Forecasting (3 tasks)

---

#### 33. `flare_bigdata22` — BigData22 Stock Movement
- **Dataset**: `TheFinAI/flare-sm-bigdata` | **Tier**: 1 (public) | **Agent**: DataAgent
- **Task type**: mc (Rise/Fall)

**What it tests**: Stock price movement prediction from financial news headlines. The query field already contains the full news text.

**Sample query sent to mesh**:
```
Based on this financial news, will the stock price rise or fall? 'Tesla reports record deliveries of 484,507 vehicles in Q4, beating analyst expectations of 473,000.' Answer Rise or Fall.
```

**Expected gold answer**: `Rise`

**Metric**: `accuracy` + **MCC**. MCC is preferred because stock movement datasets are often balanced but the task is hard (near-random difficulty at 0.50–0.55 even for fine-tuned models).

---

#### 34. `flare_acl18` — ACL18 Stock Movement
- **Dataset**: `TheFinAI/flare-sm-acl` | **Tier**: 1 (public) | **Agent**: DataAgent
- **Task type**: mc (Rise/Fall)

**What it tests**: Stock movement prediction combining 10-day price history + social media tweets. The `query` field contains both historical prices AND relevant tweets up to the prediction date.

**Sample query sent to mesh** (the actual query is much longer):
```
By reviewing price data and tweets, predict if $CSCO will Rise or Fall at 2015-10-01. [10-day OHLCV data] [tweets: analyst downgrades, short sales activity]. Answer Rise or Fall.
```

**Expected gold answer**: `Fall` (choices[gold_index] from the dataset)

**Metric**: `accuracy` + `MCC`. This task is harder than BigData22 because it requires integrating structured price data with unstructured text.

---

#### 35. `flare_cikm18` — CIKM18 Stock Movement
- **Dataset**: `TheFinAI/flare-sm-cikm` | **Tier**: 1 (public) | **Agent**: DataAgent
- **Task type**: mc (Rise/Fall)

**What it tests**: Same setup as ACL18 but from the CIKM 2018 dataset — different set of stocks and time period.

**Sample query sent to mesh**:
```
Predict if this stock will Rise or Fall based on: closing price dropped 1.8% over last 5 days; recent news: strong quarterly guidance raised; tweet volume spike positive. Answer Rise or Fall.
```

**Expected gold answer**: `Rise`

**Metric**: `accuracy` + `MCC`.

---

### CATEGORY 7: Decision Making (2 tasks)

---

#### 36. `flare_dm_simple` — FinTrade Simple *(Tier 2)*
- **Dataset**: `TheFinAI/flare-dm-simplong` | **Tier**: 2 | **Agent**: DataAgent
- **Task type**: mc (buy/hold/sell)

**What it tests**: Single-stock trading decision from technical and fundamental signals. The `query` field contains a long-horizon market summary with indicators.

**Sample query sent to mesh**:
```
Based on this market data, should we buy, hold, or sell this stock? RSI=42 (oversold approaching), MACD=bullish crossover, P/E=18 (below sector avg 22), recent earnings beat by 12%. Answer buy, hold, or sell.
```

**Expected gold answer**: `buy`

**Metric**: `f1_weighted` + `accuracy`. In ideal conditions, Sharpe ratio and annualized return would be computed from a portfolio simulation — but for zero-shot demo, F1 is used.

---

#### 37. `flare_dm_complex` — FinTrade Complex *(Tier 2)*
- **Dataset**: `TheFinAI/flare-dm-complong` | **Tier**: 2 | **Agent**: DataAgent
- **Task type**: mc (buy/hold/sell)

**What it tests**: More complex version of FinTrade — multi-factor portfolio context over a longer horizon, including macro conditions (VIX, sector rotation, Fed stance).

**Sample query sent to mesh**:
```
Given 30-day market context: sector rotation into defensives, Fed hawkish pivot, VIX=28 (elevated), stock up 45% YTD (extended). Portfolio allocation recommendation: buy, hold, or sell?
```

**Expected gold answer**: `sell`

**Metric**: `f1_weighted` + `accuracy`.

---

## Part 4 — Summary Table

| # | Task key | Dataset | Tier | Category | Type | Metric | Agent | Pass threshold |
|---|---|---|---|---|---|---|---|---|
| 1 | `flare_ner` | flare-ner | 1 | IE | sequence | F1 approx | RAGAgent | F1 >= 0.20 |
| 2 | `flare_finer_ord` | flare-finer-ord | 2 | IE | sequence | F1 approx | RAGAgent | F1 >= 0.20 |
| 3 | `flare_finred` | flare-finred | 1 | IE | sequence | F1 approx | RAGAgent | F1 >= 0.20 |
| 4 | `flare_causal_sc` | flare-causal20-sc | 2 | IE | mc | F1 + Acc | ComplianceAgent | F1 >= 0.50 |
| 5 | `flare_causal_cd` | flare-cd | 2 | IE | sequence | F1 approx | ComplianceAgent | F1 >= 0.20 |
| 6 | `flare_fnxl` | flare-fnxl | 1 | IE | sequence | F1 approx | DataAgent | F1 >= 0.20 |
| 7 | `flare_fsrl` | flare-fsrl | 1 | IE | sequence | F1 approx | DataAgent | F1 >= 0.20 |
| 8 | `flare_fpb` | en-fpb | 2 | Textual Analysis | mc | Weighted F1 | RAGAgent | F1 >= 0.50 |
| 9 | `finben_fiqa` | fiqa-sentiment | 1 | Textual Analysis | mc | Weighted F1 | RAGAgent | F1 >= 0.50 |
| 10 | `flare_tsa` | flare-tsa | 1 | Textual Analysis | regression | MSE + Pearson R | RAGAgent | R >= 0.10 |
| 11 | `flare_headlines` | flare-headlines | 1 | Textual Analysis | mc | Weighted F1 | ComplianceAgent | F1 >= 0.50 |
| 12 | `flare_fomc` | flare-fomc | 2 | Textual Analysis | mc | Weighted F1 | ComplianceAgent | F1 >= 0.50 |
| 13 | `flare_finarg_auc` | flare-finarg-ecc-auc | 2 | Textual Analysis | mc | Weighted F1 | ComplianceAgent | F1 >= 0.50 |
| 14 | `flare_finarg_arc` | flare-finarg-ecc-arc | 2 | Textual Analysis | mc | Weighted F1 | ComplianceAgent | F1 >= 0.50 |
| 15 | `flare_multifin` | flare-multifin-en | 2 | Textual Analysis | mc | Weighted F1 | RAGAgent | F1 >= 0.50 |
| 16 | `flare_ma` | flare-ma | 1 | Textual Analysis | mc | Weighted F1 + MCC | ComplianceAgent | F1 >= 0.50 |
| 17 | `flare_mlesg` | flare-mlesg | 1 | Textual Analysis | mc | Weighted F1 | ComplianceAgent | F1 >= 0.50 |
| 18 | `finben_finqa` | flare-finqa | 1 | QA | freeform | EM + Token F1 | PriceAssistAgent | Token F1 >= 0.30 |
| 19 | `flare_tatqa` | flare-tatqa | 1 | QA | freeform | EM + Token F1 | DataAgent | Token F1 >= 0.30 |
| 20 | `flare_convfinqa` | ConvFinQA | 1 | QA | freeform | EM + Token F1 | RAGAgent | Token F1 >= 0.30 |
| 21 | `flare_regulations` | flare-regulations | 2 | QA | freeform | EM + Token F1 | RAGAgent | Token F1 >= 0.30 |
| 22 | `finben_ectsum` | flare-ectsum | 1 | Text Generation | summarize | ROUGE-1/2/L | PriceAssistAgent | ROUGE-1 >= 0.20 |
| 23 | `flare_edtsum` | flare-edtsum | 2 | Text Generation | summarize | ROUGE-1/2/L | RAGAgent | ROUGE-1 >= 0.20 |
| 24 | `flare_german` | flare-german | 1 | Risk Mgmt | mc | F1 + MCC | ComplianceAgent | F1 >= 0.50 |
| 25 | `flare_australian` | flare-australian | 1 | Risk Mgmt | mc | F1 + MCC | ComplianceAgent | F1 >= 0.50 |
| 26 | `flare_lendingclub` | flare-cra-lendingclub | 2 | Risk Mgmt | mc | F1 + AUROC | ComplianceAgent | F1 >= 0.50 |
| 27 | `flare_ccf` | flare-cra-ccf | 2 | Risk Mgmt | mc | F1 + AUROC | ComplianceAgent | F1 >= 0.50 |
| 28 | `flare_ccfraud` | flare-cra-ccfraud | 2 | Risk Mgmt | mc | F1 + AUROC | ComplianceAgent | F1 >= 0.50 |
| 29 | `flare_polish` | flare-cra-polish | 2 | Risk Mgmt | mc | F1 + Acc | ComplianceAgent | F1 >= 0.50 |
| 30 | `flare_taiwan` | flare-cra-taiwan | 2 | Risk Mgmt | mc | F1 + Acc | ComplianceAgent | F1 >= 0.50 |
| 31 | `flare_portoseguro` | flare-cra-portoseguro | 2 | Risk Mgmt | mc | F1 + Acc | ComplianceAgent | F1 >= 0.50 |
| 32 | `flare_travelinsurance` | flare-cra-travelinsurance | 2 | Risk Mgmt | mc | F1 + Acc | ComplianceAgent | F1 >= 0.50 |
| 33 | `flare_bigdata22` | flare-sm-bigdata | 1 | Forecasting | mc | Acc + MCC | DataAgent | F1 >= 0.50 |
| 34 | `flare_acl18` | flare-sm-acl | 1 | Forecasting | mc | Acc + MCC | DataAgent | F1 >= 0.50 |
| 35 | `flare_cikm18` | flare-sm-cikm | 1 | Forecasting | mc | Acc + MCC | DataAgent | F1 >= 0.50 |
| 36 | `flare_dm_simple` | flare-dm-simplong | 2 | Decision Making | mc | F1 + Acc | DataAgent | F1 >= 0.50 |
| 37 | `flare_dm_complex` | flare-dm-complong | 2 | Decision Making | mc | F1 + Acc | DataAgent | F1 >= 0.50 |

**Tier 1 = 19 tasks (public, no HuggingFace login)** | **Tier 2 = 18 tasks (needs `huggingface-cli login`)**
