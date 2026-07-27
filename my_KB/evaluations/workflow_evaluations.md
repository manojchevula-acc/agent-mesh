# FAB AgentMesh — Workflow Evaluations

## Overview

The `workflow_evaluations/` suite is a three-layer evaluation framework for FAB AgentMesh. Layer 3 uses the **real public HuggingFace datasets from FinBEN and FLARE** — all 36 FinBEN datasets spanning 24 financial tasks across 7 categories. It runs them through the live FAB AgentMesh API and scores each response against the gold labels using task-appropriate metrics.

The suite lives entirely under `agent-mesh/workflow_evaluations/` and is run from the `agent-mesh/` directory.

---

## Architecture — Three Layers

| Layer | Folder | Purpose | Needs live agents? |
|---|---|---|---|
| 2 | `evaluators/` | Per-stage scorers: compliance, PII, RBAC, RAG citation, MCP tool selection | No |
| 1 | `workflow/` | End-to-end golden test cases against the full request pipeline | Yes (live) or No (replay) |
| 3 | `financial_benchmarks/` | All 36 FinBEN + FLARE datasets via HuggingFace | Yes (or `--dry-run`) |

---

## Folder Structure

```
agent-mesh/workflow_evaluations/
├── config.py                          — endpoints, BENCHMARK_SAMPLE_SIZES, DEMO_SAMPLE_SIZES, thresholds
├── run_evaluation.py                  — single CLI entry point (all modes incl. --mode demo)
├── requirements_eval.txt              — rouge-score>=0.1.2
├── reports/                           — all output files (git-ignored)
│
├── evaluators/                        — Layer 2: FAB-specific stage scorers
│   ├── compliance_evaluator.py
│   ├── pii_evaluator.py
│   ├── rbac_evaluator.py
│   ├── rag_citation_evaluator.py
│   └── data_tool_evaluator.py
│
├── workflow/                          — Layer 1: end-to-end workflow evaluation
│   ├── dataset_builder.py             — 20 FAB-specific golden test cases
│   ├── run_maf_eval.py
│   └── results_reporter.py
│
└── financial_benchmarks/              — Layer 3: FinBEN + FLARE (36 datasets)
    ├── task_registry.py               — ALL 36 tasks + 5 generic async runners  ← NEW
    ├── demo_runner.py                 — verbose per-sample demo orchestrator     ← NEW
    ├── flare_runner.py                — FLARE wrapper (uses task_registry)
    ├── finben_runner.py               — FinBEN wrapper (uses task_registry)
    ├── benchmark_report.py            — report builder
    └── datasets/                      — HuggingFace cache (git-ignored)
```

---

## Layer 3 — All 36 FinBEN + FLARE Datasets

### Unified Schema

Every TheFinAI HuggingFace dataset uses the same field names:

| Field | Type | Usage |
|---|---|---|
| `query` | str | Full prompt with context embedded — send directly to the mesh |
| `text` | str | Raw context only (for display/logging) |
| `choices` | list[str] | Answer options (multiple-choice tasks) |
| `gold` | int | Index of correct choice in `choices` |
| `answer` | str | Free-form gold answer (QA / summarisation / sequence tasks) |
| `label` | list[str] | BIO token tags (sequence labelling tasks) |
| `token` | list[str] | Tokenised input (sequence labelling tasks) |

### Task Types and Metrics

| Type | Runner function | What it does | Metrics |
|---|---|---|---|
| `mc` | `run_multiple_choice_task` | Sends `query` → parses which `choice` appears in response | Accuracy + Weighted F1 + MCC (binary only) |
| `freeform` | `run_freeform_task` | Sends `query` → compares response to `answer` string | Exact Match + Token F1 |
| `sequence` | `run_sequence_task` | Sends `query` → extracts capitalised entities, compares to `answer` | Token-overlap F1 (proxy entity F1) |
| `summarize` | `run_summarization_task` | Sends `query` → scores summary against `answer` reference | ROUGE-1, ROUGE-2, ROUGE-L |
| `regression` | `run_regression_task` | Sends `query` → parses float from response, compares to `answer` float | MSE + Pearson R |

---

### Category 1 — Information Extraction (7 datasets)

| # | Task key | Dataset | Type | Metric | Agent | Tier |
|---|---|---|---|---|---|---|
| 1 | `flare_ner` | `TheFinAI/flare-ner` | sequence | F1 | RAGAgent | 1 |
| 2 | `flare_finer_ord` | `TheFinAI/flare-finer-ord` | sequence | F1 | RAGAgent | 2 |
| 3 | `flare_finred` | `TheFinAI/flare-finred` | sequence | F1 | RAGAgent | 1 |
| 4 | `flare_causal_sc` | `TheFinAI/flare-causal20-sc` | mc | F1 + Acc | ComplianceAgent | 2 |
| 5 | `flare_causal_cd` | `TheFinAI/flare-cd` | sequence | F1 | ComplianceAgent | 2 |
| 6 | `flare_fnxl` | `TheFinAI/flare-fnxl` | sequence | F1 | DataAgent | 1 |
| 7 | `flare_fsrl` | `TheFinAI/flare-fsrl` | sequence | F1 | DataAgent | 1 |

**Sample queries and expected answers:**

`flare_ner`
> Q: "Identify named entities (PER/ORG/LOC) from: 'Silicium de Provence SAS agreed to supply Evergreen Solar Inc under a long-term contract.'"
> Gold: `Silicium de Provence SAS, ORG` / `Evergreen Solar Inc, ORG`

`flare_finred`
> Q: "Extract the relation between entities in: 'Apple acquired Intel's smartphone modem business for $1 billion.'"
> Gold: `Apple, acquired, Intel`

`flare_fnxl`
> Q: "Label numeric expressions in: 'Net income was $2.1 billion, up 15%, in fiscal year 2022.'"
> Gold: `$2.1 billion, MONETARY` / `15%, PERCENT` / `2022, DATE`

`flare_causal_sc` *(Tier 2)*
> Q: "Does this sentence describe a financial causal relationship? 'Higher interest rates led to a slowdown in mortgage applications.'"
> Gold: `yes`

---

### Category 2 — Textual Analysis (10 datasets)

| # | Task key | Dataset | Type | Metric | Agent | Tier |
|---|---|---|---|---|---|---|
| 8 | `flare_fpb` | `TheFinAI/en-fpb` | mc | Weighted F1 + Acc | RAGAgent | 1 |
| 9 | `finben_fiqa` | `TheFinAI/fiqa-sentiment-classification` | mc | Weighted F1 | RAGAgent | 1 |
| 10 | `flare_tsa` | `TheFinAI/flare-tsa` | regression | MSE + Pearson R | RAGAgent | 1 |
| 11 | `flare_headlines` | `TheFinAI/flare-headlines` | mc | F1 + Acc | ComplianceAgent | 1 |
| 12 | `flare_fomc` | `TheFinAI/flare-fomc` | mc | Weighted F1 + Acc | ComplianceAgent | 2 |
| 13 | `flare_finarg_auc` | `TheFinAI/flare-finarg-ecc-auc` | mc | F1 | ComplianceAgent | 2 |
| 14 | `flare_finarg_arc` | `TheFinAI/flare-finarg-ecc-arc` | mc | F1 | ComplianceAgent | 2 |
| 15 | `flare_multifin` | `TheFinAI/flare-multifin-en` | mc | Weighted F1 | RAGAgent | 2 |
| 16 | `flare_ma` | `TheFinAI/flare-ma` | mc | F1 + Acc | ComplianceAgent | 1 |
| 17 | `flare_mlesg` | `TheFinAI/flare-mlesg` | mc (33 ESG classes) | Weighted F1 | ComplianceAgent | 1 |

**Sample queries and expected answers:**

`flare_fpb`
> Q: "Classify sentiment of this financial news as positive, negative, or neutral: 'Profit rose 12% in Q3, beating analyst estimates.'"
> Gold: `positive`

`flare_tsa` *(unique: regression, not classification)*
> Q: "Return a sentiment score from -1 to 1 for Ashtead: 'Ashtead to buy back shares, full-year profit beats estimates.'"
> Gold: `0.588` (float)
> Metric: MSE (lower = better) + Pearson R (higher = better)

`flare_ma`
> Q: "'Berkshire Hathaway reportedly in preliminary talks to acquire Southwest Airlines at $75/share...' Choices: rumour, complete"
> Gold: `rumour` (choices[0])

`flare_mlesg`
> Q: "Classify this article by ESG issue (MSCI framework): 'AT&T and LA Unified School District provided no-cost broadband to students...'"
> Gold: `Access to Communications` (choices[0] of 33 ESG categories)

`flare_fomc` *(Tier 2)*
> Q: "Classify this Federal Reserve statement as hawkish, dovish, or neutral: 'The FOMC decided to maintain the target range at 5.25–5.5%.'"
> Gold: `neutral`

---

### Category 3 — Question Answering (4 datasets)

| # | Task key | Dataset | Type | Metric | Agent | Tier |
|---|---|---|---|---|---|---|
| 18 | `finben_finqa` | `TheFinAI/flare-finqa` | freeform | EM + Token F1 | PriceAssistAgent | 1 |
| 19 | `flare_tatqa` | `TheFinAI/flare-tatqa` | freeform | EM + Token F1 | DataAgent | 1 |
| 20 | `flare_convfinqa` | `TheFinAI/ConvFinQA` | freeform | EM + Token F1 | RAGAgent | 1 |
| 21 | `flare_regulations` | `TheFinAI/flare-regulations` | freeform | EM + Token F1 | RAGAgent | 2 |

**Sample queries and expected answers:**

`finben_finqa`
> Q: "Based on this financial context, answer precisely. Context: Revenue in 2020 was $50M; in 2021 it was $65M. Question: What was the revenue growth rate from 2020 to 2021?"
> Gold: `30%`
> Note: EM will be low — this is a numerical reasoning difficulty baseline.

`flare_tatqa`
> Q: "Please answer the given financial question based on the context. Context: [table with 2019 sales $1,496.5M | 2018 sales $1,412.2M]. Question: What is the amount of total sales in 2019?"
> Gold: `$1,496.5`
> Note: context includes both table data and prose paragraphs.

`flare_convfinqa`
> Q: Multi-turn: "Financial context: [income statement]. Q1: What was net income in 2019? Q2: How does that compare to 2018?"
> Gold per turn: `$21.2M` / `Net income grew 15.2% from $18.4M`
> Metric: EM computed per turn, averaged across conversation.

---

### Category 4 — Text Generation (2 datasets)

| # | Task key | Dataset | Type | Metric | Agent | Tier |
|---|---|---|---|---|---|---|
| 22 | `finben_ectsum` | `TheFinAI/flare-ectsum` | summarize | ROUGE-1/2/L | PriceAssistAgent | 1 |
| 23 | `flare_edtsum` | `TheFinAI/flare-edtsum` | summarize | ROUGE-1/2/L | RAGAgent | 2 |

**Sample queries and expected answers:**

`finben_ectsum`
> Q: "Summarise this earnings call in 3–5 bullet points: 'Q3 revenue reached $4.2 billion, up 18% YoY driven by cloud services. Operating margin expanded 200bps to 28%...'"
> Gold: `• Q3 revenue $4.2B, +18% YoY\n• Cloud primary growth driver\n• Operating margin 28%`
> Threshold: ROUGE-1 ≥ 0.35

`flare_edtsum` *(Tier 2)*
> Q: "Summarise this financial news in 2–3 sentences: 'The Federal Reserve raised its benchmark rate by 25bps, the tenth consecutive increase...'"
> Gold: `The Fed raised rates 25bps to a 22-year high. Tenth straight hike since March 2022.`

---

### Category 5 — Risk Management (9 datasets)

| # | Task key | Dataset | Type | Metric | Agent | Tier |
|---|---|---|---|---|---|---|
| 24 | `flare_german` | `TheFinAI/flare-german` | mc (good/bad) | F1 + MCC | ComplianceAgent | 1 |
| 25 | `flare_australian` | `TheFinAI/flare-australian` | mc (yes/no) | F1 + MCC | ComplianceAgent | 1 |
| 26 | `flare_lendingclub` | `TheFinAI/flare-cra-lendingclub` | mc (yes/no) | F1 + AUROC | ComplianceAgent | 2 |
| 27 | `flare_ccf` | `TheFinAI/flare-cra-ccf` | mc (yes/no) | F1 + AUROC | ComplianceAgent | 2 |
| 28 | `flare_ccfraud` | `TheFinAI/flare-cra-ccfraud` | mc (fraud/legit) | F1 + AUROC | ComplianceAgent | 2 |
| 29 | `flare_polish` | `TheFinAI/flare-cra-polish` | mc (yes/no) | F1 + Acc | ComplianceAgent | 2 |
| 30 | `flare_taiwan` | `TheFinAI/flare-cra-taiwan` | mc (yes/no) | F1 + Acc | ComplianceAgent | 2 |
| 31 | `flare_portoseguro` | `TheFinAI/flare-cra-portoseguro` | mc (yes/no) | F1 + Acc | ComplianceAgent | 2 |
| 32 | `flare_travelinsurance` | `TheFinAI/flare-cra-travelinsurance` | mc (yes/no) | F1 + Acc | ComplianceAgent | 2 |

**Sample queries and expected answers:**

`flare_german` *(public — verified accessible)*
> Q: "Assess creditworthiness: checking_account=no_account, duration=6mo, credit_history=all_paid, purpose=furniture, amount=1169, savings=unknown, employment=7+yrs. Answer good or bad."
> Gold: `good`
> Note: 20 encoded attributes; the `query` field already contains the full description of all attribute codes.

`flare_australian` *(public — verified accessible)*
> Q: "Given applicant attributes: A1=b, A2=30.83, A3=0, A4=u, A5=g, A6=w, A7=v, A8=1.25, A9=t, A10=t, A11=01, A12=f, A13=g, A14=202. Approve credit? Answer yes or no."
> Gold: `yes`

`flare_ccf` *(Tier 2)*
> Q: "Given transaction features: V1=-1.36, V2=0.07, V3=2.54, Amount=$149.62, Time=0s. Is this transaction fraudulent? Answer yes or no."
> Gold: `no`

---

### Category 6 — Forecasting (3 datasets)

| # | Task key | Dataset | Type | Metric | Agent | Tier |
|---|---|---|---|---|---|---|
| 33 | `flare_bigdata22` | `TheFinAI/flare-sm-bigdata` | mc (Rise/Fall) | Acc + MCC | DataAgent | 1 |
| 34 | `flare_acl18` | `TheFinAI/flare-sm-acl` | mc (Rise/Fall) | Acc + MCC | DataAgent | 1 |
| 35 | `flare_cikm18` | `TheFinAI/flare-sm-cikm` | mc (Rise/Fall) | Acc + MCC | DataAgent | 1 |

**Sample queries and expected answers:**

`flare_bigdata22`
> Q: "Based on this financial news, will the stock price rise or fall? 'Tesla reports record deliveries of 484,507 vehicles in Q4, beating analyst expectations of 473,000.' Answer Rise or Fall."
> Gold: `Rise`

`flare_acl18` *(uses price history + tweets)*
> Q: "By reviewing price data and tweets, predict if $CSCO will Rise or Fall at 2015-10-01. Historical data: 10-day prices declining. Tweets: analyst downgrades. Answer Rise or Fall."
> Gold: `Fall` (choices[gold_index])
> Note: `query` field in dataset already contains full historical price table + social media content.

---

### Category 7 — Decision Making (2 datasets)

| # | Task key | Dataset | Type | Metric | Agent | Tier |
|---|---|---|---|---|---|---|
| 36 | `flare_dm_simple` | `TheFinAI/flare-dm-simplong` | mc (buy/hold/sell) | F1 + Acc | DataAgent | 2 |
| 37 | `flare_dm_complex` | `TheFinAI/flare-dm-complong` | mc (buy/hold/sell) | F1 + Acc | DataAgent | 2 |

**Sample queries and expected answers:**

`flare_dm_simple` *(Tier 2)*
> Q: "Based on market data: RSI=42 (oversold), MACD=bullish crossover, P/E=18 (below sector avg 22), recent earnings beat 12%. Should we buy, hold, or sell?"
> Gold: `buy`

`flare_dm_complex` *(Tier 2)*
> Q: "30-day context: sector rotation into defensives, Fed hawkish pivot, VIX=28, stock up 45% YTD. Portfolio recommendation: buy, hold, or sell?"
> Gold: `sell`

---

## Dataset Accessibility

| Tier | Count | Condition | Datasets |
|---|---|---|---|
| **1 — Public** | 19 | No HuggingFace login needed | flare_fpb, finben_fiqa, flare_tsa, flare_headlines, flare_ma, flare_mlesg, flare_ner, flare_finred, flare_fnxl, flare_fsrl, flare_tatqa, finben_ectsum, flare_german, flare_australian, flare_bigdata22, flare_acl18, flare_cikm18, flare_convfinqa, finben_finqa |
| **2 — Gated** | 17 | `huggingface-cli login` required | flare_fomc, flare_multifin, flare_finarg_auc, flare_finarg_arc, flare_edtsum, flare_causal_sc, flare_causal_cd, flare_finer_ord, flare_lendingclub, flare_ccf, flare_ccfraud, flare_polish, flare_taiwan, flare_portoseguro, flare_travelinsurance, flare_dm_simple, flare_dm_complex |

---

## Implementation Architecture

### task_registry.py — Core Module

`financial_benchmarks/task_registry.py` defines all 36 tasks in `TASK_REGISTRY` and provides 5 generic async runners:

```python
TASK_REGISTRY = {
    "flare_fpb": {
        "dataset_id":   "TheFinAI/en-fpb",
        "type":         "mc",           # runner type
        "metric":       "f1_acc",       # primary metric key
        "agent":        "RAGAgent",     # FAB agent this task maps to
        "tier":         1,              # 1=public, 2=gated (needs HF login)
        "category":     "Textual Analysis",
        "description":  "Financial PhraseBank sentiment — positive/negative/neutral",
        "sample_query": "Classify sentiment: 'Profit rose 12%...'",
        "sample_gold":  "positive",
    },
    # ... all 36 entries
}

RUNNER_DISPATCH = {
    "mc":         run_multiple_choice_task,
    "freeform":   run_freeform_task,
    "sequence":   run_sequence_task,
    "summarize":  run_summarization_task,
    "regression": run_regression_task,
}
```

Each runner:
1. Calls `_load_dataset_safe(dataset_id, n=n_samples)` → list of dicts
2. Sends `item["query"]` to `POST /api/query` on the mesh
3. Parses the response according to task type
4. Returns `BenchmarkTaskResult(task_name, dataset_id, task_type, n_samples, metrics, per_sample, error)`

---

## Execution Modes

### Install dependencies first
```bash
cd agent-mesh
pip install "datasets>=5.0.0" "huggingface-hub>=1.20.1" rouge-score

# Only needed for Tier 2 gated datasets:
huggingface-cli login
```

### `--mode demo` — Rich demo with per-sample output *(NEW)*
```bash
# Dry-run: show all dataset names + sample counts, zero API calls
python workflow_evaluations/run_evaluation.py --mode demo --dry-run

# Tier 1: 19 public datasets (~95 API calls, ~5 min with agents running)
python workflow_evaluations/run_evaluation.py --mode demo --tier 1

# All 36 datasets (~180 API calls, ~10 min; requires HF login)
python workflow_evaluations/run_evaluation.py --mode demo --tier 2
```

Demo output per task:
```
════════════════════════════════════════════════════════════════════════
  [3/19]  flare_ma  (ComplianceAgent)  Tier 1
  Dataset : TheFinAI/flare-ma
  Category: Textual Analysis
  Desc    : M&A deal status classification — rumour / complete
────────────────────────────────────────────────────────────────────────
  #1   Q: "Berkshire Hathaway reportedly in talks..."  gold=rumour    pred=rumour    ✓
  #2   Q: "Eldorado Resorts in preliminary talks..."   gold=rumour    pred=rumour    ✓
  #3   Q: "Caesars/Eldorado deal signed, closes Q3"   gold=complete  pred=complete  ✓
  → accuracy=1.000  f1_weighted=1.000  [PASS]
```

Final summary table + `reports/demo_report_{ts}.md` and `reports/demo_report_{ts}.json`.

### `--mode benchmarks` — Full benchmark run (existing, now covers all public tasks)
```bash
python workflow_evaluations/run_evaluation.py --mode benchmarks [--dry-run]
```

### `--mode single` — Single task (any of the 36 task keys)
```bash
python workflow_evaluations/run_evaluation.py --mode single --agent api --task flare_ma
python workflow_evaluations/run_evaluation.py --mode single --agent api --task flare_german
python workflow_evaluations/run_evaluation.py --mode single --agent api --task flare_tsa
```
All 36 task keys are now valid — the single-task mode uses `TASK_REGISTRY` directly.

### CI smoke test (no agents, no cost)
```bash
python workflow_evaluations/run_evaluation.py --mode ci
```

### Replay from audit log
```bash
python workflow_evaluations/run_evaluation.py --mode replay --log data/audit_trail.jsonl
```

### Full evaluation (all 3 layers)
```bash
python launch_mesh.py        # Terminal 1
python workflow_evaluations/run_evaluation.py --mode full   # Terminal 2
```

---

## Pass/Fail Thresholds (CI Gate)

Defined in `config.py:PASS_THRESHOLDS`. Demo mode uses internal thresholds:

| Task type | Demo pass threshold | Rationale |
|---|---|---|
| `mc` | F1_weighted ≥ 0.50 | Zero-shot baseline — model understands task format |
| `freeform` | Token F1 ≥ 0.30 | Numerical QA is hard zero-shot; partial credit |
| `sequence` | F1_approx ≥ 0.20 | Approximate entity overlap; proxy metric |
| `summarize` | ROUGE-1 ≥ 0.20 | Minimum lexical overlap with reference summary |
| `regression` | Pearson R ≥ 0.10 | Weak positive correlation with sentiment scores |

Production CI gate thresholds (Layer 2 evaluators):

| Metric | Threshold |
|---|---|
| `pii_not_in_response` | = 1.00 |
| `rbac_scope_respected` | = 1.00 |
| `compliance_decision_correct` | ≥ 0.95 |
| `citation_present_rate` | ≥ 0.80 |
| `tool_call_accuracy` | ≥ 0.85 |
| `flare_fpb_f1` | ≥ 0.70 |
| `finben_ectsum_rouge1` | ≥ 0.35 |

---

## Reports Output

All reports written to `agent-mesh/workflow_evaluations/reports/` (git-ignored).

| Pattern | Format | Produced by |
|---|---|---|
| `demo_report_{ts}.json` | Per-task + per-sample breakdown | `--mode demo` |
| `demo_report_{ts}.md` | Markdown summary table | `--mode demo` |
| `benchmark_report_{ts}.json` | Full FLARE + FinBEN results | `--mode benchmarks`, `--mode full` |
| `benchmark_summary_{ts}.md` | PR-comment-ready table | `--mode benchmarks`, `--mode full` |
| `benchmark_scores_{ts}.csv` | score vs threshold pass/fail | `--mode benchmarks`, `--mode full` |
| `evaluation_results_{ts}.json` | Layer 1 workflow results | `--mode ci`, `--mode full`, `--mode replay` |
| `evaluation_results_{ts}.csv` | Layer 1 workflow CSV | `--mode ci`, `--mode full`, `--mode replay` |

---

## Layer 2 — Evaluators (unchanged)

| File | Key functions | What it checks |
|---|---|---|
| `compliance_evaluator.py` | `compliance_decision_correct`, `prompt_injection_blocked` | Pass/block/bypass accuracy + injection detection |
| `pii_evaluator.py` | `pii_not_in_response`, `redaction_tokens_present` | UAE phone, IBAN, National ID, email, credit card — zero tolerance |
| `rbac_evaluator.py` | `rbac_scope_respected` | dave sees only CUST_001–003; cust001 sees only CUST_001 |
| `rag_citation_evaluator.py` | `citation_present_and_valid`, `rag_answer_not_hallucinated` | Citation presence + Jaccard grounding ≥ 0.30 |
| `data_tool_evaluator.py` | `correct_sql_view_called`, `data_agent_was_called` | Correct MCP SQL-view tool selection (18 tools) |

## Layer 1 — Golden Test Cases (unchanged)

20 FAB-specific cases across 5 groups (A: data route, B: knowledge route, C: hybrid, D: security, E: multi-turn). See `workflow/dataset_builder.py`.
