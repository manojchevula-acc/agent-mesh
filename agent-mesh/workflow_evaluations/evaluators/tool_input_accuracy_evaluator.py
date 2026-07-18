"""Tool input accuracy evaluator.

Verifies that tool call inputs were correct:
  - customer_id in query matches customer_id passed to the tool
  - no raw PII was passed as a tool argument
  - (LLM judge) other query parameters (date range, product, entity, filter) were
    correctly threaded into the tool call

Evaluation strategy — two-tier:
  Tier 1 (deterministic): exact customer ID threading check (regex CUST###),
    and PII scan of tool argument text.  Precise for ID-presence/absence.
  Tier 2 (LLM judge): semantic parameter accuracy assessment.  The LLM checks
    whether the tool was called with the RIGHT parameters beyond just customer IDs:
    - Correct date range / fiscal period
    - Correct product or deal type
    - Correct entity or counterparty
    - Correct report type / output format
    - No extra or missing parameters that would distort the result
  The LLM judge adds a supplemental check entry.  The final score is the minimum
  of the deterministic score and the LLM score — a bad parameter choice cannot be
  hidden by a correct customer ID.
  Fallback: if GROQ_API_KEY is absent, the deterministic result is kept unchanged.
"""
from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path
from typing import List, Optional

from evaluators.compliance_evaluator import EvalScore

_EVAL_ROOT = Path(__file__).resolve().parents[1]
if str(_EVAL_ROOT) not in sys.path:
    sys.path.insert(0, str(_EVAL_ROOT))

_CUST_RE = re.compile(r"\bCUST[_-]?\d{3,}\b", re.IGNORECASE)

# ---------------------------------------------------------------------------
# LLM judge infrastructure (same Groq/Cerebras endpoint as task_adherence)
# ---------------------------------------------------------------------------

_DEFAULT_GROQ_MODEL     = "llama-3.3-70b-versatile"
_DEFAULT_CEREBRAS_MODEL = "gemma-4-31b"

_TOOL_INPUT_JUDGE_PROMPT = """\
You are evaluating a banking AI assistant (FAB — First Abu Dhabi Bank) on whether \
it passed the correct parameters to its data-retrieval tools.

ORIGINAL USER QUERY:
{query}

TOOL CALL INPUTS AND OUTPUTS (from audit log):
{audit_summary}

=== Evaluation task ===
Compare the user's request to what was actually passed to the tool(s).

Check for mismatches in:
- Date range / fiscal period / reporting quarter
- Product type (loan, deposit, FX, trade finance, etc.)
- Deal reference or facility ID
- Entity name or counterparty
- Report type or output format
- Any other parameter explicitly stated in the query

Score:
  1.0 = INPUTS_CORRECT
        All parameters explicitly mentioned in the query were correctly passed
        to the tool(s).  Minor reformatting (e.g. "Q1 2025" → date range) is fine.

  0.5 = INPUTS_PARTIALLY_CORRECT
        Most parameters are correct but one non-critical parameter is missing or
        slightly wrong (e.g. wrong quarter but right product, or right customer
        but wrong report format).

  0.0 = INPUTS_WRONG
        A key parameter was wrong or missing — e.g. wrong customer, completely
        wrong date range, wrong product type — in a way that would produce an
        incorrect or irrelevant result.

Return ONLY valid JSON (no markdown fences):
{{
  "score": 1.0,
  "label": "INPUTS_CORRECT|INPUTS_PARTIALLY_CORRECT|INPUTS_WRONG",
  "reason": "one sentence explaining the verdict",
  "mismatch": "what was wrong if score < 1.0, else null"
}}"""


def _call_tool_input_llm_judge(
    query: str,
    audit_records: Optional[List[dict]],
    combined_output: str,
) -> Optional[dict]:
    """Call the LLM judge for tool input accuracy.

    Returns a parsed dict with keys: score, label, reason, mismatch.
    Returns None when GROQ_API_KEY is absent or on any exception.
    """
    api_key = os.getenv("GROQ_API_KEY", "")
    if not api_key:
        return None

    # Build a concise audit summary for the prompt
    audit_lines = []
    if audit_records:
        for i, rec in enumerate(audit_records[:5]):  # cap at 5 records
            agent = rec.get("agent_name", f"agent_{i}")
            inputs = str(rec.get("inputs", ""))[:300]
            output = str(rec.get("output", ""))[:200]
            audit_lines.append(f"[{agent}] inputs={inputs} | output_preview={output}")
    elif combined_output:
        audit_lines.append(f"[combined_output] {combined_output[:400]}")
    else:
        return None  # Nothing to judge without tool context

    audit_summary = "\n".join(audit_lines) or "(no audit data)"

    base_url = os.getenv("LLM_BASE_URL", "https://api.groq.com/openai/v1")
    model = (
        os.getenv("EVAL_JUDGE_MODEL")
        or os.getenv("GROQ_MODEL")
        or (_DEFAULT_CEREBRAS_MODEL if "cerebras" in base_url else _DEFAULT_GROQ_MODEL)
    )
    try:
        from openai import OpenAI
        client = OpenAI(base_url=base_url, api_key=api_key)
        prompt = _TOOL_INPUT_JUDGE_PROMPT.format(
            query=query[:400],
            audit_summary=audit_summary,
        )
        resp = client.chat.completions.create(
            model=model,
            max_tokens=300,
            messages=[{"role": "user", "content": prompt}],
        )
        raw = resp.choices[0].message.content if resp.choices else ""
        start = raw.find("{")
        end = raw.rfind("}") + 1
        if start == -1 or end <= 0:
            return None
        data = json.loads(raw[start:end])
        raw_score = float(data.get("score", 0.5))
        if raw_score >= 0.75:
            data["score"] = 1.0
        elif raw_score >= 0.25:
            data["score"] = 0.5
        else:
            data["score"] = 0.0
        return data
    except Exception:
        return None


def tool_input_accuracy_score(
    query: str,
    agent_outputs: List[str],
    audit_records: Optional[List[dict]] = None,
) -> EvalScore:
    """Score whether tool inputs correctly matched the query intent.

    Tier 1 (deterministic) checks:
    1. customer_id in query appears in agent outputs / audit parameters
    2. no raw PII patterns in tool arguments (delegate to pii_evaluator)

    Tier 2 (LLM judge) check:
    3. other query parameters (date range, product, entity) correctly threaded

    The final score is min(deterministic, llm_score) so a parameter mismatch
    cannot be masked by a correct customer ID.
    """
    query_customers = set(c.upper() for c in _CUST_RE.findall(query))

    if not query_customers:
        combined = " ".join(agent_outputs)
        pii_result = _check_pii_in_tool_args(combined)
        checks = [
            {"name": "Customer IDs in query", "passed": True,
             "detail": "No customer IDs in query — only checking PII in tool arguments"},
            {"name": "No PII detected in tool arguments",
             "passed": pii_result.score == 1.0,
             "detail": "Clean — no PII patterns" if pii_result.score == 1.0
                       else pii_result.detail or "PII detected in tool args"},
        ]
        det_score = pii_result.score
        det_label = pii_result.label
        det_detail = pii_result.detail
    else:
        # Check that expected customer IDs appear in tool outputs / audit
        combined_output = " ".join(agent_outputs)
        combined_audit = " ".join(
            str(r.get("inputs", "")) + str(r.get("output", ""))
            for r in (audit_records or [])
        )
        combined = (combined_output + " " + combined_audit).upper()

        checks = []
        matched = []
        missing = []
        for c in sorted(query_customers):
            found = c in combined
            checks.append({
                "name": f"Customer ID {c} threaded into tool call",
                "passed": found,
                "detail": "Found in tool arguments / audit output" if found
                          else "Missing — ID from query not passed to tool",
            })
            if found:
                matched.append(c)
            else:
                missing.append(c)

        pii_result = _check_pii_in_tool_args(combined_output)
        checks.append({
            "name": "No PII detected in tool arguments",
            "passed": pii_result.score == 1.0,
            "detail": "Clean — no PII patterns in tool args" if pii_result.score == 1.0
                      else pii_result.detail or "PII detected in tool args",
        })

        if missing:
            det_score, det_label, det_detail = (
                0.0, "WRONG_CUSTOMER_ID",
                f"query had {query_customers}, missing in tool call: {missing}",
            )
        elif pii_result.score < 1.0:
            det_score, det_label, det_detail = (
                0.5, "PII_IN_TOOL_ARGS",
                pii_result.detail or "raw PII detected in tool arguments",
            )
        else:
            det_score, det_label, det_detail = (
                1.0, "INPUTS_CORRECT",
                f"customer_ids matched: {matched}",
            )

    # Tier 2: LLM judge for semantic parameter accuracy
    combined_output_for_llm = " ".join(agent_outputs)
    llm_result = _call_tool_input_llm_judge(query, audit_records, combined_output_for_llm)

    if llm_result is None:
        checks.append({
            "name": "LLM parameter accuracy judge verdict",
            "passed": det_score >= 1.0,
            "detail": "JUDGE_UNAVAILABLE — GROQ_API_KEY not set; keeping deterministic result",
        })
        return EvalScore(det_score, det_label, det_detail, checks=checks)

    llm_score   = llm_result.get("score", det_score)
    llm_label   = str(llm_result.get("label", det_label))
    llm_reason  = str(llm_result.get("reason", ""))[:200]
    llm_mismatch = llm_result.get("mismatch") or None

    llm_detail = f"{llm_label} — {llm_reason}"
    if llm_mismatch:
        llm_detail += f" | Mismatch: {llm_mismatch}"

    checks.append({
        "name": "LLM parameter accuracy judge verdict",
        "passed": llm_score >= 1.0,
        "detail": llm_detail,
    })

    # Final score: minimum of deterministic and LLM — a parameter mistake cannot be
    # hidden behind a correctly threaded customer ID.
    if det_score == 0.0:
        final_score, final_label, final_detail = det_score, det_label, det_detail
    elif llm_score == 0.0:
        final_score, final_label, final_detail = 0.0, llm_label, llm_reason or det_detail
    elif det_score == 0.5 or llm_score == 0.5:
        final_score = 0.5
        final_label = llm_label if llm_score <= det_score else det_label
        final_detail = llm_reason or det_detail
    else:
        final_score, final_label, final_detail = 1.0, "INPUTS_CORRECT", det_detail

    return EvalScore(final_score, final_label, final_detail, checks=checks)


def _check_pii_in_tool_args(text: str) -> EvalScore:
    from evaluators.pii_evaluator import pii_not_in_response
    result = pii_not_in_response(text)
    return result
