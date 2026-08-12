"""Partition an answer into atomic spans and label each subjective or objective.

This is RAG-Check §III-A. The paper's finding that matters here: a custom
rule-based categoriser matched human subjective/objective labelling BETTER than
asking GPT-3.5 to do it. So this is deliberately rules, not a model call —
cheaper, deterministic, reviewable, and no judge-independence question.

Why it matters for scoring. Today eval_llm_judge.py gives one 0/1/2 for a whole
answer, so a reply with four correct clauses and one wrong one collapses to "1"
and you cannot see which clause failed (eval_runs.md case 4 is exactly this).
Splitting first means correctness is measured per statement.

The subjective/objective split is not decoration. Scoring "This is likely the
applicable floor" as correct-or-incorrect is a category error — it is a hedge,
not a claim, and marking it either way corrupts the metric. Subjective spans
are excluded from correctness and reported as their own ratio, because a
generator that hedges everything scores suspiciously well otherwise.
"""

import re
from dataclasses import dataclass

# ── Subjectivity markers, grouped exactly as the paper lists them ────────
_MODAL_VERBS = r"\b(?:could|might|may|would|should|ought to)\b"
_OPINION = r"\b(?:believe|feel|think|suspect|assume|expect|hope|prefer)\b"
_HEDGING = (
    r"(?:\bit seems\b|\bit appears\b|\bappears to\b|\bseems to\b|\blikely\b"
    r"|\bunlikely\b|\bprobably\b|\bpossibly\b|\bperhaps\b|\barguably\b"
    r"|\bpresumably\b|\broughly\b|\bapproximately\b|\bmore or less\b)"
)
_UNCERTAIN_QUANTIFIER = r"\b(?:some|many|several|few|most|various|numerous|a number of)\b"
_FREQUENCY_DEGREE = (
    r"\b(?:often|usually|typically|generally|frequently|rarely|seldom"
    r"|occasionally|sometimes|normally|commonly|largely|mostly)\b"
)
_JUDGMENTAL = (
    r"\b(?:important|useful|helpful|significant|notable|substantial|reasonable"
    r"|appropriate|better|worse|best|worst|strong|weak|good|bad|poor|excellent"
    r"|crucial|critical|key|essential)\b"
)
_CONJECTURE = (
    r"(?:\bit is possible that\b|\bit is likely that\b|\bthere is a chance\b"
    r"|\bone could argue\b|\bthis suggests\b|\bsuggesting that\b|\bimplies that\b)"
)
_COMPARISON_PREFERENCE = r"\b(?:prefer|preferable|favou?rable|superior|inferior|rather than)\b"

# A conditional is an analysis, not an assertion — the paper groups these with
# subjective for the same reason: there is no fact to check against the context.
_CONDITIONAL = r"^\s*(?:if|assuming|provided that|in the event|should)\b"

_SUBJECTIVE_PATTERNS: list[tuple[str, str]] = [
    ("modal_verb", _MODAL_VERBS),
    ("opinion", _OPINION),
    ("hedging", _HEDGING),
    ("uncertain_quantifier", _UNCERTAIN_QUANTIFIER),
    ("frequency_degree", _FREQUENCY_DEGREE),
    ("judgmental_adjective", _JUDGMENTAL),
    ("conjecture", _CONJECTURE),
    ("comparison_preference", _COMPARISON_PREFERENCE),
    ("conditional", _CONDITIONAL),
]

_COMPILED = [(name, re.compile(pattern, re.IGNORECASE)) for name, pattern in _SUBJECTIVE_PATTERNS]

# Sentence boundary that does not split on the abbreviations and decimals this
# corpus is full of: "AED 1.2bn", "Art. 5.1.2", "approx. 3 yrs", "No. 2024/BSE/047".
_ABBREVIATIONS = r"(?<!\bArt)(?<!\bNo)(?<!\bSec)(?<!\bFig)(?<!\bapprox)(?<!\bvs)(?<!\be\.g)(?<!\bi\.e)"
_SENTENCE_END = re.compile(rf"{_ABBREVIATIONS}(?<=[.!?])(?<!\d\.)\s+(?=[A-Z\[])")

# The generator ends every answer with a Sources: block. It is machine-added
# provenance, not a claim, so it must never be scored as a statement.
_SOURCES_BLOCK = re.compile(r"\n\s*Sources?\s*:.*\Z", re.IGNORECASE | re.DOTALL)

# openai/gpt-oss-120b emits a <think> reasoning trace before its answer, and
# NOTHING in generation/generator.py strips it. That trace is not the answer:
# it contains the user's questions restated, meta-commentary ("I need to look
# at..."), and quotes of the context. Scoring it as assertions makes a correct
# answer look ~90% unsupported — measured, not hypothesised.
#
# The second alternative catches an unterminated block: when the trace runs
# into max_tokens there is no closing tag, and without this the whole answer
# would survive as "content" and be scored as claims.
_THINK_BLOCK = re.compile(r"<think>.*?(?:</think>|\Z)", re.IGNORECASE | re.DOTALL)


def answer_body(answer: str) -> str:
    """The part of a generation that is actually a claim about the world.

    Shared by the span partitioner and the citation auditor so both score
    exactly the same text — otherwise citation counts include refs made inside
    the reasoning trace, which no user-visible sentence carries.
    """
    return _SOURCES_BLOCK.sub("", _THINK_BLOCK.sub("", answer)).strip()

# Markdown list bullets and numbering — stripped so a bullet reads as a sentence.
_BULLET = re.compile(r"^\s*(?:[-*•]|\d+[.)])\s+")

_CITATION = re.compile(r"\[I?\d+\]")


@dataclass
class Span:
    text: str
    subjective: bool
    reason: str = ""  # which marker fired, for the report
    citations: list[str] = None  # [N] / [IN] refs appearing in this span

    def __post_init__(self) -> None:
        if self.citations is None:
            self.citations = _CITATION.findall(self.text)

    @property
    def objective(self) -> bool:
        return not self.subjective


def classify(text: str) -> tuple[bool, str]:
    """Return (is_subjective, marker_name)."""
    for name, pattern in _COMPILED:
        if pattern.search(text):
            return True, name
    return False, ""


def partition(answer: str) -> list[Span]:
    """Split an answer into atomic spans, each labelled subjective/objective.

    Deliberately NOT an LLM call — see the module docstring. Pronoun resolution
    (the paper replaces "It" with "The desk") is not attempted here: this
    pipeline's answers are citation-dense and already largely self-contained,
    and a bad rewrite would silently change what is being scored. Spans keep
    their original wording, and the CS judge sees the full answer as context.
    """
    body = answer_body(answer)
    if not body:
        return []

    spans: list[Span] = []
    for line in body.splitlines():
        line = _BULLET.sub("", line).strip()
        if not line:
            continue
        # A markdown heading is a label, not an assertion.
        if line.startswith("#") or (line.startswith("**") and line.endswith("**")):
            continue
        for sentence in _SENTENCE_END.split(line):
            sentence = sentence.strip()
            # Too short to carry a checkable claim: "Yes.", "[1]".
            if len(sentence) < 12 or not re.search(r"[A-Za-z]{3}", sentence):
                continue
            subjective, reason = classify(sentence)
            spans.append(Span(text=sentence, subjective=subjective, reason=reason))
    return spans


def summarise(spans: list[Span]) -> dict:
    total = len(spans)
    subjective = sum(1 for s in spans if s.subjective)
    return {
        "total": total,
        "objective": total - subjective,
        "subjective": subjective,
        "subjective_ratio": (subjective / total) if total else None,
    }
