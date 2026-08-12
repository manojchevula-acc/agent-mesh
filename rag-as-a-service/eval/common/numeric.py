"""Deterministic quantity extraction and matching.

Shared by stage 2b (did the VLM read the chart's numbers correctly?) and
stage 4 (did the answer carry the gold answer's numbers?).

Deterministic on purpose. Numbers are the one part of a banking answer where an
LLM judge adds nothing but cost and variance: 180 either equals 180 or it does
not, and a judge that calls 174 "approximately correct" is actively harmful.
"""

import re

# Captures 1,250 / 13.5 / 0.75 / 65 — grouping separators and decimals both.
_NUMBER = re.compile(r"(?<![\w.])(\d{1,3}(?:,\d{3})+|\d+(?:\.\d+)?)(?![\w])")

# Percentages, bps and currency read as different quantities even at the same
# magnitude: "18.4%" and "18.4bn" must not satisfy one another.
_UNIT_AFTER = re.compile(r"\s*(%|bps|bn|billion|mn|million|m\b|k\b|yr|years?|days?|sec)", re.I)

_UNIT_CANON = {
    "billion": "bn", "million": "mn", "m": "mn", "years": "yr", "year": "yr",
    "days": "day", "day": "day",
}


# Ordinals that structure a reply rather than state a quantity: "1. Title:",
# "2) Y-axis", "- 3. Legend". Vision models answer in enumerated lists, so
# without this every reply invents the numbers 1..N — measured on a real run,
# `invented 1, 2, 3` appeared in 9 of 24 crops and dragged no_fabrication_rate
# down by roughly a third for a reason that has nothing to do with the chart.
#
# Leading markdown emphasis is allowed: vision models routinely answer with
# "**1. Analyze the image:**", which a bare `^\d+\.` pattern misses entirely.
_LIST_MARKER = re.compile(r"^[ \t]*(?:[-*•]\s*)?(?:\*{1,2}|#{1,6})?\s*\d{1,2}[.)](?=[\s*])", re.MULTILINE)

# Ranges written as "ticks 90-160" or "0.2-1.0". Human transcriptions summarise
# an axis this way while the model dutifully enumerates every tick, so without
# expanding the range each intermediate tick reads as a fabrication.
_RANGE = re.compile(r"(\d+(?:\.\d+)?)\s*[-–—]\s*(\d+(?:\.\d+)?)")

# Bare integers below this, when they appear ONLY in the model's output, are
# treated as structure rather than data — list ordinals, "Block 2", "Step 3",
# panel numbers. A fabricated *data* value in this corpus carries a unit, a
# decimal, or a magnitude (bps, %, AED, 18.4, 260). Ignoring small bare
# integers costs the rare real one and removes pervasive formatting noise:
# measured on a 23-crop run they were the single largest contributor.
_STRUCTURAL_INT_MAX = 12


def _strip_list_markers(text: str) -> str:
    return _LIST_MARKER.sub("", text)


def _expected_ranges(text: str) -> list[tuple[float, float]]:
    out = []
    for lo, hi in _RANGE.findall(text):
        try:
            a, b = float(lo), float(hi)
        except ValueError:
            continue
        if a < b:
            out.append((a, b))
    return out


def _is_structural(value: str, unit: str) -> bool:
    if unit or "." in value:
        return False
    try:
        return int(value) <= _STRUCTURAL_INT_MAX
    except ValueError:
        return False


def extract(text: str) -> set[tuple[str, str]]:
    """Return {(value, unit)} found in ``text``.

    Value is normalised (commas stripped, trailing zeros preserved as written).
    Unit is lowercased and canonicalised, or "" when the number is bare.
    List-item ordinals are removed first — they are formatting, not data.
    """
    text = _strip_list_markers(text)
    out: set[tuple[str, str]] = set()
    for match in _NUMBER.finditer(text):
        value = match.group(1).replace(",", "")
        unit_match = _UNIT_AFTER.match(text, match.end())
        unit = ""
        if unit_match:
            unit = unit_match.group(1).lower().strip()
            unit = _UNIT_CANON.get(unit, unit)
        out.add((value, unit))
    return out


def _equal(a: str, b: str) -> bool:
    """Numeric equality that tolerates 18.40 vs 18.4 but nothing else."""
    try:
        return abs(float(a) - float(b)) < 1e-9
    except ValueError:
        return a == b


def recall(expected: str, actual: str) -> tuple[float | None, list[str]]:
    """Fraction of ``expected``'s quantities present in ``actual``.

    Returns (score, missing). Score is None when the expected text holds no
    quantities at all — a signature scribble has nothing to get wrong, and
    scoring it 0% would drag the corpus average down for no reason.
    """
    want, have = extract(expected), extract(actual)
    if not want:
        return None, []

    missing = []
    hit = 0
    for value, unit in sorted(want):
        if any(_equal(value, hv) and (unit == hu or not unit or not hu) for hv, hu in have):
            hit += 1
        else:
            missing.append(f"{value}{unit}")
    return hit / len(want), missing


def fabricated(expected: str, actual: str) -> tuple[float | None, list[str]]:
    """Fraction of ``actual``'s quantities that are NOT in ``expected``.

    RAG-Check's context-generation-hallucination, measured at the pixel-reading
    step: a value the model produced that is simply not in the crop. Reported as
    a rate so it aggregates; the offending values come back for the report.
    """
    want, have = extract(expected), extract(actual)
    ranges = _expected_ranges(expected)

    def accounted_for(value: str, unit: str) -> bool:
        if any(_equal(value, wv) and (unit == wu or not unit or not wu) for wv, wu in want):
            return True
        # Inside a range the transcription already described ("ticks 90-160").
        try:
            number = float(value)
        except ValueError:
            return False
        return any(lo <= number <= hi for lo, hi in ranges)

    candidates = [(v, u) for v, u in have if not _is_structural(v, u)]
    if not candidates:
        return None, []

    invented = [f"{v}{u}" for v, u in sorted(candidates) if not accounted_for(v, u)]
    return len(invented) / len(candidates), invented
