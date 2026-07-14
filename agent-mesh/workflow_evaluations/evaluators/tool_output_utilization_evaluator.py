"""Tool output utilization evaluator.

Did the agent actually use the tool's output in its final response?
Uses Jaccard token overlap ≥ 0.15 between tool output and final response.
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
        return EvalScore(1.0, "NOT_APPLICABLE", "no tool outputs to check")

    if not final_response or not final_response.strip():
        return EvalScore(0.0, "NO_RESPONSE")

    combined_tool = " ".join(tool_outputs)
    tool_tokens = _tokenize(combined_tool)
    resp_tokens = _tokenize(final_response)

    overlap = _jaccard(tool_tokens, resp_tokens)

    if overlap >= threshold:
        return EvalScore(
            1.0, "OUTPUT_USED", f"Jaccard={overlap:.3f} >= {threshold}"
        )
    elif overlap >= threshold / 2:
        return EvalScore(
            0.5, "OUTPUT_WEAKLY_USED", f"Jaccard={overlap:.3f} (below {threshold})"
        )
    else:
        return EvalScore(
            0.0, "OUTPUT_NOT_USED", f"Jaccard={overlap:.3f} (threshold={threshold})"
        )
