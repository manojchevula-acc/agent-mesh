"""Unified task registry -- all 36 FinBEN + FLARE datasets, 5 generic async runners.

All TheFinAI/flare-* datasets share the same schema:
  query   : str        Full prompt embedding context + instruction (send directly to mesh)
  text    : str        Raw context (logging only)
  choices : list[str]  Answer options           (multiple-choice tasks)
  gold    : int        Correct choice index     (multiple-choice tasks)
  answer  : str        Free-form gold answer    (QA / summarisation / sequence tasks)
  label   : list[str]  BIO / span labels        (sequence-labelling tasks)
  token   : list[str]  Tokenised input          (sequence-labelling tasks)

Runner dispatch by type:
  "mc"         → run_multiple_choice_task   Acc + Weighted-F1 (+ MCC for binary)
  "freeform"   → run_freeform_task          Exact-Match + Token-F1
  "sequence"   → run_sequence_task          token-overlap F1 on answer strings
  "summarize"  → run_summarization_task     ROUGE-1 / ROUGE-2 / ROUGE-L
  "regression" → run_regression_task        MSE + Pearson-R
"""
from __future__ import annotations

import asyncio
import os
import re
import sys
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

import httpx


# ---------------------------------------------------------------------------
# Task registry -- 36 datasets
# ---------------------------------------------------------------------------

TASK_REGISTRY: Dict[str, Dict[str, Any]] = {
    # ── Category 1: Information Extraction (IE) ─────────────────────────────
    "flare_ner": {
        "dataset_id":  "TheFinAI/flare-ner",
        "type":        "sequence",
        "metric":      "f1",
        "agent":       "RAGAgent",
        "tier":        1,
        "category":    "Information Extraction",
        "description": "Named entity recognition -- PER / ORG / LOC from financial filings",
        "sample_query": "Identify named entities (PER/ORG/LOC) from: 'Silicium de Provence SAS agreed to supply Evergreen Solar Inc under a long-term contract.'",
        "sample_gold":  "Silicium de Provence SAS, ORG\nEvergreen Solar Inc, ORG",
    },
    "flare_finer_ord": {
        "dataset_id":  "TheFinAI/flare-finer-ord",
        "type":        "sequence",
        "metric":      "f1",
        "agent":       "RAGAgent",
        "tier":        2,
        "category":    "Information Extraction",
        "description": "Numeric expression recognition and ordinal labelling in financial text",
        "sample_query": "Identify and label numeric expressions in: 'Revenue grew 12.5% to $4.3 billion in Q3 2023.'",
        "sample_gold":  "12.5%, PERCENT\n$4.3 billion, MONEY\nQ3 2023, DATE",
    },
    "flare_finred": {
        "dataset_id":  "TheFinAI/flare-finred",
        "type":        "sequence",
        "metric":      "f1",
        "agent":       "RAGAgent",
        "tier":        1,
        "category":    "Information Extraction",
        "description": "Relation extraction between entities in financial text",
        "sample_query": "Extract the relation between entities in: 'Apple acquired Intel's smartphone modem business for $1 billion.'",
        "sample_gold":  "Apple, acquired, Intel",
    },
    "flare_causal_sc": {
        "dataset_id":  "TheFinAI/flare-causal20-sc",
        "type":        "mc",
        "metric":      "f1_acc",
        "agent":       "ComplianceAgent",
        "tier":        2,
        "category":    "Information Extraction",
        "description": "Causal sentence classification -- does the sentence contain a financial causal relationship?",
        "sample_query": "Does this sentence describe a financial causal relationship? 'Higher interest rates led to a slowdown in mortgage applications.' Answer: yes or no",
        "sample_gold":  "yes",
    },
    "flare_causal_cd": {
        "dataset_id":  "TheFinAI/flare-cd",
        "type":        "sequence",
        "metric":      "f1",
        "agent":       "ComplianceAgent",
        "tier":        2,
        "category":    "Information Extraction",
        "description": "Causal span detection -- extract cause and effect spans from financial text",
        "sample_query": "Identify cause and effect in: 'The Fed raised rates by 75bps, causing mortgage demand to drop 20%.'",
        "sample_gold":  "cause: raised rates 75bps | effect: mortgage demand dropped 20%",
    },
    "flare_fnxl": {
        "dataset_id":  "TheFinAI/flare-fnxl",
        "type":        "sequence",
        "metric":      "f1",
        "agent":       "DataAgent",
        "tier":        1,
        "category":    "Information Extraction",
        "description": "Numeric expression labelling in XBRL financial filings (MONETARY / PERCENT / DATE / CARDINAL)",
        "sample_query": "Label numeric expressions in: 'Net income was $2.1 billion, up 15%, in fiscal year 2022.'",
        "sample_gold":  "$2.1 billion, MONETARY\n15%, PERCENT\n2022, DATE",
    },
    "flare_fsrl": {
        "dataset_id":  "TheFinAI/flare-fsrl",
        "type":        "sequence",
        "metric":      "f1",
        "agent":       "DataAgent",
        "tier":        1,
        "category":    "Information Extraction",
        "description": "Financial span role labelling -- subject / relation / object in financial statements",
        "sample_query": "Label the financial span roles (subject, relation, object) in: 'Apple's revenue increased to $90 billion.'",
        "sample_gold":  "subject: Apple's revenue | relation: increased to | object: $90 billion",
    },

    # ── Category 2: Textual Analysis ────────────────────────────────────────
    "flare_fpb": {
        "dataset_id":  "TheFinAI/en-fpb",
        "type":        "mc",
        "metric":      "f1_acc",
        "agent":       "RAGAgent",
        "tier":        2,
        "category":    "Textual Analysis",
        "description": "Financial PhraseBank sentiment -- positive / negative / neutral",
        "sample_query": "Classify sentiment of this financial news as positive, negative, or neutral: 'Profit rose 12% in Q3, beating analyst estimates.'",
        "sample_gold":  "positive",
    },
    "finben_fiqa": {
        "dataset_id":  "TheFinAI/fiqa-sentiment-classification",
        "type":        "mc",
        "metric":      "f1_acc",
        "agent":       "RAGAgent",
        "tier":        1,
        "category":    "Textual Analysis",
        "description": "FiQA aspect-based sentiment on financial headlines and microblogs",
        "sample_query": "Classify the financial sentiment as positive, negative, or neutral: 'Weak iPhone sales weigh on Apple earnings outlook.'",
        "sample_gold":  "negative",
    },
    "flare_tsa": {
        "dataset_id":  "TheFinAI/flare-tsa",
        "type":        "regression",
        "metric":      "mse_pearson",
        "agent":       "RAGAgent",
        "tier":        1,
        "category":    "Textual Analysis",
        "description": "Target-specific sentiment scoring: float -1 (very negative) to +1 (very positive) for a named company",
        "sample_query": "Return a sentiment score from -1 to 1 for Ashtead: 'Ashtead to buy back shares, full-year profit beats estimates.'",
        "sample_gold":  "0.588",
    },
    "flare_headlines": {
        "dataset_id":  "TheFinAI/flare-headlines",
        "type":        "mc",
        "metric":      "f1_acc",
        "agent":       "ComplianceAgent",
        "tier":        1,
        "category":    "Textual Analysis",
        "description": "Financial headline classification -- price-sensitive (yes/no)",
        "sample_query": "Is this financial headline price-sensitive? 'Fed signals three rate cuts in 2024.' Answer yes or no.",
        "sample_gold":  "yes",
    },
    "flare_fomc": {
        "dataset_id":  "TheFinAI/flare-fomc",
        "type":        "mc",
        "metric":      "f1_acc",
        "agent":       "ComplianceAgent",
        "tier":        2,
        "category":    "Textual Analysis",
        "description": "FOMC monetary policy stance classification -- hawkish / dovish / neutral",
        "sample_query": "Classify this Federal Reserve statement as hawkish, dovish, or neutral: 'The Committee decided to maintain the target range for the federal funds rate at 5.25-5.5%.'",
        "sample_gold":  "neutral",
    },
    "flare_finarg_auc": {
        "dataset_id":  "TheFinAI/flare-finarg-ecc-auc",
        "type":        "mc",
        "metric":      "f1_acc",
        "agent":       "ComplianceAgent",
        "tier":        2,
        "category":    "Textual Analysis",
        "description": "Financial argument unit classification -- claim / premise / other in earnings call transcripts",
        "sample_query": "Classify this earnings call segment as claim, premise, or other: 'We expect revenue growth of 15% driven by cloud adoption.'",
        "sample_gold":  "claim",
    },
    "flare_finarg_arc": {
        "dataset_id":  "TheFinAI/flare-finarg-ecc-arc",
        "type":        "mc",
        "metric":      "f1_acc",
        "agent":       "ComplianceAgent",
        "tier":        2,
        "category":    "Textual Analysis",
        "description": "Financial argument relation classification -- support / attack between argument units",
        "sample_query": "Does this statement support or attack the claim 'Revenue will grow 15%'? Statement: 'Our new product line launched last quarter and already shows strong traction.'",
        "sample_gold":  "support",
    },
    "flare_multifin": {
        "dataset_id":  "TheFinAI/flare-multifin-en",
        "type":        "mc",
        "metric":      "f1_acc",
        "agent":       "RAGAgent",
        "tier":        2,
        "category":    "Textual Analysis",
        "description": "MultiFin multi-class financial headline topic classification (12 categories)",
        "sample_query": "What financial topic does this headline cover? 'ECB holds rates steady as inflation falls toward target.' Options: monetary policy, earnings, M&A, IPO, regulation, credit, dividend, macro, analyst, legal, ESG, other",
        "sample_gold":  "monetary policy",
    },
    "flare_ma": {
        "dataset_id":  "TheFinAI/flare-ma",
        "type":        "mc",
        "metric":      "f1_acc",
        "agent":       "ComplianceAgent",
        "tier":        1,
        "category":    "Textual Analysis",
        "description": "M&A deal status classification -- rumour / complete",
        "sample_query": "Will this M&A deal be completed or is it still a rumour? 'Berkshire Hathaway is reportedly in preliminary talks to acquire Southwest Airlines at $75/share, though Southwest declined to comment.' Choices: rumour, complete",
        "sample_gold":  "rumour",
    },
    "flare_mlesg": {
        "dataset_id":  "TheFinAI/flare-mlesg",
        "type":        "mc",
        "metric":      "f1_acc",
        "agent":       "ComplianceAgent",
        "tier":        1,
        "category":    "Textual Analysis",
        "description": "ESG issue identification -- classify news article into one of 33 MSCI ESG categories",
        "sample_query": "Classify this article by ESG issue (MSCI framework): 'AT&T and LA Unified School District partnered to provide no-cost broadband to students via FCC Emergency Connectivity Fund.'",
        "sample_gold":  "Access to Communications",
    },

    # ── Category 3: Question Answering (QA) ─────────────────────────────────
    "finben_finqa": {
        "dataset_id":  "TheFinAI/flare-finqa",
        "type":        "freeform",
        "metric":      "em_f1",
        "agent":       "PriceAssistAgent",
        "tier":        1,
        "category":    "Question Answering",
        "description": "FinQA numerical reasoning over financial reports -- requires multi-step arithmetic",
        "sample_query": "Based on this financial context, answer precisely. Context: Revenue in 2020 was $50M; in 2021 it was $65M. Question: What was the revenue growth rate from 2020 to 2021?",
        "sample_gold":  "30%",
    },
    "flare_tatqa": {
        "dataset_id":  "TheFinAI/flare-tatqa",
        "type":        "freeform",
        "metric":      "em_f1",
        "agent":       "DataAgent",
        "tier":        1,
        "category":    "Question Answering",
        "description": "TatQA -- QA over combined financial tables and text, requires reading both",
        "sample_query": "Answer the financial question from the table and text. Context: [2019 sales: $1,496.5M | 2018 sales: $1,412.2M]. Question: What is the amount of total sales in 2019?",
        "sample_gold":  "$1,496.5",
    },
    "flare_convfinqa": {
        "dataset_id":  "TheFinAI/ConvFinQA",
        "type":        "freeform",
        "metric":      "em_f1",
        "agent":       "RAGAgent",
        "tier":        1,
        "category":    "Question Answering",
        "description": "ConvFinQA -- multi-turn conversational QA over financial reports with context carry-over",
        "sample_query": "Financial context: [income statement table]. Q1: What was net income in 2019? (answer: $21.2M). Q2: How does that compare to 2018?",
        "sample_gold":  "Net income grew from $18.4M in 2018 to $21.2M in 2019, an increase of 15.2%.",
    },
    "flare_regulations": {
        "dataset_id":  "TheFinAI/flare-regulations",
        "type":        "freeform",
        "metric":      "em_f1",
        "agent":       "RAGAgent",
        "tier":        2,
        "category":    "Question Answering",
        "description": "Long-form regulatory QA -- answer questions about financial regulations (Basel III, MiFID II, etc.)",
        "sample_query": "What is the minimum Common Equity Tier 1 (CET1) capital ratio required under Basel III for systemically important banks?",
        "sample_gold":  "7% (4.5% minimum + 2.5% capital conservation buffer)",
    },

    # ── Category 4: Text Generation ─────────────────────────────────────────
    "finben_ectsum": {
        "dataset_id":  "TheFinAI/flare-ectsum",
        "type":        "summarize",
        "metric":      "rouge",
        "agent":       "PriceAssistAgent",
        "tier":        1,
        "category":    "Text Generation",
        "description": "ECTSum -- extractive summarisation of earnings call transcripts into key bullet points",
        "sample_query": "Summarise this earnings call in 3-5 bullet points: 'Good morning. Q3 revenue reached $4.2 billion, up 18% year-over-year driven by cloud services. Operating margin expanded 200bps to 28%. We are raising full-year guidance to $16-16.5 billion...'",
        "sample_gold":  "• Q3 revenue $4.2B, +18% YoY\n• Cloud services primary growth driver\n• Operating margin 28% (+200bps)\n• FY guidance raised to $16-16.5B",
    },
    "flare_edtsum": {
        "dataset_id":  "TheFinAI/flare-edtsum",
        "type":        "summarize",
        "metric":      "rouge",
        "agent":       "RAGAgent",
        "tier":        2,
        "category":    "Text Generation",
        "description": "EDTSum -- abstractive summarisation of financial news articles into 2-3 sentence digests",
        "sample_query": "Summarise this financial news in 2-3 sentences: 'The Federal Reserve raised its benchmark interest rate by 25 basis points on Wednesday, marking the tenth consecutive increase since March 2022, bringing rates to their highest level in 22 years...'",
        "sample_gold":  "The Fed raised rates 25bps to a 22-year high. This marks the tenth straight hike since March 2022.",
    },

    # ── Category 5: Risk Management ──────────────────────────────────────────
    "flare_german": {
        "dataset_id":  "TheFinAI/flare-german",
        "type":        "mc",
        "metric":      "f1_mcc",
        "agent":       "ComplianceAgent",
        "tier":        1,
        "category":    "Risk Management",
        "description": "German credit scoring -- assess creditworthiness (good/bad) from 20 financial attributes",
        "sample_query": "Assess creditworthiness from attributes: checking_account=no_account, duration=6mo, credit_history=all_paid, purpose=furniture, amount=1169, savings=unknown, employment=7+yrs. Answer good or bad.",
        "sample_gold":  "good",
    },
    "flare_australian": {
        "dataset_id":  "TheFinAI/flare-australian",
        "type":        "mc",
        "metric":      "f1_mcc",
        "agent":       "ComplianceAgent",
        "tier":        1,
        "category":    "Risk Management",
        "description": "Australian credit scoring -- approve or reject credit application from 14 attributes",
        "sample_query": "Given applicant attributes: A1=b, A2=30.83, A3=0, A4=u, A5=g, A6=w, A7=v, A8=1.25, A9=t, A10=t, A11=01, A12=f, A13=g, A14=202. Approve credit? Answer yes or no.",
        "sample_gold":  "yes",
    },
    "flare_lendingclub": {
        "dataset_id":  "TheFinAI/flare-cra-lendingclub",
        "type":        "mc",
        "metric":      "f1_auroc",
        "agent":       "ComplianceAgent",
        "tier":        2,
        "category":    "Risk Management",
        "description": "LendingClub loan default prediction -- will this loan default? (yes/no)",
        "sample_query": "Given this loan application: loan_amount=$10,000, term=36mo, interest_rate=11.44%, grade=B, employment=10+yrs, home=MORTGAGE, income=$65,000, dti=15.6. Will this loan default? Answer yes or no.",
        "sample_gold":  "no",
    },
    "flare_ccf": {
        "dataset_id":  "TheFinAI/flare-cra-ccf",
        "type":        "mc",
        "metric":      "f1_auroc",
        "agent":       "ComplianceAgent",
        "tier":        2,
        "category":    "Risk Management",
        "description": "CCF credit card fraud detection -- is this transaction fraudulent?",
        "sample_query": "Given transaction features: V1=-1.36, V2=0.07, V3=2.54, V4=1.38, V5=-0.34, Amount=$149.62, Time=0s. Is this transaction fraudulent? Answer yes or no.",
        "sample_gold":  "no",
    },
    "flare_ccfraud": {
        "dataset_id":  "TheFinAI/flare-cra-ccfraud",
        "type":        "mc",
        "metric":      "f1_auroc",
        "agent":       "ComplianceAgent",
        "tier":        2,
        "category":    "Risk Management",
        "description": "CCFraud fraud detection -- classify credit card transaction as fraudulent or legitimate",
        "sample_query": "Classify this credit card transaction: category=grocery_pos, amount=$149.46, merchant_lat=36.01, merchant_long=-81.07, city_pop=523, transaction_hour=14. Is it fraudulent or legitimate?",
        "sample_gold":  "legitimate",
    },
    "flare_polish": {
        "dataset_id":  "TheFinAI/flare-cra-polish",
        "type":        "mc",
        "metric":      "f1_acc",
        "agent":       "ComplianceAgent",
        "tier":        2,
        "category":    "Risk Management",
        "description": "Polish financial distress -- predict if company will face bankruptcy in next year",
        "sample_query": "Given company financial ratios: net_profit/total_assets=0.12, total_liabilities/total_assets=0.48, working_capital/total_assets=0.31, current_ratio=2.1, EBITDA/total_assets=0.18. Will this company face financial distress? Answer yes or no.",
        "sample_gold":  "no",
    },
    "flare_taiwan": {
        "dataset_id":  "TheFinAI/flare-cra-taiwan",
        "type":        "mc",
        "metric":      "f1_acc",
        "agent":       "ComplianceAgent",
        "tier":        2,
        "category":    "Risk Management",
        "description": "Taiwan Economic Journal -- corporate default prediction from financial indicators",
        "sample_query": "Given a Taiwanese company's indicators: X1=0.23 (ROA), X2=0.51 (debt_ratio), X3=0.78 (current_ratio), X4=0.12 (cash_flow_ratio), X5=0.04 (EPS). Will this company default? Answer yes or no.",
        "sample_gold":  "no",
    },
    "flare_portoseguro": {
        "dataset_id":  "TheFinAI/flare-cra-portoseguro",
        "type":        "mc",
        "metric":      "f1_acc",
        "agent":       "ComplianceAgent",
        "tier":        2,
        "category":    "Risk Management",
        "description": "Porto Seguro auto insurance -- will driver file an insurance claim in next year?",
        "sample_query": "Given driver attributes: ps_ind_01=2, ps_ind_02_cat=2, ps_ind_03=5, ps_ind_04_cat=1, vehicle_age=3, annual_premium=1800. Will this driver file an insurance claim? Answer yes or no.",
        "sample_gold":  "no",
    },
    "flare_travelinsurance": {
        "dataset_id":  "TheFinAI/flare-cra-travelinsurance",
        "type":        "mc",
        "metric":      "f1_acc",
        "agent":       "ComplianceAgent",
        "tier":        2,
        "category":    "Risk Management",
        "description": "Travel insurance claim prediction -- will traveller file a claim?",
        "sample_query": "Given traveller profile: age=35, employment=private_sector, annual_income=$50000, chronic_disease=no, frequent_flyer=yes, abroad_travel=yes, travel_insurance_plan=basic. Will they file a claim? Answer yes or no.",
        "sample_gold":  "yes",
    },

    # ── Category 6: Forecasting ──────────────────────────────────────────────
    "flare_bigdata22": {
        "dataset_id":  "TheFinAI/flare-sm-bigdata",
        "type":        "mc",
        "metric":      "acc_mcc",
        "agent":       "DataAgent",
        "tier":        1,
        "category":    "Forecasting",
        "description": "BigData22 -- stock movement prediction (Rise/Fall) from financial news",
        "sample_query": "Based on this financial news, will the stock price rise or fall? 'Tesla reports record deliveries of 484,507 vehicles in Q4, beating analyst expectations of 473,000.' Answer Rise or Fall.",
        "sample_gold":  "Rise",
    },
    "flare_acl18": {
        "dataset_id":  "TheFinAI/flare-sm-acl",
        "type":        "mc",
        "metric":      "acc_mcc",
        "agent":       "DataAgent",
        "tier":        1,
        "category":    "Forecasting",
        "description": "ACL18 -- stock movement prediction (Rise/Fall) combining price history and social media",
        "sample_query": "By reviewing price data and tweets, predict if $AAPL will Rise or Fall on 2015-10-01. Historical data: 10-day close prices declining 2.1%. Recent tweets: analyst downgrades, China sales concerns. Answer Rise or Fall.",
        "sample_gold":  "Fall",
    },
    "flare_cikm18": {
        "dataset_id":  "TheFinAI/flare-sm-cikm",
        "type":        "mc",
        "metric":      "acc_mcc",
        "agent":       "DataAgent",
        "tier":        1,
        "category":    "Forecasting",
        "description": "CIKM18 -- stock movement prediction (Rise/Fall) combining price history and social media",
        "sample_query": "Predict if this stock will Rise or Fall based on: closing price dropped 1.8% over last 5 days; recent news: strong quarterly guidance raised; tweet volume spike positive. Answer Rise or Fall.",
        "sample_gold":  "Rise",
    },

    # ── Category 7: Decision Making ──────────────────────────────────────────
    "flare_dm_simple": {
        "dataset_id":  "TheFinAI/flare-dm-simplong",
        "type":        "mc",
        "metric":      "f1_acc",
        "agent":       "DataAgent",
        "tier":        2,
        "category":    "Decision Making",
        "description": "FinTrade (simple) -- single-stock trading decision: buy / hold / sell based on market data",
        "sample_query": "Based on this market data, should we buy, hold, or sell this stock? RSI=42 (oversold approaching), MACD=bullish crossover, P/E=18 (below sector avg 22), recent earnings beat by 12%. Answer buy, hold, or sell.",
        "sample_gold":  "buy",
    },
    "flare_dm_complex": {
        "dataset_id":  "TheFinAI/flare-dm-complong",
        "type":        "mc",
        "metric":      "f1_acc",
        "agent":       "DataAgent",
        "tier":        2,
        "category":    "Decision Making",
        "description": "FinTrade (complex) -- multi-factor portfolio trading decision with long time horizon",
        "sample_query": "Given 30-day market context: sector rotation into defensives, Fed hawkish pivot, VIX=28 (elevated), stock up 45% YTD (extended). Portfolio allocation recommendation: buy, hold, or sell?",
        "sample_gold":  "sell",
    },
}

# ---------------------------------------------------------------------------
# Dataclass for task results
# ---------------------------------------------------------------------------

@dataclass
class BenchmarkTaskResult:
    task_name:  str
    dataset_id: str
    task_type:  str
    n_samples:  int
    metrics:    Dict[str, float] = field(default_factory=dict)
    per_sample: List[dict]       = field(default_factory=list)
    error:      Optional[str]    = None


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

def _normalise(text: str) -> str:
    return re.sub(r"\s+", " ", str(text).strip().lower())


def _exact_match(pred: str, gold: str) -> float:
    return 1.0 if _normalise(pred) == _normalise(gold) else 0.0


def _token_f1(pred: str, gold: str) -> float:
    p_toks = set(_normalise(pred).split())
    g_toks = set(_normalise(gold).split())
    if not p_toks or not g_toks:
        return 0.0
    prec = len(p_toks & g_toks) / len(p_toks)
    rec  = len(p_toks & g_toks) / len(g_toks)
    return 2 * prec * rec / (prec + rec) if (prec + rec) > 0 else 0.0


def _parse_choice(response: str, choices: List[str]) -> str:
    """Return the choice whose text appears earliest in the response (case-insensitive)."""
    lower = response.lower()
    for ch in choices:
        if ch.lower() in lower:
            return ch
    return choices[0]


async def _call_agent(
    client: httpx.AsyncClient,
    endpoint: str,
    query: str,
    semaphore: asyncio.Semaphore,
    username: str = "bob",
    timeout: float = 60.0,
) -> str:
    async with semaphore:
        try:
            resp = await client.post(
                f"{endpoint}/api/query",
                json={"query": query, "username": username},
                timeout=timeout,
            )
            resp.raise_for_status()
            data = resp.json()
            return data.get("answer") or data.get("response") or str(data)
        except Exception as exc:
            return f"ERROR: {exc}"


def _load_dataset_safe(dataset_id: str, split: str = "test", n: int = 10):
    """Load n samples from a HuggingFace dataset, return list of dicts.

    Raises a descriptive RuntimeError on access failure so callers can set
    result.error = "DATASET_UNAVAILABLE: ..." rather than a raw exception string.
    """
    from datasets import load_dataset
    try:
        ds = load_dataset(dataset_id, split=split)
        return list(ds.select(range(min(n, len(ds)))))
    except Exception as first_exc:
        err_str = str(first_exc)
        # Gated: explicitly marked as gated repo, or HTTP 401/403 (auth failure)
        if "gated" in err_str.lower() or "403" in err_str or "401" in err_str:
            raise RuntimeError(
                f"DATASET_UNAVAILABLE: {dataset_id} requires access approval — "
                f"visit https://huggingface.co/datasets/{dataset_id} and accept the terms, "
                f"then re-run"
            ) from first_exc
        # Missing: dataset does not exist on the Hub
        if "doesn't exist" in err_str.lower() or "404" in err_str or "not found" in err_str.lower() or "repository" in err_str.lower():
            raise RuntimeError(f"DATASET_UNAVAILABLE: {dataset_id} does not exist on the Hub") from first_exc
        # Fall back to train split (some datasets have no test split)
        try:
            ds = load_dataset(dataset_id, split="train")
            return list(ds.select(range(min(n, len(ds)))))
        except Exception as second_exc:
            err2 = str(second_exc)
            if "gated" in err2.lower() or "403" in err2 or "401" in err2:
                raise RuntimeError(
                    f"DATASET_UNAVAILABLE: {dataset_id} requires access approval — "
                    f"visit https://huggingface.co/datasets/{dataset_id} and accept the terms, "
                    f"then re-run"
                ) from second_exc
            raise RuntimeError(f"DATASET_UNAVAILABLE: {dataset_id} — {second_exc}") from second_exc


# ---------------------------------------------------------------------------
# Generic runner 1: Multiple-choice tasks
# ---------------------------------------------------------------------------

async def run_multiple_choice_task(
    task_name:    str,
    api_endpoint: str,
    n_samples:    int = 10,
    dry_run:      bool = False,
) -> BenchmarkTaskResult:
    """Generic runner for all MC tasks.

    Reads: item["query"] as prompt, item["choices"] as options, item["gold"] as correct index.
    Metrics: accuracy + weighted F1; MCC added for binary tasks.
    """
    info   = TASK_REGISTRY[task_name]
    result = BenchmarkTaskResult(task_name, info["dataset_id"], "mc", n_samples)
    if dry_run:
        print(f"  [DRY RUN] {task_name}: would load {n_samples} samples from {info['dataset_id']}")
        return result
    try:
        from sklearn.metrics import f1_score, accuracy_score, matthews_corrcoef
        samples = _load_dataset_safe(info["dataset_id"], n=n_samples)
        semaphore = asyncio.Semaphore(5)
        preds, golds = [], []
        async with httpx.AsyncClient() as client:
            coros = []
            for item in samples:
                choices = item.get("choices", [])
                choices_str = " / ".join(choices) if choices else ""
                prompt = item.get("query", "")
                if choices and choices_str not in prompt.lower():
                    prompt = f"{prompt}\nAnswer with one of: {choices_str}"
                coros.append(_call_agent(client, api_endpoint, prompt, semaphore))
            responses = await asyncio.gather(*coros)

        for item, response in zip(samples, responses):
            choices = item.get("choices", [])
            gold_idx = item.get("gold", 0)
            gold_label = choices[gold_idx] if choices and gold_idx < len(choices) else str(item.get("answer", ""))
            pred_label = _parse_choice(response, choices) if choices else response[:50]
            match = _normalise(pred_label) == _normalise(gold_label)
            preds.append(_normalise(pred_label))
            golds.append(_normalise(gold_label))
            result.per_sample.append({
                "query_preview": item.get("text", item.get("query", ""))[:80],
                "gold": gold_label,
                "pred": pred_label,
                "match": match,
            })

        result.metrics["accuracy"] = accuracy_score(golds, preds)
        result.metrics["f1_weighted"] = f1_score(golds, preds, average="weighted", zero_division=0)
        unique_labels = set(golds)
        if len(unique_labels) == 2:
            result.metrics["mcc"] = float(matthews_corrcoef(golds, preds))
    except Exception as exc:
        result.error = str(exc)
    return result


# ---------------------------------------------------------------------------
# Generic runner 2: Free-form QA tasks
# ---------------------------------------------------------------------------

async def run_freeform_task(
    task_name:    str,
    api_endpoint: str,
    n_samples:    int = 10,
    dry_run:      bool = False,
) -> BenchmarkTaskResult:
    """Generic runner for free-form QA tasks (FinQA, TatQA, ConvFinQA, Regulations).

    Reads: item["query"] as prompt, item["answer"] as gold.
    Metrics: Exact Match + Token F1.
    """
    info   = TASK_REGISTRY[task_name]
    result = BenchmarkTaskResult(task_name, info["dataset_id"], "freeform", n_samples)
    if dry_run:
        print(f"  [DRY RUN] {task_name}: would load {n_samples} samples from {info['dataset_id']}")
        return result
    try:
        samples = _load_dataset_safe(info["dataset_id"], n=n_samples)
        semaphore = asyncio.Semaphore(5)
        async with httpx.AsyncClient() as client:
            coros = [_call_agent(client, api_endpoint, item.get("query", ""), semaphore) for item in samples]
            responses = await asyncio.gather(*coros)

        em_scores, f1_scores = [], []
        for item, response in zip(samples, responses):
            gold = str(item.get("answer", ""))
            em   = _exact_match(response, gold)
            f1   = _token_f1(response, gold)
            em_scores.append(em)
            f1_scores.append(f1)
            result.per_sample.append({
                "query_preview": item.get("text", "")[:80] or str(item.get("query", ""))[:80],
                "gold": gold[:80],
                "pred": response[:80],
                "em":   em,
                "f1":   f1,
            })

        result.metrics["exact_match"] = sum(em_scores) / len(em_scores) if em_scores else 0.0
        result.metrics["token_f1"]    = sum(f1_scores) / len(f1_scores) if f1_scores else 0.0
    except Exception as exc:
        result.error = str(exc)
    return result


# ---------------------------------------------------------------------------
# Generic runner 3: Sequence labelling tasks
# ---------------------------------------------------------------------------

async def run_sequence_task(
    task_name:    str,
    api_endpoint: str,
    n_samples:    int = 10,
    dry_run:      bool = False,
) -> BenchmarkTaskResult:
    """Generic runner for sequence / span extraction tasks (NER, FinRED, FNXL, FSRL, FinCausal-CD).

    Reads: item["query"] as prompt, item["answer"] as gold entity/span string.
    Metrics: capitalised token overlap F1 (proxy for entity-level F1).
    """
    info   = TASK_REGISTRY[task_name]
    result = BenchmarkTaskResult(task_name, info["dataset_id"], "sequence", n_samples)
    if dry_run:
        print(f"  [DRY RUN] {task_name}: would load {n_samples} samples from {info['dataset_id']}")
        return result
    try:
        samples = _load_dataset_safe(info["dataset_id"], n=n_samples)
        semaphore = asyncio.Semaphore(5)
        async with httpx.AsyncClient() as client:
            coros = [_call_agent(client, api_endpoint, item.get("query", ""), semaphore) for item in samples]
            responses = await asyncio.gather(*coros)

        f1_scores = []
        for item, response in zip(samples, responses):
            gold = str(item.get("answer", ""))
            pred_tokens = set(re.findall(r"\b[A-Z][a-zA-Z]+\b", response))
            gold_tokens = set(re.findall(r"\b[A-Z][a-zA-Z]+\b", gold))
            prec = len(pred_tokens & gold_tokens) / len(pred_tokens) if pred_tokens else 0.0
            rec  = len(pred_tokens & gold_tokens) / len(gold_tokens) if gold_tokens else 0.0
            f1   = 2 * prec * rec / (prec + rec) if (prec + rec) > 0 else 0.0
            f1_scores.append(f1)
            result.per_sample.append({
                "query_preview": item.get("text", "")[:80],
                "gold": gold[:80],
                "pred": response[:80],
                "f1":   f1,
            })

        result.metrics["f1_approx"] = sum(f1_scores) / len(f1_scores) if f1_scores else 0.0
    except Exception as exc:
        result.error = str(exc)
    return result


# ---------------------------------------------------------------------------
# Generic runner 4: Summarisation tasks
# ---------------------------------------------------------------------------

async def run_summarization_task(
    task_name:    str,
    api_endpoint: str,
    n_samples:    int = 5,
    dry_run:      bool = False,
) -> BenchmarkTaskResult:
    """Generic runner for summarisation tasks (ECTSum, EDTSum).

    Reads: item["query"] as prompt, item["answer"] as reference summary.
    Metrics: ROUGE-1, ROUGE-2, ROUGE-L.
    """
    info   = TASK_REGISTRY[task_name]
    result = BenchmarkTaskResult(task_name, info["dataset_id"], "summarize", n_samples)
    if dry_run:
        print(f"  [DRY RUN] {task_name}: would load {n_samples} samples from {info['dataset_id']}")
        return result
    try:
        from rouge_score import rouge_scorer as rs_lib
        samples = _load_dataset_safe(info["dataset_id"], n=n_samples)
        semaphore = asyncio.Semaphore(3)
        async with httpx.AsyncClient() as client:
            coros = [_call_agent(client, api_endpoint, item.get("query", ""), semaphore, timeout=90.0) for item in samples]
            responses = await asyncio.gather(*coros)

        scorer = rs_lib.RougeScorer(["rouge1", "rouge2", "rougeL"], use_stemmer=True)
        r1, r2, rL = [], [], []
        for item, response in zip(samples, responses):
            ref = str(item.get("answer", item.get("summary", "")))
            scores = scorer.score(ref, response)
            r1.append(scores["rouge1"].fmeasure)
            r2.append(scores["rouge2"].fmeasure)
            rL.append(scores["rougeL"].fmeasure)
            result.per_sample.append({
                "query_preview": str(item.get("query", ""))[:80],
                "gold": ref[:120],
                "pred": response[:120],
                "rouge1": scores["rouge1"].fmeasure,
            })

        result.metrics["rouge1"] = sum(r1) / len(r1) if r1 else 0.0
        result.metrics["rouge2"] = sum(r2) / len(r2) if r2 else 0.0
        result.metrics["rougeL"] = sum(rL) / len(rL) if rL else 0.0
    except Exception as exc:
        result.error = str(exc)
    return result


# ---------------------------------------------------------------------------
# Generic runner 5: Regression / continuous scoring (TSA)
# ---------------------------------------------------------------------------

async def run_regression_task(
    task_name:    str,
    api_endpoint: str,
    n_samples:    int = 10,
    dry_run:      bool = False,
) -> BenchmarkTaskResult:
    """Generic runner for continuous score tasks (TSA: target sentiment -1 to +1).

    Reads: item["query"] as prompt, item["answer"] as float gold score.
    Metrics: MSE + Pearson R.
    Response parsing: extract first float from the model's response.
    """
    info   = TASK_REGISTRY[task_name]
    result = BenchmarkTaskResult(task_name, info["dataset_id"], "regression", n_samples)
    if dry_run:
        print(f"  [DRY RUN] {task_name}: would load {n_samples} samples from {info['dataset_id']}")
        return result
    try:
        samples = _load_dataset_safe(info["dataset_id"], n=n_samples)
        semaphore = asyncio.Semaphore(5)
        async with httpx.AsyncClient() as client:
            coros = [_call_agent(client, api_endpoint, item.get("query", ""), semaphore) for item in samples]
            responses = await asyncio.gather(*coros)

        preds_f, golds_f = [], []
        for item, response in zip(samples, responses):
            gold = float(item.get("answer", 0.0))
            nums = re.findall(r"-?\d+\.?\d*", response)
            pred = float(nums[0]) if nums else 0.0
            pred = max(-1.0, min(1.0, pred))
            preds_f.append(pred)
            golds_f.append(gold)
            result.per_sample.append({
                "query_preview": item.get("text", "")[:80],
                "gold": gold,
                "pred": pred,
                "error": abs(pred - gold),
            })

        n = len(preds_f)
        if n:
            mse = sum((p - g) ** 2 for p, g in zip(preds_f, golds_f)) / n
            mean_p = sum(preds_f) / n
            mean_g = sum(golds_f) / n
            cov = sum((p - mean_p) * (g - mean_g) for p, g in zip(preds_f, golds_f)) / n
            std_p = (sum((p - mean_p) ** 2 for p in preds_f) / n) ** 0.5
            std_g = (sum((g - mean_g) ** 2 for g in golds_f) / n) ** 0.5
            pearson = cov / (std_p * std_g) if (std_p * std_g) > 0 else 0.0
            result.metrics["mse"]     = mse
            result.metrics["pearson"] = pearson
    except Exception as exc:
        result.error = str(exc)
    return result


# ---------------------------------------------------------------------------
# Orchestrator: run all (or filtered) tasks from registry
# ---------------------------------------------------------------------------

RUNNER_DISPATCH = {
    "mc":         run_multiple_choice_task,
    "freeform":   run_freeform_task,
    "sequence":   run_sequence_task,
    "summarize":  run_summarization_task,
    "regression": run_regression_task,
}


async def run_all_tasks(
    api_endpoint: str,
    sample_sizes: Optional[Dict[str, int]] = None,
    dry_run:      bool = False,
    max_tier:     int  = 1,
    task_filter:  Optional[List[str]] = None,
) -> List[BenchmarkTaskResult]:
    """Run all tasks from TASK_REGISTRY matching tier <= max_tier.

    Args:
        api_endpoint: Base URL of the FAB mesh API (e.g. http://localhost:8000)
        sample_sizes: Override per-task sample counts; defaults to 10 per task
        dry_run:      Print dataset info without making any agent calls
        max_tier:     1 = public only; 2 = include gated datasets (needs HF login)
        task_filter:  Optional list of task names to run; None = all matching tier
    """
    sizes = sample_sizes or {}
    results = []
    tasks_to_run = [
        (name, info)
        for name, info in TASK_REGISTRY.items()
        if info["tier"] <= max_tier and (task_filter is None or name in task_filter)
    ]
    total = len(tasks_to_run)
    for i, (name, info) in enumerate(tasks_to_run, 1):
        n = sizes.get(name, 10)
        runner = RUNNER_DISPATCH[info["type"]]
        print(f"\n[{i}/{total}] {name}  ({info['dataset_id']})  Tier {info['tier']}")
        result = await runner(name, api_endpoint, n_samples=n, dry_run=dry_run)
        if result.error:
            print(f"  ERROR: {result.error}")
        else:
            metrics_str = "  ".join(f"{k}={v:.3f}" for k, v in result.metrics.items())
            print(f"  → {metrics_str}")
        results.append(result)
    return results
