"""Deterministic archetype confidence router (pre-planner anchor).

Turns the EXISTING hybrid retrieval (selector.ranked_tables / dense_table_scores) into a
stable, inspectable HIGH/LOW confidence signal WITHOUT an extra LLM call. It does NOT make
the final table choice — the schema-link planner still arbitrates on LOW confidence. Its
job is to:
  1. guarantee the strongest-matching view is on the planner's shortlist, and
  2. emit a confidence derived from the retrieval score MARGIN + multi-signal agreement,
     so the pipeline can pin one view and skip the planner (HIGH), hand a shortlist to the
     planner (LOW), or short-circuit an out-of-scope question (reject).

Keyword matching over ``search_terms`` is only a CORROBORATING vote; the primary signal is
the embedding score margin, which tolerates paraphrases a keyword list cannot. A confusing
case is, by definition, a LOW-MARGIN case — handled by handing it to the planner, never by
hoping a keyword fires. Never raises: any failure yields an abstain (LOW, no pin), so the
pipeline is never worse than the planner-only path.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from sql_agent.config import settings
from sql_agent.logging_config import get_logger
from sql_agent.semantic_layer.loader import load_semantic_layer
from sql_agent.semantic_layer.selector import dense_table_scores, ranked_tables

log = get_logger("archetype")


@dataclass(frozen=True)
class Archetype:
    """The router's decision for one question.

    ``confidence`` is one of "high" | "low" | "reject". ``top_table`` is set ONLY on a
    high-confidence pin (the view to use, planner skipped); it is None otherwise so a
    caller can't accidentally pin on a low-confidence turn. ``candidates`` is the ranked
    shortlist to feed the planner (low/reject)."""

    top_table: str | None
    confidence: str
    candidates: list[str] = field(default_factory=list)
    top_score: float = 0.0
    margin: float = 0.0
    keyword_view: str | None = None


def _phrases(search_terms: str) -> list[str]:
    """``search_terms`` is a ';'-separated list of trigger phrases (schema.yaml). Split and
    normalise; empty/blank entries are dropped."""
    return [p.strip().lower() for p in (search_terms or "").split(";") if p.strip()]


def _phrase_in(phrase: str, text: str) -> bool:
    """Whole-word/phrase containment (mirrors intent_tagger's matcher) so a short trigger
    can't fire on a substring of an unrelated word."""
    return re.search(rf"\b{re.escape(phrase)}\b", text) is not None


def keyword_view(question: str) -> str | None:
    """The single view whose ``search_terms`` the question hits, or None when ZERO or MORE
    THAN ONE match. Ambiguity abstains on purpose — this is only a corroborating vote, so
    it must never veto a good semantic match."""
    text = (question or "").lower()
    hits = [
        name
        for name, table in load_semantic_layer().tables.items()
        if any(_phrase_in(p, text) for p in _phrases(table.search_terms))
    ]
    return hits[0] if len(hits) == 1 else None


def route(question: str, tables_hint: list[str] | None = None) -> Archetype:
    """Classify the question into a HIGH pin / LOW shortlist / reject, deterministically."""
    ranked = ranked_tables(question)  # fused order for the candidate shortlist, or []
    if not ranked:
        # Retrieval off/unavailable: abstain to the planner-only path with whatever hint
        # we were given (never starve).
        return Archetype(None, "low", list(dict.fromkeys(tables_hint or [])))

    dense = dense_table_scores(question)  # cosine 0..1 for the floor/margin thresholds
    top_table = ranked[0][0]
    top_cos = dense.get(top_table, 0.0)
    others = [c for t, c in dense.items() if t != top_table]
    margin = top_cos - (max(others) if others else 0.0)

    kw = keyword_view(question)  # corroborating vote
    agree = kw is None or kw == top_table

    # Ranked shortlist for the planner (low/reject), hint force-included at the tail.
    shortlist = [t for t, _ in ranked[: settings.archetype_lowconf_top_k]]

    if top_cos < settings.archetype_reject_floor and kw is None:
        conf = "reject"  # matches nothing strongly and no keyword -> out of scope
        pin = None
        candidates = shortlist
    elif (
        top_cos >= settings.archetype_score_floor
        and margin >= settings.archetype_margin
        and agree
    ):
        conf = "high"  # decisive single view -> pin it, skip the planner
        pin = top_table
        candidates = [top_table]
    else:
        conf = "low"  # near-tie / weak-but-present -> let the planner arbitrate
        pin = None
        candidates = shortlist

    # Force-include any external hint (intent classifier) so recall never drops a table
    # the upstream signal was confident about — even on a high-confidence pin.
    for t in tables_hint or []:
        if t not in candidates:
            candidates.append(t)

    log.info(
        "ARCHETYPE top=%s cos=%.3f margin=%.3f kw=%s agree=%s -> %s | cands=%s",
        top_table, top_cos, margin, kw, agree, conf, candidates,
    )
    return Archetype(pin, conf, candidates, top_cos, margin, kw)
