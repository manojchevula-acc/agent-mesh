"""Numeric-fact extraction and comparison.

This is the backbone of the cheap, deterministic half of the suite. Almost every
answer in this corpus is quantitative ("50 bps", "AED 2.0 billion", "31-Dec-2024",
"21%"), so comparing the *set of numeric facts* between two texts catches the
failure mode that matters most — a transposed or invented digit — with no judge,
no API cost and no run-to-run variance.

Design notes:
  * Facts are compared on ``(unit_family, canonical_value)``. Units are kept in
    separate families on purpose: 0.5% and 50 bps are numerically equivalent but
    treating them as equal would let a unit error pass silently.
  * Scale words are folded into the value (``AED 2.0 billion`` -> 2e9) so the
    same amount written two ways still matches.
  * Dotted identifiers (``2.1.1``, ``v1.8.3``) are excluded before parsing —
    they are clause references, not quantities.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from .text import strip_citations

# ── Unit vocabulary ───────────────────────────────────────────────────
_UNIT_FAMILY = {
    "bps": "bps",
    "bp": "bps",
    "basis point": "bps",
    "basis points": "bps",
    "%": "pct",
    "percent": "pct",
    "pct": "pct",
    "day": "day",
    "days": "day",
    "calendar day": "day",
    "calendar days": "day",
    "business day": "day",
    "business days": "day",
    "month": "month",
    "months": "month",
    "year": "year",
    "years": "year",
    "yr": "year",
    "yrs": "year",
    "hour": "hour",
    "hours": "hour",
    "hr": "hour",
    "hrs": "hour",
}

_SCALE = {
    "billion": 1e9,
    "bn": 1e9,
    "million": 1e6,
    "mn": 1e6,
    "thousand": 1e3,
    "k": 1e3,
    "m": 1e6,
}

_MONTHS = {
    "jan": 1, "january": 1, "feb": 2, "february": 2, "mar": 3, "march": 3,
    "apr": 4, "april": 4, "may": 5, "jun": 6, "june": 6, "jul": 7, "july": 7,
    "aug": 8, "august": 8, "sep": 9, "sept": 9, "september": 9, "oct": 10,
    "october": 10, "nov": 11, "november": 11, "dec": 12, "december": 12,
}

_WORD_NUMBERS = {
    "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6,
    "seven": 7, "eight": 8, "nine": 9, "ten": 10, "eleven": 11, "twelve": 12,
}

_MONTH_ALT = "|".join(sorted(_MONTHS, key=len, reverse=True))
_UNIT_ALT = "|".join(sorted((re.escape(u) for u in _UNIT_FAMILY), key=len, reverse=True))
_SCALE_ALT = "|".join(sorted((s for s in _SCALE if len(s) > 1), key=len, reverse=True))
_WORD_NUM_ALT = "|".join(_WORD_NUMBERS)

# Clause / version identifiers: three or more dot-separated groups. Excluded
# wholesale so "2.1.1" never contributes the facts 2.1 and 1.
_DOTTED_ID_RE = re.compile(r"\b\d+(?:\.\d+){2,}\b")

# Markdown ordered-list markers ("1. Mandatory review...", "2) Reviewer access
# ..."). Generated answers are routinely formatted as numbered lists, and a
# bare line-leading "1." has no unit to disambiguate it from a real quantity —
# left unmasked it shows up as a fabricated/ungrounded "1", "2", "3" in every
# multi-point answer. Anchored to the start of a line so a genuine value never
# collides ("35 bps" never opens a line looking like "35. ").
_LIST_MARKER_RE = re.compile(r"^[ \t]*\(?\d{1,2}[.)]\s+", re.MULTILINE)

# Structural cross-references ("Article 4", "Section 8.1", "Clause 2.2",
# "paragraph 3"). These point *at* a place in a document — they are not
# quantities the answer is asserting, and a gold answer that cites five
# articles ("Article 4 ... Article 5 ... Article 7 ... Article 8") otherwise
# contributes five phantom facts that no correct answer can ever "recall",
# driving answer_numeric_recall to 0 on an answer that got the substance right.
# Only the number immediately following the keyword is consumed, so "Article 5
# requires 25% coverage" still yields the real fact 25%.
#
# Deliberately excludes "tier": unlike a section number, a tier number is often
# the asserted fact itself ("Tier 1 (High) — full independent validation;
# Tier 2 (Medium) — targeted, biennial"), so masking it would delete a real
# quantity rather than a pointer.
_STRUCTURAL_REF_RE = re.compile(
    r"\b(?:article|section|clause|paragraph|para|sub-?clause|chapter|"
    r"annex|appendix|schedule|exhibit|table|figure|fig)"
    r"\s*\.?\s*\d+(?:\.\d+)*\b",
    re.IGNORECASE,
)

_DATE_RE = re.compile(
    rf"\b(?:"
    rf"(?P<d1>\d{{1,2}})\s*[-/ ]\s*(?P<m1>{_MONTH_ALT})[a-z]*\.?\s*[-/, ]\s*(?P<y1>\d{{4}})"
    rf"|(?P<m2>{_MONTH_ALT})[a-z]*\.?\s+(?P<d2>\d{{1,2}})(?:st|nd|rd|th)?,?\s+(?P<y2>\d{{4}})"
    rf"|(?P<y3>\d{{4}})-(?P<m3>\d{{2}})-(?P<d3>\d{{2}})"
    rf")\b",
    re.IGNORECASE,
)

# Day + month with no year ("by 31 March each year"). Kept in its own family so
# it can never be confused with a fully-specified date.
_DATE_MD_RE = re.compile(
    rf"\b(?P<d>\d{{1,2}})(?:st|nd|rd|th)?\s+(?P<m>{_MONTH_ALT})[a-z]*\b",
    re.IGNORECASE,
)

_NUMBER_RE = re.compile(
    r"(?:(?P<ccy>AED|USD|EUR|GBP|SAR)\s*)?"
    r"(?P<num>\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?)"
    rf"\s*(?P<scale>{_SCALE_ALT}|[km](?![a-z]))?"
    rf"\s*(?P<unit>{_UNIT_ALT})?",
    re.IGNORECASE,
)

_WORD_NUMBER_RE = re.compile(
    rf"\b(?P<word>{_WORD_NUM_ALT})\s+(?P<unit>{_UNIT_ALT})\b",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class NumericFact:
    """One quantity found in a text, normalised for comparison."""

    value: float
    unit: str  # Canonical family: "bps" | "pct" | "day" | ... | "ccy:aed" | "date" | ""
    raw: str

    @property
    def key(self) -> tuple[str, str]:
        # Integers are formatted exactly: %g would render a date like 20240315 as
        # 2.02403e+07, colliding every date in the same month, and would do the
        # same to large currency amounts. Fractional values go through %.6g,
        # which keeps 2.0 and 2 identical and strips float-repr noise
        # (18.400000000000002) without losing 5.85 or 18.4.
        if float(self.value).is_integer():
            return (self.unit, str(int(self.value)))
        return (self.unit, f"{self.value:.6g}")

    def __str__(self) -> str:
        return self.raw.strip()


def _mask(text: str, spans: list[tuple[int, int]]) -> str:
    """Blank out already-consumed spans so later passes cannot re-parse them."""
    chars = list(text)
    for start, end in spans:
        for i in range(start, end):
            chars[i] = " "
    return "".join(chars)


def numeric_facts(text: str, *, strip_citation_markers: bool = True) -> list[NumericFact]:
    """Extract every quantity, date and unit-bearing number from ``text``.

    Passes run most-specific-first (dotted ids -> full dates -> day/month ->
    spelled numbers -> bare numbers), masking each match so a later, looser
    pattern can never re-parse text an earlier one already claimed.
    """
    if not text:
        return []
    # List markers must be masked before strip_citations: that helper collapses
    # every whitespace run (including newlines) to a single space, after which
    # a MULTILINE "^" no longer lines up with where a line actually started.
    working = _mask(text, [m.span() for m in _LIST_MARKER_RE.finditer(text)])
    working = strip_citations(working) if strip_citation_markers else working
    # Structural cross-references, before any number pass can claim their digits.
    working = _mask(working, [m.span() for m in _STRUCTURAL_REF_RE.finditer(working)])

    facts: list[NumericFact] = []
    consumed: list[tuple[int, int]] = [m.span() for m in _DOTTED_ID_RE.finditer(working)]
    working = _mask(working, consumed)

    # Full dates -> YYYYMMDD.
    spans: list[tuple[int, int]] = []
    for m in _DATE_RE.finditer(working):
        if m.group("y3"):
            year, month, day = int(m.group("y3")), int(m.group("m3")), int(m.group("d3"))
        elif m.group("y1"):
            year = int(m.group("y1"))
            month = _MONTHS[m.group("m1").lower()]
            day = int(m.group("d1"))
        else:
            year = int(m.group("y2"))
            month = _MONTHS[m.group("m2").lower()]
            day = int(m.group("d2"))
        facts.append(NumericFact(float(year * 10000 + month * 100 + day), "date", m.group(0)))
        spans.append(m.span())
    working = _mask(working, spans)

    # Day + month without a year -> MMDD, distinct family.
    spans = []
    for m in _DATE_MD_RE.finditer(working):
        month = _MONTHS[m.group("m").lower()]
        facts.append(NumericFact(float(month * 100 + int(m.group("d"))), "date_md", m.group(0)))
        spans.append(m.span())
    working = _mask(working, spans)

    # Spelled numbers, but only when a unit follows ("two years"). Unconditional
    # word-number matching would turn every "one of the" into a fact.
    spans = []
    for m in _WORD_NUMBER_RE.finditer(working):
        unit = _UNIT_FAMILY[_squash(m.group("unit"))]
        facts.append(NumericFact(float(_WORD_NUMBERS[m.group("word").lower()]), unit, m.group(0)))
        spans.append(m.span())
    working = _mask(working, spans)

    # Digits, with optional currency prefix, scale word and unit suffix.
    for m in _NUMBER_RE.finditer(working):
        value = float(m.group("num").replace(",", ""))
        scale = m.group("scale")
        if scale:
            value *= _SCALE[scale.lower()]
        unit_token = m.group("unit")
        if unit_token:
            unit = _UNIT_FAMILY[_squash(unit_token)]
        elif m.group("ccy"):
            unit = f"ccy:{m.group('ccy').lower()}"
        else:
            unit = ""
        facts.append(NumericFact(value, unit, m.group(0)))

    return facts


def _squash(unit_token: str) -> str:
    """Normalise a matched unit ("Basis  Points" -> "basis points")."""
    return re.sub(r"\s+", " ", unit_token.strip().lower())


# Families whose value encoding is only meaningful with the unit attached: a date
# is stored as the integer YYYYMMDD, so letting it pair with a unit-less number
# would match "20240315" against a bare quantity.
_UNIT_STRICT_FAMILIES = frozenset({"date", "date_md"})


def unit_compatible(a: str, b: str) -> bool:
    """Can two facts with these unit families refer to the same quantity?

    Equal families always match. A unit-*less* family matches anything else,
    because a table cell or an axis reading carries no unit — the unit lives in
    the column header or the axis label ("2.0bn" in a chart the gold answer
    describes as "AED 2.0 billion"; "Apr 21" on a y-axis labelled "%"). Requiring
    both sides to carry the same family turns every table lookup into a false
    miss.

    Two *different* non-empty families never match, so this still refuses
    "50 bps" against "0.5%" — the unit error stage 2b exists to catch.
    """
    if a == b:
        return True
    if a in _UNIT_STRICT_FAMILIES or b in _UNIT_STRICT_FAMILIES:
        return False
    return not a or not b


def match_keys(
    reference: set[tuple[str, str]],
    hypothesis: set[tuple[str, str]],
    *,
    strict_units: bool = True,
) -> set[tuple[str, str]]:
    """Reference keys that have a counterpart in ``hypothesis``.

    With ``strict_units`` the match is plain set intersection on
    ``(unit_family, value)``. Without it, values must still be equal but the unit
    families only need to be :func:`unit_compatible`.
    """
    if strict_units:
        return reference & hypothesis
    by_value: dict[str, set[str]] = {}
    for unit, value in hypothesis:
        by_value.setdefault(value, set()).add(unit)
    return {
        (unit, value)
        for unit, value in reference
        if any(unit_compatible(unit, other) for other in by_value.get(value, ()))
    }


@dataclass(frozen=True)
class NumericComparison:
    """Set comparison of the numeric facts in a reference and a hypothesis."""

    recall: float | None  # Reference facts present in the hypothesis.
    precision: float | None  # Hypothesis facts present in the reference.
    matched: list[str]
    missing: list[str]  # In reference, absent from hypothesis.
    extra: list[str]  # In hypothesis, absent from reference.
    reference_count: int
    hypothesis_count: int

    @property
    def hallucination_rate(self) -> float | None:
        """Share of hypothesis quantities with no counterpart in the reference."""
        if self.hypothesis_count == 0:
            return None
        return len(self.extra) / self.hypothesis_count


def compare_numeric(
    reference: str, hypothesis: str, *, strict_units: bool = True
) -> NumericComparison:
    """Compare the numeric facts of two texts in both directions at once.

    ``strict_units`` defaults to True, which is what stage 2b needs: it compares
    two descriptions of the *same image*, so a unit divergence is a real defect.
    Pass False when comparing prose against indexed chunk text, where units are
    routinely carried by a table header instead of the cell (see
    :func:`unit_compatible`).
    """
    ref_facts = numeric_facts(reference)
    hyp_facts = numeric_facts(hypothesis)
    ref_keys = {f.key: f for f in ref_facts}
    hyp_keys = {f.key: f for f in hyp_facts}

    matched_keys = match_keys(set(ref_keys), set(hyp_keys), strict_units=strict_units)
    missing_keys = ref_keys.keys() - matched_keys
    extra_keys = set(hyp_keys) - match_keys(
        set(hyp_keys), set(ref_keys), strict_units=strict_units
    )

    return NumericComparison(
        recall=len(matched_keys) / len(ref_keys) if ref_keys else None,
        precision=len(matched_keys) / len(hyp_keys) if hyp_keys else None,
        matched=sorted(str(ref_keys[k]) for k in matched_keys),
        missing=sorted(str(ref_keys[k]) for k in missing_keys),
        extra=sorted(str(hyp_keys[k]) for k in extra_keys),
        reference_count=len(ref_keys),
        hypothesis_count=len(hyp_keys),
    )


def unsupported_facts(
    hypothesis: str, sources: list[str], *, strict_units: bool = True
) -> tuple[list[str], int]:
    """Quantities in ``hypothesis`` that appear in none of ``sources``.

    This is the grounding check for generated answers: unlike comparing against
    a terse gold answer (where extra detail is legitimate), every number in an
    answer *must* trace back to a retrieved context block. Returns the
    unsupported facts and the total fact count, so the caller can report a rate.

    Callers grounding an answer against *retrieved chunk text* should pass
    ``strict_units=False``: an answer that correctly writes "AED 2.0 billion"
    from a table row reading "2.0bn" is grounded, and flagging it would raise an
    error-severity finding for a formatting difference.
    """
    hyp_facts = {f.key: f for f in numeric_facts(hypothesis)}
    if not hyp_facts:
        return [], 0
    supported: set[tuple[str, str]] = set()
    for source in sources:
        supported |= {f.key for f in numeric_facts(source)}
    matched = match_keys(set(hyp_facts), supported, strict_units=strict_units)
    unsupported = [str(fact) for key, fact in hyp_facts.items() if key not in matched]
    return sorted(unsupported), len(hyp_facts)
