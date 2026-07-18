"""LLM-as-judge evaluation suite for FAB AgentMesh.

Two batched suite calls cover five evaluation dimensions in 2 API calls per case:

  Suite 1 — Response Quality  (query + response only, no context needed)
    • task_adherence      — does the response directly address the banking query?
    • completeness        — are all required dimensions of the query answered?
    • tool_appropriateness— was the correct DataAgent tool selected for the intent?

  Suite 2 — RAG Grounding  (response + retrieved context chunks)
    • rag_faithfulness    — are all factual claims supported by the context? (RAGAS-inspired)
    • citation_accuracy   — do cited claims match what the source document actually says?

  Suite 3 — Data Accuracy  (deterministic first; LLM only on detected mismatch)
    • data_accuracy       — do all numbers in the final response match DataAgent tool output?

Compared to one-call-per-evaluator, the suite approach:
  - Saves 1 API call on data routes (suite replaces separate task_adherence call)
  - Adds 1 RAG suite call on knowledge/hybrid routes (faithfulness + citation in 1 call)
  - Adds 0–1 data accuracy calls (deterministic pre-filter eliminates most LLM calls)

All suites use the same Groq/Cerebras OpenAI-compatible client (GROQ_API_KEY / LLM_BASE_URL /
GROQ_MODEL).  Each suite falls back gracefully — on exception the caller gets None and should
fall back to the existing deterministic evaluators.
"""
from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path
from typing import Dict, List, Optional, Tuple

_EVAL_ROOT = Path(__file__).resolve().parents[1]
if str(_EVAL_ROOT) not in sys.path:
    sys.path.insert(0, str(_EVAL_ROOT))

from evaluators.compliance_evaluator import EvalScore

_DEFAULT_GROQ_MODEL     = "llama-3.3-70b-versatile"
_DEFAULT_CEREBRAS_MODEL = "gemma-4-31b"


def _get_client_and_model(model: Optional[str] = None):
    from openai import OpenAI
    base_url = os.getenv("LLM_BASE_URL", "https://api.groq.com/openai/v1")
    api_key  = os.getenv("GROQ_API_KEY", "")
    if not api_key:
        raise RuntimeError("GROQ_API_KEY not set")
    if model is None:
        # EVAL_JUDGE_MODEL takes priority — decouples eval judge from live agent model
        model = (
            os.getenv("EVAL_JUDGE_MODEL")
            or os.getenv("GROQ_MODEL")
            or (_DEFAULT_CEREBRAS_MODEL if "cerebras" in base_url else _DEFAULT_GROQ_MODEL)
        )
    return OpenAI(base_url=base_url, api_key=api_key), model


def _llm_call(prompt: str, model: Optional[str] = None, max_tokens: int = 600) -> str:
    """Single LLM call with exponential-backoff retry on rate-limit (429) errors."""
    import time
    client, mdl = _get_client_and_model(model)
    last_exc: Exception = RuntimeError("no attempts made")
    for attempt in range(4):  # up to 4 attempts: 0s, 10s, 20s, 40s backoff
        try:
            resp = client.chat.completions.create(
                model=mdl,
                max_tokens=max_tokens,
                messages=[{"role": "user", "content": prompt}],
            )
            return resp.choices[0].message.content if resp.choices else ""
        except Exception as exc:
            last_exc = exc
            # Only retry on HTTP 429 (rate limit); propagate all other errors immediately
            err_str = str(exc).lower()
            if "429" not in err_str and "rate limit" not in err_str:
                raise
            wait = 10 * (2 ** attempt)   # 10s, 20s, 40s
            time.sleep(wait)
    raise last_exc


def _parse_json(raw: str) -> dict:
    """Extract and parse the first JSON object in raw.  If the object is
    truncated (common when max_tokens is tight or the model is a reasoning
    model that burns tokens on internal CoT), try to close open brackets
    so json.loads can recover whatever top-level keys were completed.
    """
    start = raw.find("{")
    if start == -1:
        raise ValueError(f"No JSON object in: {raw[:80]}")
    # Try the full substring first
    end = raw.rfind("}") + 1
    if end > start:
        try:
            return json.loads(raw[start:end])
        except json.JSONDecodeError:
            pass
    # Fallback: close any open braces/brackets and retry
    fragment = raw[start:]
    open_braces   = fragment.count("{") - fragment.count("}")
    open_brackets = fragment.count("[") - fragment.count("]")
    repaired = fragment + "]" * max(open_brackets, 0) + "}" * max(open_braces, 0)
    try:
        return json.loads(repaired)
    except json.JSONDecodeError:
        pass
    # Last resort: extract only complete top-level key:value pairs via regex
    partial: dict = {}
    for m in re.finditer(r'"(\w+)"\s*:\s*(-?\d+(?:\.\d+)?|true|false|null|"[^"]*")', fragment):
        key, val_str = m.group(1), m.group(2)
        try:
            partial[key] = json.loads(val_str)
        except json.JSONDecodeError:
            pass
    if partial:
        return partial
    raise ValueError(f"Could not parse JSON from: {raw[:120]}")


# ---------------------------------------------------------------------------
# Suite 1: Response Quality  (task adherence + completeness + tool appropriateness)
# ---------------------------------------------------------------------------

_TOOL_DESCRIPTIONS_SHORT = """\
customer_360: 360° customer profile; pricing_recommendation: recommended price/margin/compliance flag; \
margin_analysis: deal margin vs treasury benchmark; profitability_summary: revenue/costs/net profit; \
rwa_impact: RWA/Basel III capital; new_customer_pricing: pricing for prospects; \
competitor_price_analysis: FAB vs competitor rates; pricing_trace: step-by-step price breakdown; \
segment_pricing_benchmark: pricing floors/ceilings by segment; operations_cost_impact: cost margin; \
relationship_discount: discount eligibility; win_loss_insights: win/loss count and win rate; \
policy_exception: per-deal policy breaches; non_compliant_deals: deals below pricing floor; \
cross_sell_opportunity: cross-sell recommendations; credit_rating_events: rating migrations; \
similar_customer_pricing: reference similar customers; treasury_rate_sheet: EIBOR/funding rates; \
pricing_policy: internal policy floors/ceilings; historical_deals: historical deal records"""

_RESPONSE_QUALITY_PROMPT = """\
You are evaluating a banking AI assistant on three dimensions in one pass.

USER QUERY: {query}

AGENT RESPONSE (may be abbreviated for evaluation — do NOT penalise if the text \
appears cut off; judge based on the content that is shown):
{response}

TOOL USED BY DATA AGENT (or "N/A"): {tool_used}

=== Dimension 1: Task Adherence ===
Does the response directly and usefully address the banking query?

Overall score:
  1.0 = ADHERENT   — Fully on-topic and complete.
  0.5 = PARTIAL    — Partially on-topic or hedged / incomplete.
  0.0 = OFF_TOPIC  — Off-topic, refused without cause, or mirrors the query verbatim.

Also evaluate each of these four criteria independently:

  criterion_query_answered: Did the response directly address what was asked \
(not deflect, not just echo the question back)?
  criterion_content_present: Does the response contain the expected domain content \
(data figures for data queries; policy explanation+citation for knowledge; both for hybrid)?
  criterion_not_error_or_refusal: Is this a real answer — NOT a generic error message \
("I was unable to retrieve"), NOT an unexplained refusal, NOT empty?
  criterion_response_complete: Does the response appear complete — not abruptly ending \
mid-thought? Note: the response text may be abbreviated for this evaluation; \
only flag as incomplete if the response itself (not the evaluation window) is clearly truncated.

=== Dimension 2: Response Completeness ===
Identify the dimensions the query requires (from: entity_identified, correct_metric, \
specific_value_given, policy_context, actionable_recommendation, time_period_specified, \
comparison_provided, clarification_given).
Score = (ADDRESSED + 0.5*PARTIAL) / required_count.

=== Dimension 3: Tool Appropriateness ===
Available tools: {tool_descriptions}
Was the TOOL USED the most appropriate choice for this query?
  1.0 = Correct — directly answers the intent.
  0.5 = Acceptable but suboptimal.
  0.0 = Wrong — a clearly better tool was available and unused.
Set "applies": false when TOOL USED is "N/A".

Return ONLY valid JSON (no markdown fences):
{{
  "task_adherence": {{
    "score": 0.0,
    "label": "ADHERENT|PARTIAL|OFF_TOPIC",
    "reason": "one sentence",
    "criteria": {{
      "criterion_query_answered":      {{"passed": true,  "detail": "one sentence"}},
      "criterion_content_present":     {{"passed": true,  "detail": "one sentence"}},
      "criterion_not_error_or_refusal":{{"passed": true,  "detail": "one sentence"}},
      "criterion_response_complete":   {{"passed": true,  "detail": "one sentence"}}
    }},
    "metrics_used": "comma-separated list of signals the judge used (e.g. content relevance, query alignment, completeness)"
  }},
  "completeness": {{
    "required_dimensions": ["entity_identified"],
    "dimension_scores": {{}},
    "score": 0.0,
    "missing_dimensions": []
  }},
  "tool_appropriateness": {{
    "applies": true,
    "score": 0.0,
    "label": "APPROPRIATE|SUBOPTIMAL|WRONG_TOOL",
    "reason": "one sentence",
    "better_tool": null
  }}
}}"""


class ResponseQualitySuiteResult:
    """Holds the three EvalScores from Suite 1."""
    __slots__ = ("task_adherence", "completeness", "tool_appropriateness", "raw")

    def __init__(
        self,
        task_adherence: EvalScore,
        completeness: EvalScore,
        tool_appropriateness: Optional[EvalScore],
        raw: dict,
    ):
        self.task_adherence     = task_adherence
        self.completeness       = completeness
        self.tool_appropriateness = tool_appropriateness
        self.raw                = raw


def run_response_quality_suite(
    query: str,
    response: str,
    tool_used: Optional[str] = None,
    model: Optional[str] = None,
) -> Optional[ResponseQualitySuiteResult]:
    """Single LLM call returning task_adherence + completeness + tool_appropriateness.

    Returns None on any error (caller should fall back to deterministic evaluators).
    """
    if not query or not response:
        return None

    try:
        # Truncate at a word boundary to avoid cutting mid-word (which causes the
        # LLM to think the response itself is truncated rather than the eval window).
        _RESP_LIMIT = 2500
        resp_for_prompt = response if len(response) <= _RESP_LIMIT else response[:_RESP_LIMIT].rsplit(" ", 1)[0] + " …"

        prompt = _RESPONSE_QUALITY_PROMPT.format(
            query=query[:400],
            response=resp_for_prompt,
            tool_used=tool_used or "N/A",
            tool_descriptions=_TOOL_DESCRIPTIONS_SHORT,
        )
        raw  = _llm_call(prompt, model=model, max_tokens=1200)
        data = _parse_json(raw)

        # --- task_adherence ---
        ta_raw      = data.get("task_adherence", {})
        ta_score    = float(ta_raw.get("score", 0.5))
        ta_label    = str(ta_raw.get("label", "PARTIAL"))
        ta_reason   = str(ta_raw.get("reason", ""))[:200]
        ta_metrics  = str(ta_raw.get("metrics_used", ""))[:150]
        ta_criteria = ta_raw.get("criteria", {})

        # Build checks from structured criteria — each criterion becomes one check line,
        # giving a clear breakdown of why the judge scored the way it did.
        _CRITERION_LABELS = {
            "criterion_query_answered":       "Query directly answered",
            "criterion_content_present":      "Expected domain content present",
            "criterion_not_error_or_refusal": "Response is not an error or refusal",
            "criterion_response_complete":    "Response is complete (not truncated mid-thought)",
        }
        ta_checks = []
        for key, display_name in _CRITERION_LABELS.items():
            crit = ta_criteria.get(key, {})
            ta_checks.append({
                "name": display_name,
                "passed": bool(crit.get("passed", ta_score >= 0.75)),
                "detail": str(crit.get("detail", ""))[:200] or "(no detail)",
            })
        ta_checks.append({
            "name": "Judge overall verdict",
            "passed": ta_score >= 0.75,
            "detail": f"{ta_label} — {ta_reason}"
                      + (f" | Metrics: {ta_metrics}" if ta_metrics else ""),
        })
        task_adherence = EvalScore(ta_score, ta_label, ta_reason, checks=ta_checks)

        # --- completeness ---
        co_raw      = data.get("completeness", {})
        co_score    = float(co_raw.get("score", 0.5))
        co_required = co_raw.get("required_dimensions", [])
        co_missing  = co_raw.get("missing_dimensions", [])
        if co_score >= 0.85:
            co_label = "COMPLETE"
        elif co_score >= 0.50:
            co_label = "PARTIALLY_COMPLETE"
        else:
            co_label = "INCOMPLETE"
        co_detail = (
            f"All {len(co_required)} required dimensions addressed" if not co_missing
            else f"Missing: {co_missing}"
        )
        co_checks = [
            {"name": f"Dimension: {dim}", "passed": dim not in co_missing,
             "detail": "Addressed" if dim not in co_missing else "Missing"}
            for dim in co_required
        ] + [
            {"name": "Overall completeness", "passed": co_score >= 0.70, "detail": co_detail},
        ]
        completeness = EvalScore(
            1.0 if co_score >= 0.85 else (0.5 if co_score >= 0.50 else 0.0),
            co_label, co_detail, checks=co_checks,
        )

        # --- tool_appropriateness ---
        tool_appropriateness: Optional[EvalScore] = None
        ta2_raw = data.get("tool_appropriateness", {})
        if ta2_raw.get("applies", False):
            ta2_score = float(ta2_raw.get("score", 0.5))
            ta2_label = str(ta2_raw.get("label", "APPROPRIATE"))
            ta2_reason= str(ta2_raw.get("reason", ""))[:200]
            ta2_better= ta2_raw.get("better_tool") or None
            mapped_score = 1.0 if ta2_score >= 0.75 else (0.5 if ta2_score >= 0.25 else 0.0)
            ta2_checks = [
                {"name": f"Tool '{tool_used}' appropriate for query",
                 "passed": mapped_score >= 0.5, "detail": ta2_reason},
            ]
            if ta2_better and mapped_score < 1.0:
                ta2_checks.append({"name": "Better tool suggested", "passed": False,
                                    "detail": f"LLM suggests: {ta2_better}"})
            tool_appropriateness = EvalScore(mapped_score, ta2_label, ta2_reason, checks=ta2_checks)

        return ResponseQualitySuiteResult(task_adherence, completeness, tool_appropriateness, data)

    except Exception:
        return None


# ---------------------------------------------------------------------------
# Suite 2: RAG Grounding  (faithfulness + citation accuracy)
# ---------------------------------------------------------------------------

_RAG_GROUNDING_PROMPT = """\
You are evaluating a banking AI RAG response on two dimensions.

AGENT RESPONSE: {response}

SOURCE CONTEXT (the only information the agent had):
{context}

=== Dimension 1: Faithfulness ===
Break the response into atomic factual claims (numbers, rates, policy rules, verdicts, names).
Limit to the 8 most important claims.
For each, decide:
  SUPPORTED   — context explicitly states or clearly implies this (paraphrases count).
  PARTIAL     — context mentions the topic but the specific value/qualifier differs.
  UNSUPPORTED — claim cannot be verified from context at all.
faithfulness_score = (SUPPORTED + 0.5*PARTIAL) / total_claims.

=== Dimension 2: Citation Accuracy ===
Find sentences containing explicit citations (e.g. "[Source: …]", "According to FAB …", "per CBUAE …").
For each cited claim, check whether the cited source content (in the context above) supports the
specific claim — especially numerical values and policy thresholds.
  ACCURATE      — context supports the cited claim as stated.
  CONTRADICTED  — context states a different value or rule.
  UNVERIFIABLE  — citation present but context doesn't cover this specific claim.
If no citations found, set "citations_found": [] and "citation_score": 1.0 (not applicable).

Return ONLY valid JSON (no markdown fences):
{{
  "faithfulness": {{
    "claims": [
      {{"claim": "text", "verdict": "SUPPORTED|PARTIAL|UNSUPPORTED"}}
    ],
    "faithfulness_score": 0.0,
    "unsupported_claims": []
  }},
  "citation_accuracy": {{
    "citations_found": [
      {{"sentence": "text", "verdict": "ACCURATE|CONTRADICTED|UNVERIFIABLE"}}
    ],
    "citation_score": 1.0,
    "contradictions": []
  }}
}}"""


class RAGGroundingSuiteResult:
    """Holds the two EvalScores from Suite 2."""
    __slots__ = ("faithfulness", "citation_accuracy", "raw")

    def __init__(self, faithfulness: EvalScore, citation_accuracy: EvalScore, raw: dict):
        self.faithfulness    = faithfulness
        self.citation_accuracy = citation_accuracy
        self.raw             = raw


# Phrases that identify a generic agent fallback/error response.
# When the answer is an error message, faithfulness scoring is not meaningful:
#   • The LLM finds 0 factual claims → score defaults to 0.5 ("All 0 claims grounded")
#   • The confusing "All 0 claims grounded" detail misleads reviewers
# Returning None here causes run_maf_eval.py to skip the suite entirely so the
# rag_faithfulness key is absent from scores — the case is not penalised for this.
_AGENT_ERROR_RESPONSE_MARKERS = (
    "i was unable to retrieve",
    "unable to retrieve the required data",
    "please try again",
    "contact your relationship manager",
    "an error occurred",
    "could not retrieve",
    "failed to retrieve",
    "service is currently unavailable",
    "i'm unable to retrieve",
    "i am unable to retrieve",
)


def run_rag_grounding_suite(
    response: str,
    context_chunks: List[str],
    model: Optional[str] = None,
) -> Optional[RAGGroundingSuiteResult]:
    """Single LLM call returning rag_faithfulness + citation_accuracy.

    Returns None on any error (caller should keep Jaccard score as-is).
    Also returns None when the agent response is a generic error/fallback message —
    in that case faithfulness is not meaningful (0 claims → "All 0 claims grounded"
    confusion) and the Tool Call Success evaluator already captures the failure.
    """
    if not response or not context_chunks:
        return None

    # Skip faithfulness for generic error/fallback responses.
    # B1 exhibits this: RAGAgent retrieved real chunks but the pipeline returned
    # "I was unable to retrieve the required data." — 0 factual claims are found,
    # the LLM returns faithfulness_score=0.5, and the detail reads confusingly as
    # "All 0 claims grounded".  Skipping the suite makes rag_faithfulness absent
    # from scores so the case verdict is not affected.
    if any(m in response.lower() for m in _AGENT_ERROR_RESPONSE_MARKERS):
        return None

    context_text = "\n\n---\n\n".join(c for c in context_chunks if c)[:1500]
    if not context_text.strip():
        return None

    try:
        prompt = _RAG_GROUNDING_PROMPT.format(
            response=response[:800],
            context=context_text,
        )
        raw  = _llm_call(prompt, model=model, max_tokens=1000)
        data = _parse_json(raw)

        # --- faithfulness ---
        faith_raw   = data.get("faithfulness", {})
        faith_score = float(faith_raw.get("faithfulness_score", 0.5))
        claims      = faith_raw.get("claims", [])
        unsupported = faith_raw.get("unsupported_claims", [])
        if faith_score >= 0.85:
            f_label = "FAITHFUL"
            f_score = 1.0
        elif faith_score >= 0.50:
            f_label = "PARTIALLY_FAITHFUL"
            f_score = 0.5
        else:
            f_label = "UNFAITHFUL"
            f_score = 0.0
        if len(claims) == 0:
            f_detail = "No factual claims identified — faithfulness not applicable (response contains no verifiable assertions)"
        elif not unsupported:
            f_detail = f"All {len(claims)} claims grounded"
        else:
            f_detail = f"{len(unsupported)} unsupported claim(s): {unsupported[:2]}"
        f_checks = [
            {"name": f"Claim: \"{c.get('claim', '')[:70]}\"",
             "passed": c.get("verdict") != "UNSUPPORTED",
             "detail": c.get("verdict", "SUPPORTED")}
            for c in claims[:8]
        ] + [
            {"name": f"Faithfulness score: {faith_score:.2f}", "passed": faith_score >= 0.70,
             "detail": f"Threshold ≥0.85→FAITHFUL, ≥0.50→PARTIAL, else UNFAITHFUL"},
        ]
        faithfulness = EvalScore(f_score, f_label, f_detail, checks=f_checks)

        # --- citation accuracy ---
        cit_raw   = data.get("citation_accuracy", {})
        cit_score = float(cit_raw.get("citation_score", 1.0))
        cit_found = cit_raw.get("citations_found", [])
        contradictions = cit_raw.get("contradictions", [])
        if not cit_found:
            cit_label  = "NO_CITATIONS_TO_VERIFY"
            cit_mapped = 1.0   # not penalised — presence check already handled by citation_present_and_valid
            cit_detail = "No explicit citations found — accuracy check not applicable"
        elif cit_score >= 0.80:
            cit_label  = "CITATION_ACCURATE"
            cit_mapped = 1.0
            cit_detail = f"All {len(cit_found)} citation(s) accurate"
        elif cit_score >= 0.40:
            cit_label  = "CITATION_UNVERIFIABLE"
            cit_mapped = 0.5
            cit_detail = f"{len(cit_found)} citation(s) — some unverifiable"
        else:
            cit_label  = "CITATION_INACCURATE"
            cit_mapped = 0.0
            cit_detail = f"Contradicted claim(s): {contradictions[:2]}"
        cit_checks = [
            {"name": f"Citation: \"{c.get('sentence', '')[:70]}\"",
             "passed": c.get("verdict") != "CONTRADICTED",
             "detail": c.get("verdict", "ACCURATE")}
            for c in cit_found[:5]
        ]
        if contradictions:
            cit_checks.append({"name": "Contradictions detected", "passed": False,
                                "detail": str(contradictions[:2])})
        citation_accuracy = EvalScore(cit_mapped, cit_label, cit_detail, checks=cit_checks)

        return RAGGroundingSuiteResult(faithfulness, citation_accuracy, data)

    except Exception:
        return None


# ---------------------------------------------------------------------------
# Suite 3: Data Accuracy  (deterministic pre-filter + LLM on mismatch)
# ---------------------------------------------------------------------------

# Number-aware extractor: captures values like "4.5%", "AED 10,000", "150bps", "4.50"
_NUM_WITH_UNIT_RE = re.compile(
    r"(?:\b\d{1,3}(?:,\d{3})*(?:\.\d+)?|\b\d+(?:\.\d+)?)"
    r"(?:\s*%|\s*(?:AED|USD|EUR|bps|bp))?",
    re.IGNORECASE,
)


def _extract_numbers(text: str) -> List[float]:
    """Extract all numeric values from text (strips commas and unit suffixes)."""
    nums: List[float] = []
    for m in _NUM_WITH_UNIT_RE.finditer(text):
        raw = re.sub(r"[,%a-zA-Z\s]", "", m.group())
        try:
            nums.append(float(raw))
        except ValueError:
            pass
    return nums


def _numbers_match(a: float, b: float, tol: float = 0.015) -> bool:
    """True when a ≈ b within relative tolerance (default 1.5%)."""
    if b == 0:
        return abs(a) < tol
    return abs(a - b) / abs(b) <= tol


_DATA_ACCURACY_PROMPT = """\
A banking AI synthesized a final response from data tool output.
Check whether specific numbers in the response are accurate.

DATA TOOL OUTPUT:
{tool_output}

FINAL RESPONSE:
{response}

Flagged number(s) in the response that may not match the tool output:
{flagged}

For each flagged number, decide:
  - Is this a legitimate rounding or unit conversion? (4.50 → 4.5, bps → %) → accurate
  - Is it the same value under a different label? → label issue, not data error
  - Is it a completely different value not traceable to tool output? → data error

Return ONLY valid JSON (no markdown fences):
{{
  "results": [
    {{
      "value_in_response": "...",
      "verdict": "ACCURATE|ROUNDING|DATA_ERROR",
      "tool_value": "...",
      "explanation": "one sentence"
    }}
  ],
  "overall_score": 1.0
}}
overall_score: 1.0=all accurate, 0.5=only rounding/unit differences, 0.0=genuine data errors."""


def data_accuracy_score(
    tool_outputs: List[str],
    response: str,
    model: Optional[str] = None,
    numeric_tolerance: float = 0.015,
) -> EvalScore:
    """Numerical consistency between DataAgent tool output and final response.

    Step 1 (deterministic): extract numbers from tool output and response.
    Step 2 (deterministic): flag response numbers absent from tool output within tolerance.
    Step 3 (LLM): called ONLY when mismatches found — verifies whether discrepancy is
                  rounding/unit or genuine data error.

    Returns EvalScore with score 1.0/0.5/0.0 and per-number checks.
    """
    if not tool_outputs or not response:
        return EvalScore(1.0, "DATA_ACCURACY_SKIP", "No tool output or response", checks=[
            {"name": "Tool output available", "passed": False,
             "detail": "Cannot check data accuracy without tool output"},
        ])

    tool_text     = " ".join(tool_outputs)
    tool_numbers  = _extract_numbers(tool_text)
    resp_numbers  = _extract_numbers(response)

    if not resp_numbers:
        return EvalScore(1.0, "NO_NUMBERS", "No numeric figures in response — check not applicable", checks=[
            {"name": "Numeric figures present", "passed": False,
             "detail": "No numbers found in response — data accuracy check skipped"},
        ])

    # Deterministic check: flag response numbers not traceable to tool output
    flagged = []
    for rv in resp_numbers:
        if not any(_numbers_match(rv, tv, numeric_tolerance) for tv in tool_numbers):
            flagged.append(rv)

    if not flagged:
        return EvalScore(1.0, "NUMERICALLY_CONSISTENT",
                         f"All {len(resp_numbers)} figure(s) traceable to tool output",
                         checks=[
                             {"name": "All response figures traceable to tool output",
                              "passed": True,
                              "detail": f"{len(resp_numbers)} figure(s) checked — all match within {numeric_tolerance*100:.1f}% tolerance"},
                         ])

    # LLM call only on detected mismatch
    try:
        flagged_str = ", ".join(str(v) for v in flagged[:5])
        prompt = _DATA_ACCURACY_PROMPT.format(
            tool_output=tool_text[:1500],
            response=response[:900],
            flagged=flagged_str,
        )
        raw  = _llm_call(prompt, model=model, max_tokens=700)
        data = _parse_json(raw)

        overall    = float(data.get("overall_score", 0.5))
        results    = data.get("results", [])
        if overall >= 0.75:
            score, label = 1.0, "NUMERICALLY_CONSISTENT"
        elif overall >= 0.25:
            score, label = 0.5, "MINOR_DISCREPANCY"
        else:
            score, label = 0.0, "DATA_INACCURATE"

        checks = []
        errors = 0
        for r in results[:5]:
            is_err = r.get("verdict") == "DATA_ERROR"
            if is_err:
                errors += 1
            checks.append({
                "name": f"Figure: '{r.get('value_in_response', '?')}'",
                "passed": not is_err,
                "detail": f"{r.get('verdict', '?')} — {r.get('explanation', '')}",
            })
        detail = (
            f"All figures match tool output (only rounding differences)" if label == "NUMERICALLY_CONSISTENT"
            else f"{errors} data error(s) in {len(results)} flagged figure(s)"
        )
        return EvalScore(score, label, detail, checks=checks)

    except RuntimeError:
        return EvalScore(0.5, "DATA_ACCURACY_SKIP", "Judge unavailable", checks=[
            {"name": "LLM judge available", "passed": False,
             "detail": "GROQ_API_KEY not set — data accuracy LLM step skipped"},
        ])
    except Exception as exc:
        # Deterministic result: flagged but could not verify — partial score
        return EvalScore(0.5, "DATA_ACCURACY_UNVERIFIED",
                         f"Flagged {len(flagged)} figure(s) unverified: {flagged[:3]}",
                         checks=[
                             {"name": "Numerical mismatch check", "passed": False,
                              "detail": f"{len(flagged)} figure(s) in response not found in tool output: {flagged[:3]}"},
                             {"name": "LLM verification", "passed": False,
                              "detail": f"Judge error: {str(exc)[:80]}"},
                         ])
