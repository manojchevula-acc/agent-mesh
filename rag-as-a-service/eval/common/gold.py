"""Loader for tests/fixtures/gold_qa.json — the canonical gold set.

Canonical was VERIFIED, not assumed: every code reference in the repo
(`scripts/eval_llm_judge.py:55`, `scripts/eval_gold_qa.py:46`) and every
mention in EVAL_SUITE_PLAN.md points here. `gold_qa.remapped.json` is
referenced by nothing. The two files carry identical questions and answers and
differ only in `expected_sources`: this one targets by `section_heading` +
`pipeline_verified`, the remapped one by `clause_reference`.

What is deliberately NOT used as a retrieval target
---------------------------------------------------
Neither `section_heading` nor `clause_reference`, measured against the live
index:

    section_heading   16 distinct values over 117 chunks;
                      'FIRST ABU DHABI BANK' alone covers 39 (33%)
    clause_reference  33 distinct, and many are synthetic ('figure_p3')

Both collide far too heavily to identify a specific chunk, so grading on them
would measure the metadata, not the retrieval. `tests/fixtures/pipeline_suite.yaml`
reached the same conclusion in its header comment, and `scripts/eval_gold_qa.py`
already grades on **document + fact containment** instead. This module keeps
that contract.
"""

import json
import re
from dataclasses import dataclass, field
from pathlib import Path

GOLD_PATH = Path("tests/fixtures/gold_qa.json")

# The facts worth grading in this corpus: money, percentages, bps, dates,
# article/tier refs. Ported from scripts/eval_gold_qa.py so the new suite and
# the old script agree on what "the chunk contained the answer" means.
_FACT_RE = re.compile(
    r"\b(?:AED\s?[\d.,]+\s?(?:billion|bn|million|m)?"
    r"|\d+(?:\.\d+)?%"
    r"|\d+\s?bps"
    r"|\d{1,2}\s+\w+\s+\d{4}"
    r"|\d{2}-\w{3}-\d{4}"
    r"|Article\s+\d+(?:\.\d+)*"
    r"|Tier\s+\d)\b",
    re.IGNORECASE,
)


@dataclass
class GoldCase:
    id: str
    name: str
    question: str
    expected_answer: str
    expected_documents: list[str]
    expected_content_types: list[str]
    modalities: list[str]
    pipeline_verified: bool
    answerable: bool = True
    note: str = ""
    facts: list[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        if not self.facts:
            self.facts = sorted({m.group(0) for m in _FACT_RE.finditer(self.expected_answer)})


def load_gold(path: Path = GOLD_PATH) -> list[GoldCase]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    cases = []
    for entry in raw:
        sources = entry.get("expected_sources") or []
        # `answerable` is implicit: a case with no expected sources and no gold
        # answer is an out-of-corpus probe.
        answerable = entry.get("answerable", bool(entry.get("expected_answer")))
        cases.append(
            GoldCase(
                id=str(entry["id"]),
                name=entry.get("name", ""),
                question=entry["question"],
                expected_answer=entry.get("expected_answer") or "",
                expected_documents=[s["document"] for s in sources if s.get("document")],
                expected_content_types=[
                    s["content_type"] for s in sources if s.get("content_type")
                ],
                modalities=[s.get("modality", "") for s in sources],
                # Marks a case whose expected_sources were confirmed reachable
                # by this pipeline. Unverified cases are still scored, but they
                # are reported separately — a failure there may be bad ground
                # truth rather than bad retrieval.
                pipeline_verified=any(s.get("pipeline_verified") for s in sources),
                answerable=answerable,
                note=entry.get("note", ""),
            )
        )
    return cases


def fact_coverage(facts: list[str], text: str) -> tuple[int, list[str]]:
    """How many of a case's gold facts appear in the retrieved text."""
    if not facts:
        return 0, []
    haystack = re.sub(r"\s+", " ", text).lower()
    missing = [f for f in facts if re.sub(r"\s+", " ", f).lower() not in haystack]
    return len(facts) - len(missing), missing
