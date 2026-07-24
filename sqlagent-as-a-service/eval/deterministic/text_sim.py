"""String and set similarity primitives — pure Python, zero dependencies.

The only third-party packages available in this project are sqlglot and numpy (see
pyproject.toml); rapidfuzz / python-Levenshtein / scikit-learn are NOT. Rather than pull
in a dependency for a handful of well-understood formulas, they are implemented here. Each
is small, exact, and unit-testable, and the eval must stay runnable on a bare checkout.

Everything returns a similarity in [0.0, 1.0] where 1.0 == identical, so scores from
different families (edit distance, token overlap, set Jaccard) compose on one scale.
"""

from __future__ import annotations

import re


def levenshtein(a: str, b: str) -> int:
    """Edit distance: the fewest single-character inserts/deletes/substitutions from a->b.

    Iterative two-row DP (O(len(a)*len(b)) time, O(min) space). Used as the raw signal
    behind `ratio`; exposed on its own because a caller sometimes wants the count, not the
    normalized score (e.g. "off by 1 character" is a different story from "42% similar").
    """
    if a == b:
        return 0
    if not a:
        return len(b)
    if not b:
        return len(a)
    # Keep the shorter string as the inner loop so the row we allocate is the smaller one.
    if len(a) < len(b):
        a, b = b, a
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            cur.append(min(
                prev[j] + 1,          # deletion
                cur[j - 1] + 1,       # insertion
                prev[j - 1] + (ca != cb),  # substitution (0 if the chars already match)
            ))
        prev = cur
    return prev[-1]


def ratio(a: str, b: str) -> float:
    """Normalized edit similarity in [0,1]: 1 - levenshtein / max(len).

    Case- and whitespace-normalized first, because for our purposes "Trade Finance" and
    "trade  finance" are the same textual value — the differences we want to reward the
    agent for tolerating are cosmetic, and the ones we want to catch are substantive
    (a genuinely different name), which survive normalization.
    """
    a, b = _norm(a), _norm(b)
    if a == b:
        return 1.0
    if not a and not b:
        return 1.0
    dist = levenshtein(a, b)
    longest = max(len(a), len(b))
    return 1.0 - dist / longest if longest else 1.0


def token_set_ratio(a: str, b: str) -> float:
    """Order-insensitive token similarity — Jaccard over the WORD sets of a and b.

    Complements `ratio`: edit distance punishes reordering ("Ltd Trading Co" vs
    "Trading Co Ltd" is a large edit distance but clearly the same entity), while token
    overlap is blind to order. The evaluator takes the max of the two so neither failure
    mode (a typo OR a reordering) alone tanks the score.
    """
    ta, tb = set(_tokens(a)), set(_tokens(b))
    return jaccard(ta, tb)


def best_ratio(a: str, b: str) -> float:
    """The kinder of edit-similarity and token-set-similarity — see token_set_ratio."""
    return max(ratio(a, b), token_set_ratio(a, b))


def jaccard(a: set, b: set) -> float:
    """|A n B| / |A u B|. Two empty sets are defined as identical (1.0), matching the
    intuition that "neither selected anything" is agreement, not undefined."""
    if not a and not b:
        return 1.0
    union = a | b
    return len(a & b) / len(union) if union else 1.0


def _norm(s) -> str:
    """Lowercase, collapse runs of whitespace. The single place textual normalization is
    defined, so `ratio` and `token_set_ratio` treat values identically."""
    return re.sub(r"\s+", " ", str(s).strip().lower())


def _tokens(s) -> list[str]:
    """Alphanumeric word tokens of a normalized string."""
    return re.findall(r"[a-z0-9]+", _norm(s))
