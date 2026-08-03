"""Central pass/fail gates for every stage.

Kept in one file so the quality bar of the whole system is reviewable in a single
diff, and so raising a bar is a deliberate, greppable change rather than an edit
buried in a scoring function.

Direction semantics: ``min`` = the metric must be at least the threshold;
``max`` = it must be at most the threshold.

Thresholds marked ``gating=False`` are reported but never fail a run. Use that
for metrics that describe a known design gap (e.g. media chunks having no
parent) or that are informational rather than contractual — they still show up
in the report and in trend data, they just do not block CI.
"""

from __future__ import annotations

from dataclasses import dataclass

from .models import Direction


@dataclass(frozen=True)
class Gate:
    threshold: float
    direction: Direction = "min"
    gating: bool = True
    unit: str = ""


# ── Stage 1: extraction / layout fidelity ─────────────────────────────
STAGE1: dict[str, Gate] = {
    # A figure that is never detected can never be retrieved — this is a hard
    # ceiling on the whole system, so it is gated at 1.0.
    "figure_detection_recall": Gate(1.0),
    "table_detection_recall": Gate(1.0),
    "heading_recall": Gate(0.90),
    # Any Docling label the extractor does not know about silently becomes a
    # paragraph, so the only acceptable count is zero.
    "unmapped_label_count": Gate(0, direction="max"),
    # Enrichment cannot run on a figure whose crop was not captured.
    "figure_image_capture_rate": Gate(0.95),
    "bbox_presence_rate": Gate(0.95),
    "ocr_routing_accuracy": Gate(1.0),
}

# ── Stage 2: enrichment (captions) and index integrity ────────────────
STAGE2_INTEGRITY: dict[str, Gate] = {
    # Every stored image must resolve and decode, or answer-time hydration and
    # citation-to-image both break.
    "artifact_resolvable_rate": Gate(1.0),
    # An artifact with no chunk is a figure that was captured, captioned (or not)
    # and then silently dropped from the index. Highest-severity integrity check.
    "orphan_artifact_count": Gate(0, direction="max"),
    "media_chunk_id_derivation_rate": Gate(1.0),
    "media_atomicity_rate": Gate(1.0),
    "media_source_page_rate": Gate(0.95),
    "media_bbox_rate": Gate(0.95),
    "media_enrichment_model_rate": Gate(1.0),
    "empty_chunk_count": Gate(0, direction="max"),
    "duplicate_chunk_id_count": Gate(0, direction="max"),
    "text_orphan_rate": Gate(0.02, direction="max"),
    "fragmented_table_count": Gate(0, direction="max"),
    # Media chunks currently never get a parent_chunk_id, so parent expansion is
    # a no-op for figures. Reported so the gap stays visible; not gated until the
    # linkage is implemented, otherwise every run would fail on a known design
    # decision rather than on a regression.
    "media_parent_linkage_rate": Gate(1.0, gating=False),
    # clause_reference for media chunks is derived from caption text, so values
    # like "18.4" (a chart percentage) leak in. Tracked, not gated.
    "clause_reference_plausibility_rate": Gate(0.90, gating=False),
    "caption_truncation_count": Gate(0, direction="max", gating=False),
}

STAGE2_CAPTIONS: dict[str, Gate] = {
    # The money metric: every number printed on the figure must survive into the
    # caption, and the caption must not invent any.
    "caption_numeric_recall": Gate(0.98),
    "caption_numeric_hallucination_rate": Gate(0.02, direction="max"),
    "caption_cer": Gate(0.15, direction="max"),
    "illegible_marker_rate": Gate(0.10, direction="max", gating=False),
    "empty_caption_rate": Gate(0.0, direction="max"),
    # Coverage of the ground truth itself — a suite scoring 3 of 15 figures is
    # not passing, it is under-measured.
    "transcription_coverage": Gate(0.80, gating=False),
}

# ── Stage 3: retrieval ────────────────────────────────────────────────
STAGE3: dict[str, Gate] = {
    "recall_at_5": Gate(0.95),
    "recall_at_10": Gate(0.98),
    "mrr": Gate(0.80),
    "ndcg_at_10": Gate(0.80),
    # Figure-answerable questions are the multimodal-specific risk; held to the
    # same bar as text so a regression there cannot hide inside the average.
    "recall_at_5_figure": Gate(0.90),
    "recall_at_5_text": Gate(0.95),
    # Reranking a caption below a prose chunk is the classic cross-encoder
    # failure on this kind of corpus.
    "rerank_demotion_count": Gate(0, direction="max", gating=False),
    "rerank_drop_count": Gate(0, direction="max"),
    # The ablation reproduces the pipeline's final ordering locally; if that ever
    # stops matching production, every number in the stage is suspect.
    "pipeline_parity_rate": Gate(1.0),
}

# ── Stage 4: generation ───────────────────────────────────────────────
STAGE4: dict[str, Gate] = {
    "answer_numeric_recall": Gate(0.95),
    # Every quantity in an answer must trace back to a retrieved context block.
    "answer_numeric_groundedness": Gate(0.98),
    "judge_strict_pass_rate": Gate(0.85),
    "judge_lenient_pass_rate": Gate(0.95),
    "citation_presence_rate": Gate(0.95),
    "citation_validity_rate": Gate(1.0),
    "citation_support_rate": Gate(0.85, gating=False),
    # For a regulatory assistant, answering a question the corpus cannot support
    # is the most costly failure mode there is.
    "abstention_accuracy": Gate(1.0),
    "judge_unknown_rate": Gate(0.02, direction="max"),
}


ALL_GATES: dict[str, dict[str, Gate]] = {
    "stage1_extraction": STAGE1,
    "stage2_integrity": STAGE2_INTEGRITY,
    "stage2_captions": STAGE2_CAPTIONS,
    "stage3_retrieval": STAGE3,
    "stage4_generation": STAGE4,
}


def gate_for(stage: str, metric: str) -> Gate | None:
    return ALL_GATES.get(stage, {}).get(metric)
