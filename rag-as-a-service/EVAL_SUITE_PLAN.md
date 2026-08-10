# Stage-by-stage evaluation suite for the GERNAS multimodal RAG pipeline

## Context

Today's evaluation is a pile of independent scripts (`scripts/audit_tables.py`,
`verify_tables.py`, `verify_chunks.py`, `audit_figures.py`, `eval_gold_qa.py`,
`eval_pipeline_suite.py`, `eval_multimodal.py`, `eval_llm_judge.py`,
`run_evaluation.py`) plus a RAGAS-based `RAGEvaluator` used by the live
`/evaluate` API. Each is useful, but there's no single place that says "here
is every stage from PDF to answer, here is what passes at each one, here is
the bar." A failure surfaces as an end-to-end symptom (e.g. the RETRIEVAL
diagnoses already showing up in `eval_runs.md`) with no structured trail back
to *why* — was it extraction, indexing, image reading, retrieval ranking, or
generation?

The user supplied a reference design (`RAG_EVAL_README.md`) for exactly this:
a `python -m eval <stage>` CLI, five stages, deterministic scoring wherever
possible, LLM judgment only where a rule can't do the job, and one
`thresholds.py` file as the single reviewable source of every quality bar.
That design was written against a generic Docling+Qdrant RAG system; this
plan adapts it to how **this repo's** multimodal pipeline actually works
(confirmed by reading the source, not assumed):

- Extraction: `DoclingExtractor` (text/headings) + `DoclingImageExtractor`
  (figures via `doc.pictures`, tables via `doc.tables` + `RegionRenderer`
  crops — D8 dual representation).
- Index: two Qdrant collections (text chunks incl. `image_stub` chunks, and
  image vectors) plus a content-addressed `LocalAssetStore` on disk
  (`images/store.py`, id = sha256[:32] of image bytes via `make_asset_id`).
- **No offline captioning step exists.** `enrichment/` is an empty package.
  Image descriptors at index time (`images/captions.py::CaptionResolver`)
  are deterministic — Docling structural caption → regex "Figure N:" match →
  surrounding prose → nearest heading. The vision model
  (`llm.vision_model_name`, e.g. `qwen/qwen3.6-27b`) only reads actual pixels
  **at answer-generation time**, in `side_car` mode
  (`generation/generator.py`). This is the biggest divergence from the
  reference doc's Stage 2b, and the plan below adapts accordingly (see
  "Stage 2b adaptation" below) — confirmed with the user.
- Retrieval: `retrieval/multimodal_pipeline.py` (hybrid search + rerank +
  intent-gated image branch + table-crop promotion).
- Generation: `generation/generator.py`, citations as `[N]`/`[IN]`, ends
  with a `Sources:` block.

Decisions already confirmed with the user:
1. **Stage 2b** is reframed as *vision perception fidelity* — test the vision
   model's raw read of an isolated figure/table crop against a human
   transcription, decoupled from retrieval and from the full answer prompt.
2. **Ground truth**: scaffold auto-drafting for all 8 docs in `docs/`, then
   the assistant hand-verifies a pilot (the CBUAE circular) as a worked
   example; the rest ship `verified: false` for the user to review at their
   pace.
3. **Migration**: build the new `eval/` package as the source of truth,
   port the logic out of the old `scripts/eval_*.py` / `audit_*.py` /
   `verify_*.py`, delete those once subsumed. `src/gernas_rag/evaluation/`
   (`RAGEvaluator`) and the `/evaluate` API endpoint stay untouched — Stage
   4's RAGAS mode calls into `RAGEvaluator` rather than reimplementing it.

## Stage mapping (reference doc → this repo)

| Doc stage | This repo's equivalent | Absorbs (old script) | New ground truth |
|---|---|---|---|
| 1 — Extraction & Layout Fidelity | `DoclingExtractor` + `DoclingImageExtractor`, re-run over `docs/*.pdf` | `audit_figures.py` (partially) | `data/eval/layout_manifest.json` (new) |
| 2a — Index & Artifact Integrity | Qdrant text+image collections vs. `LocalAssetStore` on disk | `verify_chunks.py`, `audit_tables.py`, `verify_tables.py` | none — live-state cross-check |
| 2b — **Vision Perception Fidelity** (adapted) | Isolated call to `llm.vision_model_name` on one crop at a time | *(new capability)* | `data/eval/figure_transcriptions.json` (new) |
| 3 — Retrieval Quality & Ordering | `MultimodalRetrievalPipeline.retrieve()` | `eval_gold_qa.py`, `eval_pipeline_suite.py`, `eval_multimodal.py` | derives from `tests/fixtures/gold_qa.json` (existing) + runs `tests/fixtures/pipeline_suite.yaml` (existing) as named scenarios |
| 4 — Answer Quality | `ResponseGenerator.generate()`, judged 3 ways | `eval_llm_judge.py`, `run_evaluation.py` (via `RAGEvaluator`) | same `gold_qa.json`; abstention cases sourced from `pipeline_suite.yaml`'s existing "out-of-corpus" cases (`covers: "out-of-corpus; ..."`) — this is a genuinely useful find, they're unused for abstention scoring today |

`gold_qa.json` and `pipeline_suite.yaml` **stay in `tests/fixtures/`** —
they're actively used and referenced in `eval_runs.md`'s workflow; moving
them would be pure churn. Everything net-new (manifest, transcriptions,
run/report output) goes under `data/eval/`, matching the reference doc's
convention.

## File layout

```
eval/                              # top-level package (sibling to src/), like scripts/ but structured
  __init__.py
  __main__.py                      # `python -m eval <stage>` dispatch (list/stage1/2a/2b/3/4/all)
  core/
    thresholds.py                  # every pass/fail bar, one file, docstring-commented like metrics.py today
    report.py                      # shared JSON (data/eval/runs/<stage>.json) + Markdown (data/eval/reports/<stage>.md) writer
  common/
    numeric.py                     # deterministic quantity extractor/matcher — shared by stage 2b and stage 4
    gold.py                        # loads/validates tests/fixtures/gold_qa.json (ports validation from eval_gold_qa.py)
  stage1_extraction/
    manifest.py                    # LayoutManifest model + --init-manifest auto-draft
    check.py                       # re-extracts docs/*.pdf, matches manifest by page, scores recall/rates
  stage2a_index/
    check.py                       # ports verify_chunks.py + audit_tables.py + verify_tables.py logic into one report
  stage2b_vision/
    transcriptions.py              # FigureTranscription model + --init auto-draft
    check.py                       # calls vision LLM on isolated crops, diffs against verified transcriptions
  stage3_retrieval/
    qrels.py                       # derives stage3_qrels.json from gold_qa.json (ports eval_gold_qa.py's fact-containment grading)
    check.py                       # hit_rate/recall/mrr/precision/context_recall + runs pipeline_suite.yaml as named scenarios (ports eval_pipeline_suite.py, eval_multimodal.py)
  stage4_generation/
    deterministic.py               # numeric recall/groundedness, citation presence/validity/support, abstention
    judge.py                       # custom LLM judge (ports eval_llm_judge.py's prompt + RETRIEVAL/GENERATION/HALLUCINATION split)
    ragas_stage.py                 # thin adapter calling the existing RAGEvaluator
    check.py                       # orchestrates all three, single report

data/eval/
  layout_manifest.json             # curated, versioned (Stage 1 ground truth)
  figure_transcriptions.json       # curated, versioned (Stage 2b ground truth)
  runs/                            # machine-readable, gitignored (regenerated per run)
  reports/                         # human-readable markdown, gitignored (regenerated per run)
```

`eval/` is a plain top-level package like `scripts/`, not under `src/` — it's
dev tooling, not shipped product code (mirrors how `scripts/` is already
excluded from `[tool.hatch.build.targets.wheel] packages`). Since
`gernas_rag` is already editable-installed (`import gernas_rag` works from
anywhere — verified), `eval/` modules import it directly with no
`sys.path.insert` hacks, unlike the old flat scripts.

## Necessary source changes (not just new eval code)

- **`src/gernas_rag/extraction/docling_extractor.py`**: `_sync_extract`
  currently builds every `ExtractedElement` with `page_number=None` — it's
  never read off `item.prov`. Stage 1's heading-recall check needs page-level
  matching (same reasoning the reference doc gives for figures/tables: a
  heading detected on the wrong page should count as a miss). Fix: read
  `item.prov[0].page_no` the same way `docling_images.py::_page_of` already
  does, and pass it through. Low risk — purely additive metadata, existing
  consumers of `ExtractedElement.page_number` (`captions.py`) already assume
  it's populated and silently degrade to page-1-fallback when it isn't, so
  this is fixing a latent bug in the image-captioning context resolution
  too, not just enabling new eval code.
- No other production code changes needed. Stage 2b calls
  `llm/factory.py::get_llm` and the vision path already exposed by
  `generation/generator.py` — reused, not modified.

## Threshold source of truth

`eval/core/thresholds.py` supersedes `src/gernas_rag/evaluation/metrics.py`
as the reviewable list, but does **not** change `RAGEvaluator`'s behavior —
`ragas_stage.py` imports the thresholds it needs from the existing
`metrics.py` (kept in place, since `RAGEvaluator`/the `/evaluate` endpoint
still owns those), and `thresholds.py` re-exports them alongside the ~30 new
per-stage metrics from the reference doc, using the same numeric bars the
doc specifies (they're sensible: e.g. `hit_rate_at_5 >= 0.95` lines up with
this repo's existing `final_top_k: 5` in `config/default.yaml`).

## Ground truth curation (what the assistant does vs. what's left for the user)

1. Build `stage1_extraction/manifest.py --init-manifest`: runs
   `DoclingExtractor` + `DoclingImageExtractor` over all 8 PDFs in `docs/`,
   drafts `layout_manifest.json` with every figure/table/heading found,
   each entry `verified: false`.
2. Build `stage2b_vision/transcriptions.py --init`: for every figure/table
   crop in the (by-then-drafted) manifest, saves a stub entry in
   `figure_transcriptions.json` awaiting a human-typed transcription,
   `verified: false`.
3. The assistant hand-verifies one document end-to-end as a worked example —
   `CBUAE_Circular_2024_BSE_047_AI_Governance.pdf` (smallest, cleanest
   layout of the 8) — reading the actual PDF pages, correcting the drafted
   manifest entries, and typing real transcriptions for its figures/tables,
   flipping `verified: true`. This gives Stage 1 and Stage 2b at least one
   document with real, scored (not `n/a`) metrics, and shows the format for
   the user to continue the other 7.

## Verification plan

- `python -m eval` (no args) lists all stages.
- `python -m eval stage2a` and `python -m eval stage3` should run clean
  immediately (no new ground truth needed) — compare their output against
  today's `verify_chunks.py` / `audit_tables.py` / `eval_gold_qa.py` /
  `eval_pipeline_suite.py` runs to confirm the ported logic agrees on the
  same live index.
- `python -m eval stage1 --init-manifest` then `python -m eval stage1` —
  confirm the CBUAE doc reports real (non-n/a) recall numbers, other 7
  report `n/a (unverified)`.
- `python -m eval stage2b --init` then `python -m eval stage2b` — same
  n/a-until-verified check, confirm at least one live vision-model call
  succeeds end-to-end for the CBUAE doc's figures.
- `python -m eval stage4 --judge --ragas` — confirm it reproduces
  equivalent output to today's `eval_llm_judge.py` + `run_evaluation.py`
  against the same `gold_qa.json` cases already logged in `eval_runs.md`,
  plus confirm `abstention_accuracy` now scores (not `n/a`) using the
  `pipeline_suite.yaml` out-of-corpus cases.
- `python -m eval all` — runs stage2a → stage2b → stage3 → stage4 in order,
  confirm exit code 0/1 matches whether any required metric actually failed.
- Delete `scripts/audit_tables.py`, `verify_tables.py`, `verify_chunks.py`,
  `audit_figures.py`, `eval_gold_qa.py`, `eval_pipeline_suite.py`,
  `eval_multimodal.py`, `eval_llm_judge.py` only after their replacement
  stage reproduces equivalent output — `run_evaluation.py` and
  `diagnose_faithfulness.py` stay (still useful standalone debug tools
  wrapping `RAGEvaluator` directly).
- Add `data/eval/runs/` and `data/eval/reports/` to `.gitignore`.
