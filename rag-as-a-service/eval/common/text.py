"""Text repair and match-normalisation shared across stages.

Two distinct jobs, deliberately kept apart:

``repair_mojibake``  fixes UTF-8-read-as-cp1252 damage in the imported ground
                     truth. Lossy input: the corruption dropped bytes, so some
                     characters are unrecoverable by rule and get FLAGGED
                     rather than guessed.

``normalize``        folds text for comparison at scoring time (dash variants,
                     whitespace, case). Applied to BOTH sides of every match so
                     a VLM writing "3-5yr" is not penalised against "3–5yr".
"""

import re
import unicodedata

# ── Mojibake repair ──────────────────────────────────────────────────────
# The imported ground truth was written as UTF-8, read back as cp1252, and the
# middle byte of each 3-byte sequence was dropped. `—` (E2 80 94) survives as a
# bare `â`, and so does `–`, `→`, `↑`, `↓`, `≤`. One glyph, many origins — so
# these rules resolve only what CONTEXT makes unambiguous, in order, and
# everything else is reported for a human.
_RULES: list[tuple[str, str, str]] = [
    # (pattern, replacement, why) — order matters, specific before general.
    (r"Â·", "·", "middle dot: C2 B7 kept both bytes, unambiguous"),
    (r"Â§", "§", "section sign: C2 A7 kept both bytes, unambiguous"),
    (r"â¤", "≤", "only ≤ appears before a tenor bucket ('≤ 1 Year', '≤ 3mo')"),
    (r"Ã(?= Facility| Tenor)", "×", "'Client Rating × Facility Tenor' crosstab header"),
    # Numeric ranges: en dash. '1â3 Years', 'ticks 90â160', '0.80â0.90'.
    (r"(?<=\d)â(?=\d)", "–", "numeric range => en dash"),
    # Magnitude ranges: 'AED 50Mâ250M', 'AED 500Mâ1 billion'.
    (r"(?<=\d[MB])â(?=\d)", "–", "money range => en dash"),
    # Compound labels with no surrounding spaces: 'GREâUAE', 'AAAâBBB',
    # 'JanâJun', 'CorpâSub-IG'. A spaced em dash would have kept its spaces.
    (r"(?<=[A-Za-z])â(?=[A-Za-z])", "–", "unspaced compound label => en dash"),
    # Ratings written 'B–' / 'B-' at a token end: '(Bâ)'.
    (r"(?<=[A-Z])â(?=\))", "-", "rating suffix, e.g. '(B-)'"),
    # Everything left that is SPACE-DELIMITED is an em dash used as a separator.
    (r"(?<= )â(?= )", "—", "spaced separator => em dash"),
]

# Any of these surviving a repair pass means a character we refused to guess.
_RESIDUE = re.compile(r"[âÂÃ]")


def repair_mojibake(text: str) -> tuple[str, list[str]]:
    """Return (repaired_text, unresolved_fragments).

    Unresolved fragments are returned with surrounding context so a reviewer can
    decide. They are NOT silently dropped or replaced: ground truth that quietly
    invents a character is worse than ground truth that admits a gap.
    """
    out = text
    for pattern, replacement, _why in _RULES:
        out = re.sub(pattern, replacement, out)

    unresolved = []
    for match in _RESIDUE.finditer(out):
        start, end = max(0, match.start() - 40), min(len(out), match.end() + 40)
        unresolved.append(out[start:end].replace("\n", " "))
    return out, unresolved


def repair_tree(node):
    """Recursively repair every string in a parsed-JSON structure."""
    flagged: list[str] = []
    if isinstance(node, str):
        fixed, unresolved = repair_mojibake(node)
        return fixed, unresolved
    if isinstance(node, list):
        out = []
        for item in node:
            fixed, unresolved = repair_tree(item)
            out.append(fixed)
            flagged += unresolved
        return out, flagged
    if isinstance(node, dict):
        out = {}
        for key, value in node.items():
            fixed, unresolved = repair_tree(value)
            out[key] = fixed
            flagged += unresolved
        return out, flagged
    return node, flagged


# ── Match normalisation ──────────────────────────────────────────────────
_DASHES = dict.fromkeys(map(ord, "‒–—―‐‑−"), "-")
_QUOTES = {ord("‘"): "'", ord("’"): "'", ord("“"): '"', ord("”"): '"'}
_WS = re.compile(r"\s+")


def normalize(text: str) -> str:
    """Fold text so cosmetic differences do not count as errors.

    Applied to both ground truth and model output. Deliberately does NOT touch
    digits, '%', '<', '>' or '≤' — those carry meaning that a scorer must keep.
    """
    out = unicodedata.normalize("NFKC", text)
    out = out.translate(_DASHES).translate(_QUOTES)
    out = out.replace("·", " ").replace("§", "section ")
    return _WS.sub(" ", out).strip().lower()
