"""Standalone check (H3): does RETRIEVAL quality actually PREDICT end-to-end SQL success?

The two retrieval checks (check_table_retrieval.py, check_example_retrieval.py) measure
whether the agent was fed the right context. The deterministic SQL evaluator
(eval/deterministic/) measures whether the agent then produced the right answer. On their
own, each only measures SIMILARITY ("did we retrieve something that looks right"). This
script joins them to measure USEFULNESS ("did retrieving it actually cause a correct
answer") — entirely deterministically, with NO LLM: it only READS the YAML/JSON the other
tools already wrote. It never runs the agent, never touches a provider key.

For every retrieval signal (a per-question boolean the retrieval checks record) it builds a
2x2 contingency against the deterministic PASS/FAIL, keyed by question id, over the
questions that are BOTH present in the retrieval record AND *evaluable* in the SQL report
(a rate-limited / no-SQL run produced no answer, so it says nothing about retrieval and is
excluded). It then reports two deterministic, reproducible statistics per signal:

  lift  = P(PASS | signal=1) / P(PASS | signal=0)
          "how many times likelier is a correct answer when retrieval succeeded" — >1 means
          the signal helps; ~1 means it is not predictive of success.
  MCC   = the Matthews correlation coefficient of the 2x2 (phi coefficient): +1 perfect
          agreement, 0 no better than chance, -1 systematic disagreement. Robust on the
          skewed pass-rates a small POC benchmark produces.

Run:
    uv run python eval/correlate_retrieval_outcome.py \
        --det-report eval/results/sql_eval/DETERMINISTIC_EVAL_REPORT_JOIN.json
    # defaults: table_retrieval/ + example_retrieval/ records in eval/results/
"""

from __future__ import annotations

import argparse
import json
import math
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))

import yaml  # noqa: E402

RESULTS = HERE / "results"
OUT_PATH = RESULTS / "retrieval_correlation" / "retrieval_outcome_correlation.yaml"


def _base_id(rid: str) -> str:
    """Strip a paraphrase/variant suffix (`D11::other-language`) down to the gold id."""
    return str(rid).split("::")[0]


def _load_yaml(path: Path) -> dict | None:
    if not path.exists():
        return None
    return yaml.safe_load(path.read_text(encoding="utf-8")) or {}


def _pass_by_id(det: dict) -> dict[str, bool]:
    """{gold_id -> passed} over EVALUABLE rows only. Prefers the canonical (non-variant)
    row when several share a base id, so a paraphrase can't outvote the primary question."""
    out: dict[str, bool] = {}
    canonical: set[str] = set()
    for row in det.get("rows", []):
        if not row.get("evaluable", False):
            continue
        rid = row.get("id", "")
        base = _base_id(rid)
        is_canonical = (rid == base)
        if base in canonical and not is_canonical:
            continue  # already have the canonical verdict; ignore variants
        out[base] = bool(row.get("passed", False))
        if is_canonical:
            canonical.add(base)
    return out


def _contingency(pairs: list[tuple[bool, bool]]) -> dict:
    """pairs = [(signal, passed), ...] -> 2x2 counts + lift + MCC (all deterministic)."""
    n11 = sum(1 for s, p in pairs if s and p)
    n10 = sum(1 for s, p in pairs if s and not p)
    n01 = sum(1 for s, p in pairs if not s and p)
    n00 = sum(1 for s, p in pairs if not s and not p)

    p_pass_if_1 = n11 / (n11 + n10) if (n11 + n10) else None
    p_pass_if_0 = n01 / (n01 + n00) if (n01 + n00) else None
    lift = (p_pass_if_1 / p_pass_if_0) if (p_pass_if_1 is not None
                                           and p_pass_if_0 not in (None, 0.0)) else None

    denom = math.sqrt((n11 + n10) * (n11 + n01) * (n00 + n10) * (n00 + n01))
    mcc = ((n11 * n00 - n10 * n01) / denom) if denom else None

    return {
        "n": n11 + n10 + n01 + n00,
        "counts": {"signal1_pass": n11, "signal1_fail": n10,
                   "signal0_pass": n01, "signal0_fail": n00},
        "pass_rate_when_signal": round(p_pass_if_1, 3) if p_pass_if_1 is not None else None,
        "pass_rate_when_not": round(p_pass_if_0, 3) if p_pass_if_0 is not None else None,
        "lift": round(lift, 3) if lift is not None else None,
        "mcc": round(mcc, 3) if mcc is not None else None,
    }


# Each signal: (label, source file key, function mapping a retrieval item -> bool | None).
# Returning None drops the question from that signal's contingency (no signal recorded).
TABLE_SIGNALS = {
    "generator_full_recall": lambda it: it.get("generator_full_recall"),
    "core_full_recall": lambda it: it.get("core_full_recall"),
    "column_full_recall": lambda it: it.get("column_full_recall"),
    "context_sufficient (tables AND columns)":
        lambda it: (bool(it.get("generator_full_recall")) and bool(it.get("column_full_recall")))
        if it.get("column_full_recall") is not None else None,
}
EXAMPLE_SIGNALS = {
    "either_hit": lambda it: it.get("either_hit"),
    "strong_hit": lambda it: it.get("strong_hit"),
    "operator_coverage==1.0":
        lambda it: (it.get("operator_coverage") == 1.0)
        if it.get("operator_coverage") is not None else None,
}


def _correlate(items: list[dict], signals: dict, passed: dict[str, bool]) -> dict:
    out = {}
    for label, fn in signals.items():
        pairs: list[tuple[bool, bool]] = []
        for it in items:
            base = _base_id(it.get("id", ""))
            if base not in passed:
                continue           # not evaluable / not in the SQL report
            sig = fn(it)
            if sig is None:
                continue           # signal not recorded for this question
            pairs.append((bool(sig), passed[base]))
        if pairs:
            out[label] = _contingency(pairs)
    return out


def _print_block(title: str, block: dict) -> None:
    print(f"\n{title}")
    print(f"  {'signal':<40s} {'n':>3s} {'pass|1':>7s} {'pass|0':>7s} {'lift':>6s} {'MCC':>6s}")
    for label, s in block.items():
        pr1 = f"{s['pass_rate_when_signal']:.2f}" if s["pass_rate_when_signal"] is not None else "  -"
        pr0 = f"{s['pass_rate_when_not']:.2f}" if s["pass_rate_when_not"] is not None else "  -"
        lift = f"{s['lift']:.2f}" if s["lift"] is not None else "  -"
        mcc = f"{s['mcc']:+.2f}" if s["mcc"] is not None else "   -"
        print(f"  {label:<40s} {s['n']:>3d} {pr1:>7s} {pr0:>7s} {lift:>6s} {mcc:>6s}")


def main() -> int:
    ap = argparse.ArgumentParser(
        description="Correlate retrieval signals with the deterministic SQL PASS/FAIL "
                    "(retrieval usefulness, not just similarity). Read-only; no LLM.")
    ap.add_argument("--det-report", required=True,
                    help="deterministic report JSON (eval/results/sql_eval/*.json)")
    ap.add_argument("--table-retrieval", default=str(RESULTS / "table_retrieval" / "table_retrieval.yaml"),
                    help="table retrieval record (default eval/results/table_retrieval/table_retrieval.yaml)")
    ap.add_argument("--example-retrieval", default=str(RESULTS / "example_retrieval" / "example_retrieval.yaml"),
                    help="example retrieval record (default eval/results/example_retrieval/example_retrieval.yaml)")
    ap.add_argument("--out", default=None, help=f"YAML output (default {OUT_PATH})")
    args = ap.parse_args()

    det_path = Path(args.det_report)
    det = _load_yaml_or_json(det_path)
    if det is None:
        avail = sorted((RESULTS / "sql_eval").glob("*.json"))
        raise SystemExit(
            f"No deterministic report at {det_path}. Run eval/deterministic_eval.py first."
            + (f"\nAvailable: {', '.join(p.name for p in avail)}" if avail else ""))
    passed = _pass_by_id(det)
    if not passed:
        raise SystemExit("The deterministic report has no evaluable rows to correlate.")

    table_doc = _load_yaml(Path(args.table_retrieval))
    example_doc = _load_yaml(Path(args.example_retrieval))

    result: dict = {
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "det_report": det_path.name,
        "evaluable_questions": len(passed),
        "overall_pass_rate": round(sum(passed.values()) / len(passed), 3),
    }
    print(f"Deterministic report : {det_path.name}")
    print(f"Evaluable questions  : {len(passed)}  (overall pass rate "
          f"{100 * sum(passed.values()) / len(passed):.0f}%)")

    if table_doc and table_doc.get("items"):
        block = _correlate(table_doc["items"], TABLE_SIGNALS, passed)
        result["table_retrieval"] = block
        _print_block("TABLE retrieval signals -> SQL pass:", block)
    else:
        print("\n(no table_retrieval.yaml — run eval/check_table_retrieval.py to include it)")

    if example_doc and example_doc.get("items"):
        block = _correlate(example_doc["items"], EXAMPLE_SIGNALS, passed)
        result["example_retrieval"] = block
        _print_block("EXAMPLE retrieval signals -> SQL pass:", block)
    else:
        print("\n(no example_retrieval.yaml — run eval/check_example_retrieval.py to include it)")

    out_path = Path(args.out) if args.out else OUT_PATH
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(
        yaml.safe_dump(result, sort_keys=False, allow_unicode=True, default_flow_style=False),
        encoding="utf-8")
    print(f"\nWrote {out_path}")
    return 0


def _load_yaml_or_json(path: Path) -> dict | None:
    """The deterministic report is JSON; tolerate a YAML path too (YAML is a JSON superset)."""
    if not path.exists():
        return None
    text = path.read_text(encoding="utf-8")
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return yaml.safe_load(text) or {}


if __name__ == "__main__":
    raise SystemExit(main())
