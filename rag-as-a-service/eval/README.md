# GERNAS RAG — stage-wise evaluation suite

Mirrors the ingest/serve pipeline one stage at a time so a regression can be
attributed to the stage that caused it, instead of only showing up as a lower
end-to-end score:

```
Stage 1            Stage 2a            Stage 2b            Stage 3             Stage 4
Extraction    -->   Index/artifact -->  Caption       -->   Retrieval     -->   Generation
(Docling)           integrity           fidelity (VLM)      (hybrid search)     (answer)
```

Every stage is invoked the same way, writes the same two artifacts, and returns
the same kind of exit code:

```
python -m eval <stage> [options]
```

writes:

- `data/eval/runs/<stage>.json` — machine-readable result
- `data/eval/reports/<stage>.md` — human-readable report
- exit code `0` = pass, `1` = a gated metric failed or an error finding was raised

## Stages

| Alias | Module | What it measures | Needs |
|---|---|---|---|
| `stage1` | `eval.stage1_extraction.run` | Did Docling find every figure/table/heading? | Source PDFs, a layout manifest |
| `stage2a` | `eval.stage2_enrichment.run_integrity` | Is every chunk stored, keyed and linked correctly? | Live Qdrant collection + artifact store |
| `stage2b` | `eval.stage2_enrichment.run_captions` | Are the VLM captions numerically correct? | Human transcriptions of each figure |
| `stage3` | `eval.stage3_retrieval.run` | Are the right chunks retrieved, in the right order? | Graded relevance judgments (qrels) |
| `stage4` | `eval.stage4_generation.run` | Is the answer grounded, correct, cited, appropriately silent? | Gold Q&A set |

`python -m eval` with no arguments lists all of this. `python -m eval all` runs
stage2a → stage2b → stage3 → stage4 in order (stage1 is excluded — it
re-extracts every PDF and takes minutes).

## Ground truth: bootstrap once, review by hand

Every stage that needs ground truth can scaffold a draft from a live run. A
scaffolded entry is written with `verified: false` and is **excluded from every
gated metric** — scoring the system against its own unreviewed output would be
a tautology. Review each draft against the actual source (PDF page, figure
image, retrieved chunk) and flip it to `verified: true`.

| File | Scaffolded by | What you review |
|---|---|---|
| `data/eval/layout_manifest.json` | `stage1 --init-manifest` | Every figure/table/heading Docling found, against the PDF |
| `data/eval/figure_transcriptions.json` | `stage2b --init` | Type out what's actually printed on each figure |
| `data/eval/qrels.json` | `stage3 --derive-qrels` | Prune false matches when a clause resolves to >1 chunk |
| `data/eval/gold_qa.json` | `scripts/build_gold_set.py` (pre-existing) | Question/answer pairs — already curated |

## Commands, in the order you'd normally run them

### Stage 1 — Extraction & layout fidelity

```bash
# First time: extract every doc, draft a manifest, review it
python -m eval stage1 --init-manifest
#   -> edit data/eval/layout_manifest.json by hand, set verified: true per document

# Look at what Docling actually cropped, if a metric looks wrong
python -m eval stage1 --dump-crops
#   -> data/eval/crops/<document>__<page>_<index>_<type>.png

# Normal run once the manifest has verified entries
python -m eval stage1
python -m eval stage1 --doc FAB_Credit_Pricing_Policy_v2_4   # one document only
python -m eval stage1 --no-images                            # skip rasterisation (faster)
```

**What happens:** builds a `DoclingExtractor` with the same `EnrichmentConfig`
ingestion uses, runs it over every PDF in `docs/`, and counts what came out —
figures/tables per page, headings, OCR-vs-text-layer routing, whether an
element's Docling label had no entry in `_LABEL_MAP` (which silently downgrades
a figure to an empty paragraph). Detected counts are matched **page by page**
against the manifest, so a figure detected on the wrong page still counts as a
miss.

**Metrics you'll see:** `figure_detection_recall`, `table_detection_recall`
(both gated at 1.0 — a miss here is invisible to every later stage),
`heading_recall`, `unmapped_label_count` (gated at 0), `ocr_routing_accuracy`,
`figure_image_capture_rate`, `bbox_presence_rate`.

**Runtime:** slow — Docling loads models and rasterises every page. Minutes for
the full corpus, first run slower (model download).

---

### Stage 2a — Index & artifact integrity

```bash
python -m eval stage2a
```

No `--init` step — it reads the live collection and artifact store directly,
no ground truth file needed.

**What happens:** loads every chunk from Qdrant (`ChunkIndex.load`), then:
- resolves every media chunk's `artifact_ref` through the real artifact store
  and verifies the bytes decode as an image
- scans the artifact store's local directory for images that exist on disk but
  that **no chunk references** — this is the check that catches a figure whose
  VLM caption failed, because the pipeline stores the image, sets
  `artifact_ref` regardless of caption success, and then the chunker drops the
  element for having empty text. Nothing else in the system would surface this.
- re-derives every media chunk id from `(document, modality, artifact_ref)` and
  checks it matches what's stored (this is what makes re-ingestion idempotent)
- checks parent/child linkage, empty chunks, duplicate ids, and whether a
  markdown table got cut across a chunk boundary (`fragmented_table_count`)

**Metrics:** `artifact_resolvable_rate`, `orphan_artifact_count` (gated at 0 —
highest-severity check in the suite), `media_chunk_id_derivation_rate`,
`text_orphan_rate`, `empty_chunk_count`, `duplicate_chunk_id_count`,
`fragmented_table_count`. Plus two informational (non-gating) metrics that
document known gaps: `media_parent_linkage_rate` (media chunks currently never
get a parent) and `clause_reference_plausibility_rate` (a media chunk's
`clause_reference` is derived from its caption text, so a chart value like
`18.4` can end up looking like a clause number).

**Runtime:** fast — no model loads, just reads the vector DB and disk.

---

### Stage 2b — VLM caption fidelity

```bash
# First time: scaffold ground truth and export the images to look at
python -m eval stage2b --init --export-images
#   -> data/eval/figures/<document>__p<page>__<hash>.png
#   -> edit data/eval/figure_transcriptions.json: type out every number/label
#      you see in each image, then set verified: true

# Normal run once some entries are verified
python -m eval stage2b
```

**What happens:** for every media chunk whose `artifact_ref` has a **verified**
transcription, extracts every quantity (`50 bps`, `AED 2.0 billion`,
`31-Dec-2024`, ...) from both the transcription and the indexed caption using a
deterministic numeric-fact parser (`eval/core/numeric.py`), and set-compares
them. No LLM judge is used here — it's pure regex/parsing, so it's free and has
zero run-to-run variance.

**Metrics:** `caption_numeric_recall` (share of printed numbers that survived
into the caption — gated ≥0.98), `caption_numeric_hallucination_rate` (numbers
in the caption that aren't on the image — gated ≤0.02), `caption_cer`
(character error rate on the full text), plus `transcription_coverage` telling
you how much of the ground truth you've actually filled in.

**Runtime:** fast — only reads the collection and artifact store. Coverage will
show `0` until you've transcribed at least one figure by hand.

---

### Stage 3 — Retrieval quality & ordering

```bash
# First time: bootstrap chunk-id judgments from the existing gold set
python -m eval stage3 --derive-qrels
#   -> data/eval/qrels.json — review entries flagged "ambiguous" (a gold
#      clause_reference matched more than one chunk); prune the wrong ones,
#      set verified: true per question

# Normal run (loads the embedder + reranker — this is the slow part)
python -m eval stage3
python -m eval stage3 --id 13,14,15     # only these questions; others kept as-is
python -m eval stage3 --limit 5         # quick smoke test
python -m eval stage3 --score-only      # re-score the last run, no retrieval (fast)
```

**What happens:** for each question, runs the retrieval pipeline **one
component at a time** — dense-only, sparse-only, RRF fusion, cross-encoder
rerank, freshness penalty — reusing the exact same searcher/reranker/freshness
objects `RetrievalPipeline` builds internally (so it's the deployed
configuration, not a re-implementation). After computing the "final" ordering
locally, it also calls the real `RetrievalPipeline.retrieve()` for the same
question and compares the two result sets — if they ever disagree,
`pipeline_parity_rate` drops below 1.0 and every other stage 3 number is
flagged as untrustworthy in the report, rather than silently drifting.

**Metrics on the served ("final") ordering:** `recall_at_5`, `recall_at_10`,
`mrr`, `ndcg_at_10` (the ordering-sensitive one — moves when a correct chunk
shifts rank, unlike recall). Split by answer modality:
`recall_at_5_figure` vs `recall_at_5_text` — this is the number that tells you
whether VLM captions compete with prose in the same embedding space.
`rerank_drop_count` / `rerank_demotion_count` count cases where the
cross-encoder pushed a known-relevant chunk down or out of the window entirely
— the classic failure mode when a caption doesn't read like natural prose.

**Report also includes (non-gating, diagnostic):** a per-stage ablation table
(recall/MRR/nDCG at each of dense/sparse/rrf/rerank/final, so you can see
*where* a drop happens), a rank-of-first-hit histogram, and a per-question
table.

**Runtime:** slow on first run (loads BGE-M3 embedder + cross-encoder
reranker), fast after that within the same process. `--score-only` re-runs the
scoring logic against the last saved run file with no model loading at all —
use this to iterate on qrels or metric thresholds.

---

### Stage 4 — Answer quality

```bash
# Generate answers + judge them + score
python -m eval stage4 --judge

# Iterate on scoring without re-generating (cheap)
python -m eval stage4 --score-only --judge

# Subsets
python -m eval stage4 --id 13,14,15
python -m eval stage4 --limit 5

# A/B: does image hydration actually change/improve answers?
python -m eval stage4 --hydration on  --run-name hydrated --judge
python -m eval stage4 --hydration off --run-name textonly --judge
python -m eval stage4 --score-only --run-name hydrated --compare-run textonly
```

**What happens:** builds the generator through
`gernas_rag.generation.factory.build_generator` — the **same function
`main.py`'s FastAPI lifespan calls** — so the artifact store and vision LLM are
wired exactly as production wires them. (This replaced the old scripts, which
constructed `ResponseGenerator` directly and silently ran text-only regardless
of the `hydration.enabled` setting — every historical gold-set number before
this change was measuring a different, text-only system.) For each question it
retrieves, generates, and records a `GenerationTrace` — whether hydration was
eligible, how many images actually got resolved, whether the vision LLM
answered — so "did the multimodal path even run" is visible instead of
inferred.

Scoring runs four independent checks:
1. **Numeric recall/groundedness** — deterministic, same fact-extraction as
   stage 2b. Recall = did the answer restate the gold quantities; groundedness
   = does every quantity in the answer trace back to a retrieved context block
   (substituting the verified human transcription for a media block's caption
   where one exists in `figure_transcriptions.json` — otherwise a caption error
   would be invisible here too).
2. **LLM judge** (only with `--judge`) — CORRECT / PARTIALLY_CORRECT /
   INCORRECT, judged independently and cached to
   `data/eval/runs/stage4_generation_judgments_<run-name>.json` so re-judging
   never requires re-generating.
3. **Citation checks** — parses `[N]` markers, verifies `N` is a real context
   index (`citation_validity_rate`), and a coarse check that the cited block
   actually shares the claimed number or wording (`citation_support_rate`,
   non-gating).
4. **Abstention** — for any gold question marked `"answerable": false`, checks
   the answer looks like a refusal rather than a fabrication
   (`abstention_accuracy`, gated at 1.0). The current `gold_qa.json` has none,
   so this reports `n/a` with a warning until you add some.

**Metrics:** `answer_numeric_recall`, `answer_numeric_groundedness`,
`judge_strict_pass_rate` / `judge_lenient_pass_rate` /`judge_unknown_rate`,
`citation_presence_rate`, `citation_validity_rate`, `abstention_accuracy`. Plus
a latency table (p50/p95, retrieval vs generation) and a hydration summary
(`images_hydrated`, `answers_via_vision_llm`).

**Runtime:** slow (embedder + vector DB + LLM per question); `--judge` adds one
LLM call per question, bounded by `--judge-concurrency` (default 3).

---

## How to read a report

Every stage produces the same shape. Open the `.md` file (or read the `.json`
for tooling) — real excerpts below, from `data/eval/reports/stage2_integrity.md`:

**Header + verdict:**
```
**Verdict: FAIL**
14 metric(s) | 1 failing gate(s) | 1 error(s) | 3 warning(s)
```
`FAIL` means at least one *gating* metric missed its threshold, or an
error-level finding was raised. Exit code follows this (`1` = FAIL).

**Metrics table:**
```
| Metric                          | Value  | Gate      | Status              |
|----------------------------------|--------|-----------|---------------------|
| artifact_resolvable_rate         | 1      | >= 1      | PASS                |
| media_parent_linkage_rate        | 0      | >= 1      | FAIL (not gating)   |
| caption_numeric_recall           | -      | >= 0.9800 | n/a                 |
```
- **Value `-` / Status `n/a`** = no data existed to compute the metric (e.g. no
  verified transcriptions yet). This is deliberately different from a failing
  score — "unmeasured" must never silently read as "passed" or "failed".
- **`FAIL (not gating)`** = the metric is genuinely failing but doesn't affect
  the pass/fail verdict or exit code. Used for metrics that describe a known,
  accepted design gap (documented in `eval/core/thresholds.py`) rather than a
  regression — e.g. media chunks having no parent is a real limitation, but
  it's not something you want blocking CI until you decide to fix it.
- Everything else gates: **PASS**/**FAIL** with no suffix count toward the
  verdict.

**Findings table** — the actionable detail behind a failing metric:
```
| Severity | Code               | Subject         | Message |
|----------|--------------------|-----------------| ... |
| ERROR    | TABLE_FRAGMENTED   | 4701c066...      | ... chunk contains markdown table rows with no header separator ... |
| WARN     | CAPTION_TRUNCATED  | 4841d77f...      | ... caption ends on a dangling separator ... raise enrichment.max_tokens ... |
```
`code` is stable and greppable — search for it across reports/runs to track a
specific defect over time. `subject` is almost always a `chunk_id` or
`artifact_ref` you can look up directly in Qdrant or the artifact store.
Severity: `error` (breaks something, always fails the run), `warn` (a real
issue but not release-blocking by itself), `info` (FYI, e.g. a small chunk).

**Supporting tables** (composition, ablation, per-question) are for orientation
and diagnosis — never gate anything themselves, they explain the metrics above
them.

**Exit code strictness** — every command accepts `--fail-on`:
- `gate` (default) — fail on a gating metric miss or an error finding
- `error` — only error findings fail the run (ignore metric thresholds)
- `warn` — warnings fail too (useful for a stricter nightly job)
- `never` — always exit 0, for exploratory local runs

## Current status in this repo (from the runs already done)

- Stage 2a/2b have been run against the live collection: 91 chunks, 14 media.
  Stage 2a currently **FAILs** on one real defect
  (`FAB_Credit_Pricing_Policy_v2_4`, a table split across a chunk boundary with
  no header) — see `data/eval/reports/stage2_integrity.md`.
- Stage 2b's ground truth was scaffolded (`--init`) but nothing is transcribed
  yet, so caption fidelity reports `n/a` until you fill in
  `data/eval/figure_transcriptions.json`.
- `data/eval/qrels.json` was derived from the 34-question gold set; **17 of 34
  questions were flagged `ambiguous`** (their `clause_reference` matched more
  than one chunk — one matched 7). Review these before trusting stage 3
  numbers; they are excluded from nothing automatically, they're just marked.
- Stage 1, stage 3 (full retrieval run) and stage 4 have not been executed yet
  in this repo — run the commands above when ready.
