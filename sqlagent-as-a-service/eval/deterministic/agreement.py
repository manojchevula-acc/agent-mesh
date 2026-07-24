"""LLM-vs-deterministic agreement — the whole point of running two evaluators.

Given, per question, a binary PASS/FAIL from each evaluator, this quantifies how much they
agree BEYOND CHANCE and names where they part ways. Raw agreement (% identical verdicts) is
misleading when one class dominates — if 90% of questions pass, two evaluators that both
say PASS blindly agree 90% of the time while being useless. Cohen's Kappa corrects for that
by subtracting the agreement expected from the marginal rates, so kappa ~0 means "no better
than chance" and kappa ~1 means "near-perfect concordance".

The disagreement breakdown is the actionable output: LLM-pass / deterministic-fail rows are
usually a semantic equivalence the metrics can't yet see (candidate for a new rule in
schema_semantic.py), and deterministic-pass / LLM-fail rows are usually the LLM being
fooled by cosmetics (evidence the metrics are the stricter, more reliable signal there).

Pure Python — numpy is available but a 2x2 table needs no help.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class AgreementReport:
    n: int
    both_pass: int
    both_fail: int
    llm_only_pass: int          # LLM PASS, deterministic FAIL
    det_only_pass: int          # deterministic PASS, LLM FAIL
    raw_agreement: float
    cohen_kappa: float | None
    kappa_interpretation: str
    llm_pass_rate: float
    deterministic_pass_rate: float
    stricter: str               # which evaluator failed more often ("llm"|"deterministic"|"tie")
    disagreement_ids: dict = field(default_factory=dict)   # {"llm_only_pass": [...], ...}

    def as_dict(self) -> dict:
        return {
            "n": self.n, "both_pass": self.both_pass, "both_fail": self.both_fail,
            "llm_only_pass": self.llm_only_pass, "det_only_pass": self.det_only_pass,
            "raw_agreement": round(self.raw_agreement, 3),
            "cohen_kappa": None if self.cohen_kappa is None else round(self.cohen_kappa, 3),
            "kappa_interpretation": self.kappa_interpretation,
            "llm_pass_rate": round(self.llm_pass_rate, 3),
            "deterministic_pass_rate": round(self.deterministic_pass_rate, 3),
            "stricter": self.stricter,
            "disagreement_ids": self.disagreement_ids,
        }


def cohen_kappa(both_pass: int, both_fail: int, llm_only: int, det_only: int) -> float | None:
    """Cohen's Kappa for the 2x2 PASS/FAIL confusion of two raters.

        kappa = (Po - Pe) / (1 - Pe)
        Po = observed agreement, Pe = agreement expected from the marginals.

    Returns None when it is undefined — when every verdict is identical there is no variance
    to correct for (Pe == 1), and kappa's 0/0 would be reported as a spurious 0.0 ("no
    better than chance") for two evaluators that in fact agree perfectly. None says
    'undefined here', which the report renders honestly."""
    n = both_pass + both_fail + llm_only + det_only
    if n == 0:
        return None
    po = (both_pass + both_fail) / n
    # marginals
    llm_pass = (both_pass + llm_only) / n
    det_pass = (both_pass + det_only) / n
    pe = llm_pass * det_pass + (1 - llm_pass) * (1 - det_pass)
    if pe >= 1.0:
        return None                     # no disagreement possible given the marginals
    return (po - pe) / (1 - pe)


def _interpret(kappa: float | None) -> str:
    """Landis & Koch bands — the conventional reading of a kappa value."""
    if kappa is None:
        return "undefined (no verdict variance)"
    if kappa < 0:
        return "worse than chance"
    if kappa < 0.20:
        return "slight"
    if kappa < 0.40:
        return "fair"
    if kappa < 0.60:
        return "moderate"
    if kappa < 0.80:
        return "substantial"
    return "almost perfect"


def compute_agreement(pairs: list[dict]) -> AgreementReport:
    """pairs: [{"id", "llm_pass": bool, "det_pass": bool}] — one per evaluated question.

    Rows whose LLM verdict is unknown (judge unavailable / --no-llm, carried as llm_pass is
    None) are excluded: an agreement statistic can only be computed where BOTH evaluators
    rendered a verdict, and silently coercing 'unknown' to FAIL would fabricate
    disagreements the judge never expressed."""
    usable = [p for p in pairs if p.get("llm_pass") is not None and p.get("det_pass") is not None]
    n = len(usable)
    both_pass = sum(1 for p in usable if p["llm_pass"] and p["det_pass"])
    both_fail = sum(1 for p in usable if not p["llm_pass"] and not p["det_pass"])
    llm_only = sum(1 for p in usable if p["llm_pass"] and not p["det_pass"])
    det_only = sum(1 for p in usable if not p["llm_pass"] and p["det_pass"])

    raw = (both_pass + both_fail) / n if n else 1.0
    kappa = cohen_kappa(both_pass, both_fail, llm_only, det_only)
    llm_pass_rate = (both_pass + llm_only) / n if n else 0.0
    det_pass_rate = (both_pass + det_only) / n if n else 0.0
    stricter = ("llm" if llm_only < det_only else
                "deterministic" if det_only < llm_only else "tie")

    return AgreementReport(
        n=n, both_pass=both_pass, both_fail=both_fail,
        llm_only_pass=llm_only, det_only_pass=det_only,
        raw_agreement=raw, cohen_kappa=kappa, kappa_interpretation=_interpret(kappa),
        llm_pass_rate=llm_pass_rate, deterministic_pass_rate=det_pass_rate,
        stricter=stricter,
        disagreement_ids={
            "llm_pass_deterministic_fail": [p["id"] for p in usable
                                            if p["llm_pass"] and not p["det_pass"]],
            "deterministic_pass_llm_fail": [p["id"] for p in usable
                                            if not p["llm_pass"] and p["det_pass"]],
        },
    )
