"""Every pass/fail bar in the evaluation suite, in one reviewable file.

The point of centralising these is that a threshold is a PRODUCT DECISION, not
an implementation detail. Scattering them across stage modules makes it
impossible to answer "what does this system consider good enough?" without
reading all the code.

Each bar carries a comment saying *why* that number. A bar with no reasoning is
a bar nobody can defend in review.

This file does NOT replace ``src/gernas_rag/evaluation/metrics.py`` — that one
belongs to ``RAGEvaluator`` and the live ``/evaluate`` endpoint, which are
shipped product code. Stage 4's RAGAS mode imports from there and re-exports
below, so there is exactly one definition of each shared bar.
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class Bar:
    """A single quality bar.

    ``required`` distinguishes a gate from a watch-metric: only required bars
    can fail a run (non-zero exit). Everything else is reported and tracked but
    does not block, which keeps new diagnostics from breaking CI on day one.
    """

    value: float
    why: str
    required: bool = True
    # Most bars are floors (recall, accuracy). A few are CEILINGS — orphan_rate
    # and subjective_span_ratio are bad when they go up. Without this flag they
    # silently report "pass" at any value, which is worse than not measuring
    # them: it looks like a green check on a metric nobody is actually checking.
    higher_is_better: bool = True

    def passed(self, observed: float) -> bool:
        return observed >= self.value if self.higher_is_better else observed <= self.value


# ── Stage 1 — Extraction & layout fidelity ───────────────────────────────
# Scored against data/eval/layout_manifest.json.
STAGE1 = {
    # A missed heading breaks hierarchical chunking's parent/child split, so
    # every downstream chunk from that section inherits the wrong parent.
    "heading_recall": Bar(0.90, "Headings drive parent/child chunk boundaries"),
    # Figures are the whole point of the multimodal path. A figure Docling
    # never sees can never be retrieved, no matter how good the encoder is.
    "figure_recall": Bar(0.95, "A missed figure is unreachable by any later stage"),
    # Tables carry the numeric policy content most gold questions ask about.
    "table_recall": Bar(0.95, "Tables hold the numbers gold Q&A asks for"),
    # Page attribution feeds citation correctness and the caption resolver's
    # nearest-heading lookup. Wrong page => wrong caption => wrong stub chunk.
    "page_accuracy": Bar(0.95, "Wrong page corrupts captions and citations"),
}

# ── Stage 2a — Index & artifact integrity ────────────────────────────────
# Live cross-check of Qdrant against the on-disk asset store. No ground truth
# file: the invariants are structural, so any violation is a bug by definition.
STAGE2A = {
    "asset_resolvable_rate": Bar(1.00, "An indexed vector whose asset is missing is a 404 to the user"),
    "stub_coverage": Bar(0.95, "Image captions must be lexically searchable via stub chunks"),
    "table_atomicity": Bar(1.00, "A table split mid-row is unreadable to the LLM"),
    # Ceiling, not a floor. Some drift is normal after a re-ingest that changed
    # chunk boundaries; a large figure means reconciliation is not running.
    "orphan_rate": Bar(
        0.10, "Assets on disk with no vector are dead bytes", required=False,
        higher_is_better=False,
    ),
}

# ── Stage 2b — Vision perception fidelity ────────────────────────────────
# Scored against data/eval/figure_transcriptions.json. RAG-Check calls this
# context-generation-hallucination: the VLM misreading the pixels, isolated
# from retrieval and from the answer prompt.
STAGE2B = {
    # The single most load-bearing number here. eval_runs.md case 12 (the MRM
    # dashboard, unreadable at 256px) is exactly this metric failing while
    # every retrieval metric looked fine.
    "numeric_fidelity": Bar(0.85, "Chart values misread at low resolution is failure mode #1"),
    "entity_recall": Bar(0.80, "Axis labels, legends and series names must survive"),
    "no_fabrication_rate": Bar(0.95, "Inventing a value not in the crop is worse than omitting it"),
}

# ── Stage 3 — Retrieval quality (RAG-Check: selection-hallucination / RS) ─
STAGE3 = {
    # Lines up with config/default.yaml retrieval.final_top_k = 5. Note
    # config/local.yaml overrides this to 3 — the check reads the live setting
    # and reports which k it actually scored at.
    "hit_rate_at_k": Bar(0.95, "Matches final_top_k; below this the answer cannot be right"),
    "recall_at_k": Bar(0.85, "Multi-fact answers need every supporting chunk, not just one"),
    "mrr": Bar(0.80, "Rank matters: the generator weights early context more heavily"),
    "context_precision": Bar(0.70, "Irrelevant chunks crowd out the token budget"),
    # The RS-proxy. Plain cosine aligned with human relevance at only 0.49-0.69
    # in RAG-Check Table II vs 0.879 for a trained cross-attention scorer. The
    # text path already closes this gap with bge-reranker-v2-m3; the image path
    # does not, so this bar starts as a watch-metric until it is calibrated.
    "image_relevancy": Bar(0.70, "SigLIP2 cosine is uncalibrated on this corpus", required=False),
    "score_gate_agreement": Bar(0.75, "Validates the 0.25 floor / 0.55 margin", required=False),
}

# ── Stage 4 — Answer quality (RAG-Check: response-hallucination / CS) ────
STAGE4 = {
    "answer_correctness": Bar(0.85, "End-to-end: does it match the gold answer"),
    # The CS-proxy: per-objective-span, after subjective spans are filtered out
    # per RAG-Check III-A. Strictly harder than whole-answer correctness.
    "span_correctness": Bar(0.85, "Per-statement, so one wrong clause cannot hide in a right answer"),
    "groundedness": Bar(0.95, "Asserting facts absent from context is the cardinal RAG sin"),
    "answer_relevancy": Bar(0.85, "On-topic but answering a different question still fails the user"),
    "citation_validity": Bar(1.00, "A [N] pointing at nothing destroys auditability"),
    "citation_support": Bar(0.90, "A citation that does not support its claim is worse than none"),
    "numeric_recall": Bar(0.90, "Policy answers are numbers; a missing bps figure is a wrong answer"),
    "abstention_accuracy": Bar(1.00, "Answering an unanswerable question is unacceptable in banking"),
    # Ceiling. A generator that hedges everything scores well on groundedness
    # while telling the user nothing, so this is the counterweight to it.
    "subjective_span_ratio": Bar(
        0.30, "High means the prompt over-hedges", required=False,
        higher_is_better=False,
    ),
}

ALL: dict[str, dict[str, Bar]] = {
    "stage1": STAGE1,
    "stage2a": STAGE2A,
    "stage2b": STAGE2B,
    "stage3": STAGE3,
    "stage4": STAGE4,
}


def required_bars(stage: str) -> dict[str, Bar]:
    return {k: v for k, v in ALL[stage].items() if v.required}
