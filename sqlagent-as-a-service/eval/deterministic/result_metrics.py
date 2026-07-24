"""Result-set similarity — compare the ROWS gold and the agent returned, not their text.

eval/compare_llm.result_match already answers ONE question well: "are these two result
sets the same answer?" as a boolean, via a careful name-first / permutation column
alignment. This module keeps that alignment idea but reports the GRADED picture the task
asks for — row precision/recall/F1, cell accuracy, exact-match, Jaccard, and a fuzzy score
— so a near-miss (17 of 18 rows right, one number off) is distinguishable from a total
miss, and so the deterministic verdict has evidence to weigh rather than a single bit.

The comparison is intensionally forgiving of everything that is not the answer, and strict
about everything that is:
  FORGIVEN  different row ORDER (unless the question is a ranking), different column
            NAMES/order, EXTRA agent columns, numeric rounding (numeric_tolerance),
            0/1 vs True/False, whitespace/case in text, minor text typos (fuzzy).
  STRICT    a value that is genuinely different, a missing/extra row, a NULL where a value
            was expected.

Cells are aligned column-to-column the same way compare_llm does (name-first, else search
a bounded set of permutations), because comparing an unordered BAG of cell values would
let a query that swaps two columns pass. Rows are then aligned greedily by best similarity
so row-level precision/recall means "how many gold rows have a matching agent row".
"""

from __future__ import annotations

import decimal
import itertools
from dataclasses import dataclass, field

from eval.deterministic import text_sim


@dataclass
class ResultComparison:
    row_precision: float = 0.0
    row_recall: float = 0.0
    row_f1: float = 0.0
    cell_accuracy: float = 0.0          # over aligned rows: fraction of cells that match
    exact_match: bool = False           # every row & cell identical (within tolerance)
    jaccard: float = 0.0                # multiset Jaccard over canonicalized rows
    fuzzy_similarity: float = 0.0       # mean best-match row similarity, text fuzzed
    gold_rows: int = 0
    agent_rows: int = 0
    matched_rows: int = 0
    column_mapping: list = field(default_factory=list)   # gold col -> agent col used
    note: str = ""

    def as_dict(self) -> dict:
        return {
            "row_precision": round(self.row_precision, 3),
            "row_recall": round(self.row_recall, 3),
            "row_f1": round(self.row_f1, 3),
            "cell_accuracy": round(self.cell_accuracy, 3),
            "exact_match": self.exact_match,
            "jaccard": round(self.jaccard, 3),
            "fuzzy_similarity": round(self.fuzzy_similarity, 3),
            "gold_rows": self.gold_rows, "agent_rows": self.agent_rows,
            "matched_rows": self.matched_rows,
            "column_mapping": self.column_mapping, "note": self.note,
        }


# --------------------------------------------------------------------------- #
# Cell coercion & comparison
# --------------------------------------------------------------------------- #
def _cell(v):
    """One DB value -> ('num', float) | ('null', None) | ('text', str).

    Tagging the type up front lets the comparators apply the right rule: numbers get a
    tolerance, NULLs only ever match NULLs (a NULL is the absence of an answer, never
    equal to 0 or ''), and text gets fuzzy-compared. MySQL returns Decimals (sometimes as
    strings) and 0/1 for booleans, so numeric strings must coerce to float."""
    if v is None:
        return ("null", None)
    if isinstance(v, bool):
        return ("num", float(v))
    if isinstance(v, (int, float, decimal.Decimal)):
        return ("num", float(v))
    s = str(v).strip()
    if s == "":
        return ("text", "")
    try:
        return ("num", float(s))
    except (TypeError, ValueError):
        return ("text", s)


def _tol(gold_value: float, abs_floor: float) -> float:
    """Absolute tolerance derived from gold's published PRECISION — half the last shown
    digit — floored by the dataset's numeric_tolerance. Same rationale as
    compare_llm._tolerance_for: ROUND(...,1) publishing 16.2 for a true 16.25 is not a
    different answer, and a fixed percentage band is simultaneously too tight for small
    numbers and too loose for large ones."""
    try:
        exp = decimal.Decimal(str(gold_value)).as_tuple().exponent
    except (decimal.InvalidOperation, ValueError):
        return abs_floor
    dp = -exp if isinstance(exp, int) and exp < 0 else 0
    return max(abs_floor, 0.5 * (10.0 ** -dp))


def _cell_similarity(g, a, abs_tol: float, fuzzy: bool) -> float:
    """Similarity of two coerced cells in [0,1]. Numbers: 1.0 within tolerance else 0.0
    (a number is right or wrong, there is no 'partially 42'). Text: exact->1.0, else a
    fuzzy ratio when enabled (typo tolerance) or 0.0. NULL matches only NULL."""
    (gt, gv), (at, av) = g, a
    if gt == "null" or at == "null":
        return 1.0 if gt == at else 0.0
    if gt == "num" and at == "num":
        return 1.0 if abs(gv - av) <= _tol(gv, abs_tol) + 1e-9 else 0.0
    if gt == "num" or at == "num":
        return 0.0                      # number vs text is never the same answer
    if gv == av:
        return 1.0
    return text_sim.best_ratio(gv, av) if fuzzy else 0.0


# --------------------------------------------------------------------------- #
# Column alignment (borrowed contract from compare_llm.result_match)
# --------------------------------------------------------------------------- #
def _project(rows: list[dict], cols: list) -> list:
    return [tuple(_cell(r.get(c)) for c in cols) for r in rows]


def _row_similarity(g_row, a_row, abs_tol: float, fuzzy: bool) -> float:
    """Mean cell similarity across an aligned pair of rows."""
    if not g_row:
        return 1.0
    return sum(_cell_similarity(gc, ac, abs_tol, fuzzy)
               for gc, ac in zip(g_row, a_row)) / len(g_row)


def _choose_mapping(gold: list[dict], got: list[dict], abs_tol: float, fuzzy: bool):
    """Pick the agent-column ordering to align against gold's columns.

    Name-first: if the agent's columns cover gold's names, that mapping is unambiguous and
    is the ONLY one considered — so a same-name/different-value column is a real miss, not
    something a lucky permutation papers over. Only when names differ (a genuine rename) do
    we search permutations for the alignment that maximizes total cell similarity. The
    permutation search is bounded (<=8 agent cols) so a wide result set cannot blow up."""
    gcols = list(gold[0].keys())
    acols = list(got[0].keys())
    by_name = {str(c).lower(): c for c in acols}
    if all(str(c).lower() in by_name for c in gcols):
        mapping = [by_name[str(c).lower()] for c in gcols]
        return gcols, mapping
    if len(acols) < len(gcols) or len(acols) > 8:
        # Cannot cover gold's columns, or too wide to search — align positionally.
        return gcols, acols[:len(gcols)]
    gproj = _project(gold, gcols)
    best, best_score = acols[:len(gcols)], -1.0
    for perm in itertools.permutations(acols, len(gcols)):
        aproj = _project(got, list(perm))
        # score the best greedy row alignment under this column permutation
        score = _greedy_row_score(gproj, aproj, abs_tol, fuzzy)
        if score > best_score:
            best, best_score = list(perm), score
    return gcols, best


def _greedy_row_score(gproj, aproj, abs_tol: float, fuzzy: bool) -> float:
    """Total similarity of the best greedy 1:1 row alignment (order-insensitive)."""
    used = set()
    total = 0.0
    for g_row in gproj:
        best_j, best = -1, -1.0
        for j, a_row in enumerate(aproj):
            if j in used:
                continue
            s = _row_similarity(g_row, a_row, abs_tol, fuzzy)
            if s > best:
                best, best_j = s, j
        if best_j >= 0:
            used.add(best_j)
            total += best
    return total


# --------------------------------------------------------------------------- #
# Public entry point
# --------------------------------------------------------------------------- #
def compare_results(gold: list[dict] | None, got: list[dict] | None, *,
                    order_sensitive: bool = False, numeric_tolerance: float = 0.01,
                    fuzzy: bool = True, match_threshold: float = 0.999) -> ResultComparison:
    """Grade the agent's result set against gold's. See module docstring for the contract.

    `match_threshold` is how similar an aligned row pair must be to count as a "matched
    row" for row-level precision/recall — 0.999 means "every cell within tolerance", i.e.
    exact by default; lower it to credit near-miss rows. `fuzzy` toggles typo-tolerant text
    matching (used for the fuzzy_similarity metric regardless; here it also affects whether
    a one-typo row counts as matched)."""
    cmp = ResultComparison()
    if gold is None:
        cmp.note = "no gold result"
        return cmp
    cmp.gold_rows = len(gold)
    cmp.agent_rows = 0 if got is None else len(got)

    if got is None:
        cmp.note = "agent returned no data"
        return cmp
    if not gold and not got:
        cmp.exact_match = True
        cmp.row_precision = cmp.row_recall = cmp.row_f1 = 1.0
        cmp.cell_accuracy = cmp.jaccard = cmp.fuzzy_similarity = 1.0
        cmp.note = "both empty"
        return cmp
    if not gold or not got:
        cmp.note = "one side empty"
        return cmp

    gcols, mapping = _choose_mapping(gold, got, numeric_tolerance, fuzzy)
    cmp.column_mapping = [[str(g), str(a)] for g, a in zip(gcols, mapping)]
    gproj = _project(gold, gcols)
    aproj = _project(got, mapping)

    # ---- exact (strict) match: same rows as a multiset, order-aware if a ranking -------
    cmp.exact_match = _exact_multiset(gproj, aproj, order_sensitive, numeric_tolerance)

    # ---- row-level P/R/F1 via greedy best-match alignment ------------------------------
    pairs = _align_rows(gproj, aproj, order_sensitive, numeric_tolerance, fuzzy)
    matched = sum(1 for _, _, s in pairs if s >= match_threshold)
    cmp.matched_rows = matched
    cmp.row_recall = matched / len(gproj) if gproj else 1.0
    cmp.row_precision = matched / len(aproj) if aproj else 0.0
    cmp.row_f1 = _f1(cmp.row_precision, cmp.row_recall)

    # ---- cell accuracy over aligned rows (fraction of cells right) ---------------------
    # A gold row can align to NO agent row (`a is None`) when the agent returned fewer rows
    # than gold — there is nothing to zip against, and every cell in that row is correctly
    # "wrong" (0 right cells), not an error. `total_cells` still counts that row's cells in
    # the denominator so a short result set is penalised rather than silently ignored.
    total_cells = sum(len(g) for g, _, _ in pairs) or 1
    right_cells = sum(
        sum(1 for gc, ac in zip(g, a) if _cell_similarity(gc, ac, numeric_tolerance, False) >= 1.0)
        for g, a, _ in pairs if a is not None)
    cmp.cell_accuracy = right_cells / total_cells

    # ---- fuzzy similarity: mean best row similarity, typo-tolerant ---------------------
    cmp.fuzzy_similarity = (sum(s for _, _, s in pairs) / len(pairs)) if pairs else 0.0

    # ---- Jaccard over canonicalized row multiset ---------------------------------------
    cmp.jaccard = _row_jaccard(gproj, aproj, numeric_tolerance)
    return cmp


# --------------------------------------------------------------------------- #
# Row alignment & set metrics
# --------------------------------------------------------------------------- #
def _align_rows(gproj, aproj, order_sensitive, abs_tol, fuzzy):
    """Return [(gold_row, agent_row_or_None, similarity)] — one entry per gold row.

    Order-sensitive (ranking): rows are compared positionally, because for a ranking the
    POSITION carries the answer. Otherwise each gold row is greedily paired with its most
    similar unused agent row, so 'did every gold row come back' is measured independent of
    the order the agent emitted them."""
    if order_sensitive:
        out = []
        for i, g_row in enumerate(gproj):
            a_row = aproj[i] if i < len(aproj) else None
            s = _row_similarity(g_row, a_row, abs_tol, fuzzy) if a_row is not None else 0.0
            out.append((g_row, a_row, s))
        return out
    used = set()
    out = []
    for g_row in gproj:
        best_j, best = -1, -1.0
        for j, a_row in enumerate(aproj):
            if j in used:
                continue
            s = _row_similarity(g_row, a_row, abs_tol, fuzzy)
            if s > best:
                best, best_j = s, j
        if best_j >= 0:
            used.add(best_j)
            out.append((g_row, aproj[best_j], best))
        else:
            out.append((g_row, None, 0.0))
    return out


def _canon_row(row, ndigits: int) -> tuple:
    """A row -> a hashable canonical tuple for multiset ops. Numbers rounded, NULL and text
    tagged distinctly so a NULL never collides with a 0 or an empty string."""
    out = []
    for kind, val in row:
        if kind == "num":
            out.append(("n", round(val, ndigits)))
        elif kind == "null":
            out.append(("z", None))
        else:
            out.append(("t", val.lower()))
    return tuple(out)


def _ndigits(numeric_tolerance: float) -> int:
    return max(0, len(str(numeric_tolerance).split(".")[-1])) if numeric_tolerance else 6


def _exact_multiset(gproj, aproj, order_sensitive: bool, numeric_tolerance: float) -> bool:
    """Strict equality: same rows with the same multiplicity (sequence if a ranking)."""
    if len(gproj) != len(aproj):
        return False
    nd = _ndigits(numeric_tolerance)
    g = [_canon_row(r, nd) for r in gproj]
    a = [_canon_row(r, nd) for r in aproj]
    return g == a if order_sensitive else sorted(g) == sorted(a)


def _row_jaccard(gproj, aproj, numeric_tolerance: float) -> float:
    """Multiset Jaccard over canonicalized rows: shared rows / total distinct rows.

    Multiset-aware (min/max of per-row counts) so duplicate rows are handled honestly — a
    result with three identical rows is not the same as one with a single copy."""
    nd = _ndigits(numeric_tolerance)
    from collections import Counter
    gc = Counter(_canon_row(r, nd) for r in gproj)
    ac = Counter(_canon_row(r, nd) for r in aproj)
    inter = sum((gc & ac).values())
    union = sum((gc | ac).values())
    return inter / union if union else 1.0


def _f1(p: float, r: float) -> float:
    return (2 * p * r / (p + r)) if (p + r) else 0.0
