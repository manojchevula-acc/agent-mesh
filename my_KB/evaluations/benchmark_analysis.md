# Financial AI Benchmark Analysis — Fit for FAB Agent-Mesh

## Why we assessed existing benchmarks

Before building a custom evaluation framework, we assessed whether established financial AI benchmarks (FLUE, FLARE, FinBEN) could be adopted. The conclusion: none of them fit directly, but FinBEN's structural approach informed how we designed our own.

---

## FLUE — Financial Language Understanding Evaluation (2022)

**What it is:** The first standardised NLP benchmark for finance. Covers five core NLP tasks using publicly available financial text datasets.

**Tasks it covers:**
- Sentiment analysis on financial news headlines (FinSent, FiQA)
- Named entity recognition on earnings calls and SEC filings
- News headline binary classification (price up/down)
- Financial phrase categorisation (positive/negative/neutral)
- Aspect-level sentiment on financial forum posts

**Why it does not apply to our system:**
Our system does not perform sentiment classification, named entity extraction, or news headline categorisation. These are public-market, text-classification tasks. FAB's pricing assistant answers internal banking queries against proprietary MySQL pricing data and internal policy documents — tasks FLUE was never designed to evaluate.

**Verdict: Not applicable.**

---

## FLARE — Financial Language Understanding and Reasoning Evaluation (2023)

**What it is:** An expanded benchmark that added time-series financial tasks on top of FLUE's NLP foundation. First benchmark to combine language understanding with quantitative market prediction.

**Tasks it covers:**
- Stock movement forecasting (price goes up/down given news + historical data)
- Credit scoring on structured loan applicant data
- Fraud detection on transaction sequences
- Financial report summarisation
- Financial question answering (public market focused — Bloomberg, Reuters)
- Relation extraction from SEC filings

**Why it does not apply to our system:**
The defining feature of FLARE is time-series market data — predicting stock movement from a combination of news and historical pricing. FAB's system does not touch public equity markets, does not forecast prices, and does not process time-series. The fraud detection and credit scoring tasks are structurally closer to what we do, but the datasets are retail/consumer credit benchmarks, not corporate loan pricing compliance.

**Verdict: Not applicable.**

---

## FinBEN — Financial Benchmark (2024)

**What it is:** The most comprehensive financial AI benchmark to date, covering 36 datasets and 24 distinct tasks organised into six capability categories.

**Task categories and relevance to FAB:**

| FinBEN Category | Tasks included | Relevance to FAB |
|---|---|---|
| Information Extraction | NER, relation extraction, causal analysis from financial filings | Partial — our DataAgent extracts structured fields, but from internal MySQL, not SEC filings |
| Textual Analysis | Sentiment, headline classification, summarisation | Low — we do not classify public market text |
| Question Answering | Open-domain financial QA using public sources | Partial — our RAGAgent answers policy questions, but against internal documents not public data |
| Risk Management | Credit scoring, fraud detection, systemic risk assessment | Partial — our compliance agent assesses request risk, but the benchmark uses consumer credit datasets |
| Decision Making | Portfolio management, loan approval, investment decisions | Moderate — our system makes compliance block/pass decisions on pricing queries |
| Text Generation | Report generation, explanation generation | Low — we generate cited answers but this is an outcome, not our primary evaluation target |

**Why it is the closest match — but still does not fit directly:**

FinBEN's decision-making and question-answering categories are conceptually aligned with what our agents do. However, all 36 of its datasets are sourced from public financial markets — Bloomberg, Reuters, SEC EDGAR, Yahoo Finance, credit bureau data. Our ground truth lives in an internal MySQL pricing database (`fab_semantic`) and proprietary FAB policy documents. There is no overlap between FinBEN's test data and ours.

Plugging FAB's agents into FinBEN would be like benchmarking a hospital's internal triage system against public emergency statistics — the domain is related, but the data, tasks, and success criteria are entirely different.

**What we borrowed from FinBEN:**
- Its *evaluation structure* — per-task scoring, category-level aggregation, and a multi-dimensional verdict (not just a single accuracy number)
- Its approach to multi-task coverage — we score six pipeline stages independently rather than collapsing everything into one metric
- Its use of an LLM-as-judge for open-ended answer quality — we replicate this using our existing Groq `qwen/qwen3.6-27b` judge

**Verdict: Structurally informative, datasets not applicable. We build a domain-specific equivalent.**

---

## Why a custom benchmark is the right approach

| Dimension | Public benchmarks (FLUE/FLARE/FinBEN) | Our custom workflow evaluator |
|---|---|---|
| Data source | Public markets, SEC, Reuters, Bloomberg | Internal MySQL (fab_semantic) + FAB policy docs |
| Task type | Public market NLP + prediction | Internal pricing compliance + policy Q&A |
| Ground truth | Published academic labels | FAB-specific known answers (pricing floors, credit limits) |
| Regulatory context | Generic financial regulation references | CBUAE 2024, FAB credit policy, internal risk thresholds |
| Pipeline coverage | Single-model evaluation | Full 6-stage multi-agent pipeline |
| Compliance layer | Not present in any benchmark | First-class evaluation target |

Building a custom benchmark from our own data is the standard practice for domain-specific AI systems in regulated industries. Public benchmarks exist to compare models across institutions; our evaluator exists to validate a production system against the specific rules it will be enforced against in a live banking environment.

---

## Related documents

- [Workflow Evaluation Plan](workflow_evaluation_plan.md) — full technical implementation plan for the custom evaluator
