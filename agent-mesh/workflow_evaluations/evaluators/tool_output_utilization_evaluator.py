"""Tool output utilization evaluator.

Did the agent actually use the tool's output in its final response?
Uses Jaccard token overlap >= 0.15 between tool output and final response.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path
from typing import List

from evaluators.compliance_evaluator import EvalScore

_EVAL_ROOT = Path(__file__).resolve().parents[1]
if str(_EVAL_ROOT) not in sys.path:
    sys.path.insert(0, str(_EVAL_ROOT))

_STOP_WORDS = frozenset(
    "the a an and or but in on at to for of with is are was were be been "
    "have has had do does did will would could should may might shall".split()
)


def _tokenize(text: str) -> set[str]:
    tokens = re.findall(r"[a-zA-Z0-9]+(?:\.\d+)?", text.lower())
    return {t for t in tokens if t not in _STOP_WORDS and len(t) > 1}


def _jaccard(a: set[str], b: set[str]) -> float:
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def tool_output_utilization_score(
    tool_outputs: List[str],
    final_response: str,
    threshold: float = 0.15,
) -> EvalScore:
    """Score whether tool outputs were used in the final response.

    tool_outputs: list of raw output strings from DataAgent / RAGAgent hops.
    final_response: the answer returned to the user.
    """
    if not tool_outputs:
        return EvalScore(1.0, "NOT_APPLICABLE", "no tool outputs to check", checks=[
            {"name": "Tool outputs provided", "passed": False,
             "detail": "No tool outputs — evaluation not applicable"},
        ])

    if not final_response or not final_response.strip():
        return EvalScore(0.0, "NO_RESPONSE", checks=[
            {"name": "Tool outputs provided", "passed": True,
             "detail": f"{len(tool_outputs)} tool output(s) available"},
            {"name": "Final response is non-empty", "passed": False, "detail": "Empty response"},
            {"name": "Tool output reflected in final response", "passed": False,
             "detail": "Cannot measure utilization — no response"},
        ])

    combined_tool = " ".join(tool_outputs)
    tool_tokens = _tokenize(combined_tool)
    resp_tokens = _tokenize(final_response)

    overlap = _jaccard(tool_tokens, resp_tokens)
    used = overlap >= threshold
    weakly_used = overlap >= threshold / 2

    checks = [
        {"name": "Tool outputs provided",
         "passed": True,
         "detail": f"{len(tool_outputs)} output(s)"},
        {"name": f"Jaccard token overlap: {overlap:.3f}",
         "passed": used,
         "detail": (f"Overlap={overlap:.3f} ≥ {threshold} → OUTPUT_USED" if used
                    else f"Overlap={overlap:.3f} — threshold ≥{threshold} (OUTPUT_USED), ≥{threshold/2:.3f} (WEAKLY_USED)")},
        {"name": "Tool output reflected in final response",
         "passed": used,
         "detail": ("OUTPUT_USED" if used else "OUTPUT_WEAKLY_USED" if weakly_used else "OUTPUT_NOT_USED")},
    ]

    if used:
        return EvalScore(1.0, "OUTPUT_USED", f"Jaccard={overlap:.3f} >= {threshold}", checks=checks)
    elif weakly_used:
        return EvalScore(0.5, "OUTPUT_WEAKLY_USED", f"Jaccard={overlap:.3f} (below {threshold})", checks=checks)
    else:
        return EvalScore(0.0, "OUTPUT_NOT_USED", f"Jaccard={overlap:.3f} (threshold={threshold})", checks=checks)
