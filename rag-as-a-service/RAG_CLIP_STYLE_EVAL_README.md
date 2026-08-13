# GERNAS RAG — Evaluation Suite

## What This Document Covers

This is the evaluation framework for the GERNAS multimodal Retrieval-Augmented
Generation (RAG) pipeline in `src/gernas_rag/`. A RAG system answers questions
by first *retrieving* relevant passages (and, in this pipeline, figures) from a
document collection and then using a language model to *generate* an answer
grounded in that context. Because several distinct steps happen between "here
is a PDF" and "here is an answer," a failure can originate almost anywhere in
that chain — and this suite exists to identify exactly where.

This suite lives at `eval/` (a plain top-level package, run as `python -m eval`) and is separate from `src/gernas_rag/evaluation/` (`RAGEvaluator`),
which backs the live `POST /api/v1/evaluate` endpoint and runs RAGAS metrics
over the text pipeline only. The two do not overlap in scope — see
[Stage 4](#stage-4--answer-quality-cs--response-generation-hallucination) for
exactly how they relate.

## Purpose

Rather than producing a single end-to-end score, this suite evaluates the
pipeline **stage by stage**, and frames each stage using the failure taxonomy
from the **RAG-Check** paper: a wrong answer is either a *selection*
failure (the right passage existed but was never retrieved), a
*context-generation* failure (a figure was retrieved but the vision model
misread its pixels), or a *response-generation* failure (the right context
reached the model, but the answer still got it wrong). Scoring these
independently means a quality regression can be traced back to its root
cause without manual debugging.

Every stage is built on three principles:

- **Deterministic scoring wherever possible.** Numeric-fact matching,
  structural cross-checks, and regex-based classification produce the same
  result every time and cost nothing to run.
- **Language-model judgment only where a rule-based check cannot do the
  job** — and even then, per objective *statement*, not once per answer, so
  one wrong clause in an otherwise-correct answer can't hide inside a
  passing grade.
- **Every quality bar is documented and reviewable** in one file
  (`eval/core/thresholds.py`), each with a one-line reason for why that
  specific number was chosen. Raising or lowering a bar is a visible,
  deliberate decision, not a silent change.

---

## Table of Contents

1. [Pipeline Overview](#pipeline-overview)
2. [How to Run](#how-to-run)
3. [Ground Truth Data](#ground-truth-data)
4. [Stage 1 — Extraction &amp; Layout Fidelity](#stage-1--extraction--layout-fidelity)
5. [Stage 2a — Index &amp; Artifact Integrity](#stage-2a--index--artifact-integrity)
6. [Stage 2b — Vision Perception Fidelity](#stage-2b--vision-perception-fidelity)
7. [Stage 3 — Retrieval Quality &amp; Ordering](#stage-3--retrieval-quality--ordering-rs--selection-hallucination)
8. [Stage 4 — Answer Quality](#stage-4--answer-quality-cs--response-generation-hallucination)
9. [Reading a Report](#reading-a-report)
10. [Metrics Glossary](#metrics-glossary--in-one-place)

---

## Pipeline Overview

The pipeline moves a document through five stages before it can answer a
question — the same five components documented in `SYSTEM_ARCHITECTURE.md`,
each checked independently here:

```
Stage 1            Stage 2a              Stage 2b              Stage 3             Stage 4
Extraction    -->   Index/artifact  -->   Vision perception --> Retrieval     -->   Answer
(Docling)           integrity             (isolated crop,       (hybrid           quality
                                           answer-time only)     search)           (judged)
```

| Stage                   | What It Checks                                                                                                                                | What It's Measured Against                                                             |
| ----------------------- | --------------------------------------------------------------------------------------------------------------------------------------------- | -------------------------------------------------------------------------------------- |
| 1 — Extraction         | Does`DoclingExtractor` + `DoclingImageExtractor` find every figure, table, and heading in the source PDFs, on the right page?             | Source PDFs plus a hand-reviewed layout manifest                                       |
| 2a — Index Integrity   | Is every chunk and image vector stored correctly, keyed idempotently, and cross-linked to its asset?                                          | The live Qdrant collections and the on-disk asset store — no ground-truth file needed |
| 2b — Vision Perception | Reading one isolated figure/table crop at exactly the resolution production sends it, does the vision model get the numbers and labels right? | Human transcriptions of each crop                                                      |
| 3 — Retrieval          | Does the live hybrid-search pipeline surface the passage that actually answers the question, and rank it near the top?                        | `tests/fixtures/gold_qa.json`, plus the live Qdrant index                            |
| 4 — Generation         | Is the final answer correct, grounded, cited, and appropriately silent when the corpus can't support one?                                     | The same`gold_qa.json`, judged by a model independent of both generators             |

> **This is not a captioning pipeline.** There is no offline "caption every
> figure at ingest time" step to evaluate — `enrichment/` in the source tree
> is an empty package. Image descriptors written at ingest are deterministic
> (structural caption → regex → surrounding prose → nearest heading); the
> vision model only reads actual pixels **at answer-generation time**, when a
> figure is retrieved and vision is enabled. Stage 2b is scoped to exactly
> that: one isolated call to the vision model on one crop, decoupled from
> retrieval and from the full answer prompt.

Every stage produces the same three outputs:

```bash
python -m eval <stage> [options]
```

| Output                  | Location                         | Purpose                                                                                                                      |
| ----------------------- | -------------------------------- | ---------------------------------------------------------------------------------------------------------------------------- |
| Machine-readable result | `data/eval/runs/<stage>.json`  | Automated tooling, diffing between runs                                                                                      |
| Human-readable report   | `data/eval/reports/<stage>.md` | The file to read when reviewing results                                                                                      |
| Exit code               | `0` or `1`                   | `0` = every required metric passed (or reported n/a); `1` = a required metric scored below its bar, or the stage errored |

---

## How to Run

```bash
python -m eval                       # list all stages and their metric counts
python -m eval stage1                # extraction & layout fidelity
python -m eval stage1 --only doc_a,doc_b   # score only named documents
python -m eval stage2a               # index & artifact integrity (fast, no models)
python -m eval stage2b               # vision perception fidelity
python -m eval stage2b --limit 5     # score only 5 more crops this run
python -m eval stage2b --rescore     # recompute scores from stored readings, no API calls
python -m eval stage3                # retrieval quality
python -m eval stage3 --top-k 5      # override retrieval.final_top_k for this run
python -m eval stage4 --judge-model llama-3.3-70b-versatile
python -m eval stage4 --reuse-retrieval --top-k 5   # reuse stage 3's stored context
python -m eval all                   # every stage, each in its OWN process, in order

python -m eval repair --dry-run      # preview mojibake fixes to the ground truth
python -m eval remap --dry-run       # preview binding transcriptions to this pipeline's asset ids
```

> **Stage 4 has a hard footgun.** The shipped `evaluation.judge_model`
> (`config/default.yaml`) is `openai/gpt-oss-120b` — the **same model** as
> `llm.model_name`, the default text generator. Running `python -m eval stage4` with no override therefore hits `judge.JudgeCollisionError` and
> refuses to run: a model grading its own output would inflate every score
> with no visible failure, so this is a hard stop, not a warning. Always pass
> `--judge-model llama-3.3-70b-versatile` (the module's built-in
> `DEFAULT_JUDGE`) or set a distinct `RAG__EVALUATION__JUDGE_MODEL`.

`all` runs **every implemented stage, including stage 1**, each as a
**separate subprocess** rather than a loop in one interpreter — deliberately.
Stage 1 holds Docling's layout models in memory; stages 2a/3/4 add BGE-M3,
SigLIP-2, and the cross-encoder reranker on top.`table_recall` — a CI gate
that quietly degrades under memory pressure is worse than one that fails
loudly, because it reports a defect that does not exist. One process per
stage keeps each stage's peak memory independent.

`--delay` (default `1.0`s) paces every real LLM call in stages 2b and 4 —
raise it if you're on a rate-limited tier.

---

## Ground Truth Data

"Ground truth" means the small set of curated, human-reviewed reference files
every metric in this suite is scored against. Nothing is ever scored against
the system's own output.

| File                           | Where It Lives      | Contents                                                       | What a Reviewer Checks                                                                                       |
| ------------------------------ | ------------------- | -------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------ |
| `layout_manifest.json`       | `data/eval/`      | 8 documents, all`verified: true`                             | Every figure, table, and heading the extraction engine should find, per page, checked against the source PDF |
| `figure_transcriptions.json` | `data/eval/`      | 27 items, all`verified: true`, 25 bound to an `asset_id`   | Every number and label visible on each figure/table crop, transcribed by hand                                |
| `gold_qa.json`               | `tests/fixtures/` | 37 curated cases — 34 answerable, 3 deliberately unanswerable | Question / expected-answer / expected-source triples                                                         |


**Every auto-drafted entry starts `verified: false` and is excluded from
every scored metric until a human reviews it** — scoring the system against
its own unreviewed output would be circular, since the system generated that
"ground truth" itself. All entries currently ship pre-verified, but any newly
drafted document or crop follows the same rule.

**Stage 3 has no ground-truth file of its own.** Relevance is calculated on
every run from `gold_qa.json`'s `expected_answer` via a deterministic
quantity/date/reference extractor (`eval/common/gold.py`) — a retrieved
passage is graded relevant when it contains those specific facts, never by
whether the retriever happened to rank it first. That means it cannot be
inflated by a retrieval run grading itself: the facts are fixed before
retrieval ever runs.

**Stage 3 also grades on document + fact containment, deliberately not on
`section_heading` or `clause_reference`.** Measured against the live index,
`section_heading` collapses to 16 distinct values across 117 chunks (one
value alone covers a third of the corpus) and `clause_reference` includes
synthetic values like `figure_p3`. Both collide far too heavily to identify a
specific chunk, so grading on either would measure the metadata, not the
retrieval.

**Stage 4 uses `gold_qa.json` as its only reference too** — the same
`expected_answer` field, scored three different ways (deterministic checks
and a custom LLM judge; see below).

---

## Stage 1 — Extraction & Layout Fidelity

**What it checks:** whether Docling — via `DoclingExtractor` for text and
`DoclingImageExtractor` for figures/tables — finds every figure, table, and
heading in the source PDFs, on the correct page. A miss here is invisible to
every later stage: a figure Docling never detects can never be embedded,
retrieved, or read by the vision model.

**Method:** Re-runs the real extractors, sharing one instance across
documents (rebuilding one per document reloads Docling's layout models every
time, which exhausts memory on a corpus this size). Detected figures and
tables are matched **page by page** against the manifest — a heading or
figure detected on the wrong page still counts as a miss, because the caption
resolver (`images/captions.py`) locates an image's nearest heading **on its
own page**; a wrong page silently produces a wrong caption downstream.
Headings are read from `raw_markdown`'s `#` markers, not from
`extraction.elements` — that markdown is literally what
`HierarchicalChunker` splits on, so scoring anything else would measure a
field the pipeline never actually consumes.

### Metrics

| Metric             | What It Measures                                                                              | Passing Threshold  | Why It Matters                                                                                                                                         |
| ------------------ | --------------------------------------------------------------------------------------------- | ------------------ | ------------------------------------------------------------------------------------------------------------------------------------------------------ |
| `heading_recall` | Manifest headings matched (substring-tolerant, normalized text) ÷ headings expected          | ≥ 0.90 (required) | Headings drive`HierarchicalChunker`'s parent/child split — a missed heading gives every chunk beneath it the wrong parent.                          |
| `figure_recall`  | Manifest figures matched by page ÷ figures expected                                          | ≥ 0.95 (required) | Figures are the whole point of the multimodal path — one Docling never sees is unreachable by every later stage.                                      |
| `table_recall`   | Same calculation, for tables                                                                  | ≥ 0.95 (required) | Tables carry the dense numeric policy content most gold questions ask about.                                                                           |
| `page_accuracy`  | Reported alongside`figure_recall` — page-level matching is already folded into that number | ≥ 0.95 (required) | A right-count-wrong-page detection is already counted as a miss by`figure_recall`; reporting it again separately would double-count the same signal. |

A manifest entry can mark a figure region `must_detect: false` — a region
that genuinely exists on the page but that this pipeline's image filters
(min size, blankness, aspect ratio) are *correct* to drop, such as a
signature scribble. Those regions are excluded from `figure_recall`'s
denominator but still listed, so the manifest stays a faithful description of
the page — and the report surfaces the excluded count per document so a
relaxed bar is never invisible.

**Runtime:** Slow — Docling loads its layout models and processes every page
of every document (minutes for the full 8-document set, longer on the first
run while models download).

---

## Stage 2a — Index & Artifact Integrity

**What it checks:** whether every chunk in the live vector index is stored
correctly, keyed in a way that survives re-ingestion, and correctly
cross-linked between the two Qdrant collections and the on-disk asset store.
**No ground-truth file is needed** — every check here is a structural
invariant, true by construction if the pipeline is working. A violation is a
bug by definition, which makes this the fastest stage to run and the first
one worth checking when something downstream looks strange.

**Method:** Scrolls every point out of the text collection and (when
multimodal is enabled) the image collection, then cross-checks both against
the on-disk asset store and against each other.

### Metrics

| Metric                    | What It Measures                                                                                     | Passing Threshold                       | Why It Matters                                                                                                                                                                                 |
| ------------------------- | ---------------------------------------------------------------------------------------------------- | --------------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `asset_resolvable_rate` | Indexed image vectors whose bytes actually exist on disk ÷ indexed image vectors                    | ≥ 1.00 (required)                      | A vector with no backing file is a 404 to the user the moment that image is retrieved.                                                                                                         |
| `stub_coverage`         | Indexed images with a matching`image_stub` chunk in the text collection ÷ indexed images          | ≥ 0.95 (required)                      | Every image should be reachable by BM25 through its caption stub, not only by the dense image tower — see`SYSTEM_ARCHITECTURE.md` §5.2 for why the two paths are not interchangeable.      |
| `table_atomicity`       | Table chunks that still carry a markdown header row (pipes + a dash separator) ÷ total table chunks | ≥ 1.00 (required)                      | A table row without its header is unlabelled numbers to the LLM — exactly how a 260 bps floor gets attributed to the wrong rating.                                                            |
| `orphan_rate`           | Assets on disk with no vector pointing at them ÷ assets on disk                                     | ≤ 0.10 (**watch, not required**) | A ceiling, not a floor. Some drift is normal after a re-ingest that changed chunk boundaries; a*large* figure means the reconciliation step (`vectordb.reconcile_document`) isn't running. |

If `multimodal.enabled` is `false`, the image-related metrics report `n/a` —
there is no image collection to check.

**Runtime:** Fast — no models are loaded; this stage only reads Qdrant and
disk.

---

## Stage 2b — Vision Perception Fidelity

**What it checks:** whether the vision model, reading one isolated figure or
table crop, correctly reads the numbers and labels actually printed on it —
decoupled entirely from retrieval and from the full answer prompt. This is
RAG-Check's *context-generation-hallucination*, isolated to the one place it
can originate: the model looking at pixels and getting them wrong.

**Why isolate it:** a chart legible on a full page can become sub-pixel once
downscaled to `vision_image_max_side_px` for the vision prompt. A retrieval
run that logs that failure as "the model didn't know" is measuring the wrong
stage — no retrieval fix and no prompt change corrects a value the model
literally could not read. This is also the stage that tells you what
resolution *is* enough, since it evaluates the crop at the exact size and
format the real answer path (`ImagePayloadBuilder`) would send it — a pass
here means the production pipeline can actually read that figure, not merely
that some larger version of it was legible.

**Method:** For each verified transcription with a resolved `asset_id`, the
real `ImagePayloadBuilder` builds the same payload production would send,
and one isolated prompt asks the model to transcribe — not interpret — every
title, axis label, tick value, legend entry, and data value it can read,
writing `illegible` rather than guessing. The reading is compared against
the human transcription with a deterministic quantity extractor
(`eval/common/numeric.py`), shared with Stage 4.

**A deliberate override, not a bug:** production's `vision_fallback_to_text`
degrades gracefully to a text-only answer on a rate limit, which is correct
in production. In this stage it is disabled — the router would otherwise
silently strip the image and "transcribe" a figure the model never saw,
returning what looks like a normal reading. Measured on a real rate-limited
run, those fabricated readings scored `entity_recall` 0.02–0.12 (against
0.36–0.88 for genuine reads) while being checkpointed as clean successes.
Fabricated data is worse than a visible error, so a rate limit here raises
instead of degrading.

### Metrics

| Metric                  | What It Measures                                                                                                                                | Passing Threshold  | Why It Matters                                                                                                                                                    |
| ----------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------- | ------------------ | ----------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `numeric_fidelity`    | Transcription's numbers that also appear in the model's reading ÷ transcription's numbers, over crops that contain numbers                     | ≥ 0.85 (required) | The single most load-bearing check in this stage — a chart value misread at low resolution is failure mode#1 for this pipeline, isolated from every other cause. |
| `entity_recall`       | Non-numeric content words (labels, legend entries, axis names) recovered ÷ words expected                                                      | ≥ 0.80 (required) | A model can get every number right and still attach it to the wrong series or axis.                                                                               |
| `no_fabrication_rate` | 1 − (quantities in the reading that are*not* in the transcription, and not accounted for by a described range, ÷ quantities in the reading) | ≥ 0.95 (required) | An invented value is worse than a missing one — it produces a confidently wrong, seemingly-well-read answer.                                                     |

Two scoring details worth knowing before reading a low score as real: bare
small integers (≤ 12, no unit, no decimal) are treated as list-numbering
noise rather than fabricated data — vision models routinely answer in
numbered lists — and a transcription range like "ticks 90–160" absorbs every
individual tick the model enumerates within it, rather than penalising each
one as invented.

**Checkpointed, resumable, and rescoreable without new API calls.** Every
crop's raw reading is persisted (not just its score) to
`data/eval/runs/stage2b_cases.jsonl`, flushed after each call. A run that
hits a quota wall loses nothing already paid for — re-running the same
command skips completed crops and continues; `--fresh` discards the
checkpoint and starts over; `--rescore` recomputes every score from the
already-stored readings with **zero API calls**, which is the path to take
after fixing a scorer bug (it has already happened twice).

**Runtime:** One real LLM call per crop, paced by `--delay` (default 1s). All
metrics report `n/a` if `llm.vision_enabled` is `false`, or if no
transcription has both `verified: true` and a resolved `asset_id` — run
`python -m eval remap` first if you see the latter.

---

## Stage 3 — Retrieval Quality & Ordering (RS / selection-hallucination)

**What it checks:** for each gold question, does the **real**
`MultimodalRetrievalPipeline` — the same hybrid search, the same reranker,
the same freshness and image-gating logic the live `/retrieve` endpoint uses
— surface a chunk from the expected document, and rank it near the top?

**Method:** Builds the production pipeline exactly (`build_pipeline()` in
`eval/stage3_retrieval/check.py` — deliberately not a simplified stand-in),
runs every gold case through it at `retrieval.final_top_k` (overridable with
`--top-k`), and grades two things per case: whether the first chunk from an
expected document appears at all (and at what rank), and how many of the
gold answer's individual facts show up anywhere in everything retrieved —
text chunks **and** image captions together, since that is exactly what the
generator actually sees.

**Modality is reported, never scored.** A gold case may mark its source as
`figure`, but this pipeline may legitimately answer it with a table-text
chunk instead (the D8 dual representation — the same table rendered as both
a text chunk and an image crop). That is a *better* outcome, since the
values become lexically searchable — penalising it would punish the pipeline
for doing the right thing. Modality mismatches are surfaced in the per-case
detail table for visibility, never subtracted from a score.

### Metrics

| Metric                   | What It Measures                                                                                                        | Passing Threshold                                | Why It Matters                                                                                                                                                                                                                                                                                                                                          |
| ------------------------ | ----------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------ | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `hit_rate_at_k`        | Questions with a chunk from an expected document anywhere in the top k ÷ all questions                                 | ≥ 0.95 (required)                               | If retrieval doesn't surface the right document at all, generation has no chance regardless of how good the model is.                                                                                                                                                                                                                                   |
| `recall_at_k`          | Gold facts present anywhere in the retrieved context (text + image captions) ÷ total gold facts, averaged per question | ≥ 0.85 (required)                               | Lower than`hit_rate_at_k` on purpose: a multi-fact answer needs every supporting fact, not just a hit on the right document.                                                                                                                                                                                                                          |
| `mrr`                  | Mean of 1 ÷ (rank of the first chunk from an expected document)                                                        | ≥ 0.80 (required)                               | Rewards ranking the right passage first, not fifth — the generator's context assembly weights earlier chunks more.                                                                                                                                                                                                                                     |
| `context_precision`    | Retrieved chunks from an expected document ÷ chunks retrieved, averaged per question                                   | ≥ 0.70 (required)                               | Irrelevant chunks crowd out the token budget the generator has to work with.                                                                                                                                                                                                                                                                            |
| `image_relevancy`      | *(RS-proxy for the image branch)*                                                                                     | ≥ 0.70 (**watch — not yet implemented**) | Plain SigLIP-2 cosine similarity aligns with human relevance judgments only moderately well by published benchmarks, versus a trained cross-attention scorer. The text path already closes this gap with`bge-reranker-v2-m3`; the image path does not, so this metric needs a judge pass before it can be trusted — it always reports `n/a` today. |
| `score_gate_agreement` | Would validate the image score-gate's 0.10 floor / 0.55 margin against human relevance                                  | ≥ 0.75 (**watch — not yet implemented**) | Depends on`image_relevancy` existing first; always reports `n/a` today.                                                                                                                                                                                                                                                                             |

**Retrieved context is persisted, not just scored.** Every case's actual
retrieved chunks and images are written to
`data/eval/runs/stage3_retrieval_cases.jsonl` (cleared and rebuilt fresh on
every run — a report always reflects one configuration, never a mix of two).
Stage 4's `--reuse-retrieval` reads this file directly, which saves ~25
seconds of local model work per case *and* guarantees both stages scored the
exact same context, so a "stage 3 passed but stage 4 failed" reading is
provably a generation problem rather than a probable one.

**Runtime:** Slow on a cold process (loads BGE-M3, SigLIP-2, and the
cross-encoder reranker), fast on repeated runs within the same session.

---

## Stage 4 — Answer Quality (CS / response-generation-hallucination)

**What it checks:** is the final generated answer correct, grounded in what
was actually retrieved, properly cited, and correctly silent on questions the
corpus can't answer? Runs the exact production code path
(`ResponseGenerator.generate()`), so results reflect real-world behaviour,
not a simplified stand-in.

**Method:** For each gold case, retrieves (or reuses Stage 3's stored
context — see above) and generates a real answer, then scores it two ways,
cheapest first:

1. **Deterministic checks** (`eval/stage4_generation/deterministic.py`) —
   citation-pointer validity, numeric-fact recall, abstention correctness.
   No LLM cost, no run-to-run variance.
2. **A custom LLM judge** (`eval/stage4_generation/judge.py`) — scored
   **per objective statement**, not once per answer. The answer is first
   split into atomic spans (`spans.py`) and each is classified subjective or
   objective by a **rule-based classifier**, not a model call — the source
   research found rules matched human labelling better than asking a
   general-purpose model to do it. A statement like *"the fee is likely
   around 50 bps"* is a hedge, not a checkable claim, and scoring it
   correct-or-incorrect is a category error; subjective spans are excluded
   from correctness and reported only as their own ratio, since a generator
   that hedges everything would otherwise score suspiciously well.



**Judge independence is enforced, not assumed.** `judge.build_judge()`
refuses to run at all if the resolved judge model is also a generator
(`llm.model_name` or, when vision is on, `llm.vision_model_name`) — see the
footgun callout in [How to Run](#how-to-run). This is a hard stop rather than
a degraded run: scores from a self-grading judge look fine and mean nothing,
which is worse than no scores.

### Metrics

| Metric                    | What It Measures                                                                                                                                                                                                                                     | Passing Threshold                                                     | Why It Matters                                                                                                                                                                                                                                                                         |
| ------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | --------------------------------------------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `answer_correctness`    | Answers the judge grades exactly`2` (matches gold on every material fact) ÷ answerable cases judged                                                                                                                                               | ≥ 0.85 (required)                                                    | The end-to-end correctness bar — no partial credit for a`1` ("partially correct").                                                                                                                                                                                                  |
| `span_correctness`      | Per-objective-span support ÷ objective spans, over answerable cases                                                                                                                                                                                 | ≥ 0.85 (required)                                                    | Strictly harder than whole-answer correctness: one wrong clause inside an otherwise-right answer is caught here instead of averaging away.                                                                                                                                             |
| `groundedness`          | **Computed from the same span-support numbers as `span_correctness`** — reported as its own row because it answers a distinct question (is every claim traceable to context?) even though the current implementation derives it identically | ≥ 0.95 (required)                                                    | Asserting a fact absent from the retrieved context is the cardinal RAG sin.**Note:** because both metrics currently share one calculation, expect them to always report the same value in a run's report — that is not a bug in the report, it reflects today's implementation. |
| `answer_relevancy`      | Answers the judge marks as addressing the question asked ÷ answerable cases judged                                                                                                                                                                  | ≥ 0.85 (required)                                                    | An answer can be factually correct and still fail this by answering a different question than the one asked.                                                                                                                                                                           |
| `citation_validity`     | `[N]` / `[IN]` references that resolve to something actually retrieved ÷ total references in the answer body                                                                                                                                    | ≥ 1.00 (required)                                                    | A citation pointing at nothing retrieved destroys auditability — the user clicks it and finds nothing. Purely structural: this does not ask whether the cited chunk*supports* the claim.                                                                                            |
| `citation_support`      | *(Would measure whether a cited chunk actually supports its adjacent claim)*                                                                                                                                                                       | ≥ 0.90 (**required on paper — always reports `n/a` today**) | Needs per-citation judging, which is not yet implemented. Because`n/a` never fails a run (§[Reading a Report](#reading-a-report)), this required bar currently cannot fail *or* pass — it is a placeholder, not a live gate.                                                      |
| `numeric_recall`        | Gold-answer quantities restated in the generated answer ÷ gold-answer quantities, over cases with numbers                                                                                                                                           | ≥ 0.90 (required)                                                    | Policy answers are numbers; a missing bps figure is a wrong answer even if the surrounding prose is correct.                                                                                                                                                                           |
| `abstention_accuracy`   | Unanswerable gold cases correctly declined (matched against a fixed list of refusal phrases) ÷ unanswerable cases                                                                                                                                   | ≥ 1.00 (required)                                                    | Fabricating an answer the corpus can't support is the single most costly failure mode for a system used in a regulatory context.                                                                                                                                                       |
| `subjective_span_ratio` | Subjective spans ÷ total spans                                                                                                                                                                                                                      | ≤ 0.30 (**watch, not required**)                               | A ceiling: a generator that hedges every statement scores artificially well on groundedness while telling the user nothing useful.                                                                                                                                                     |


**Checkpointed and resumable, same mechanism as Stage 2b.** ~3 API calls per
case means a quota wall is a question of when, not if, on a free tier; every
completed case is flushed to `data/eval/runs/stage4_cases.jsonl` immediately,
so a crash or a 429 costs nothing already paid for. `--limit` after a
checkpoint means "N *more* cases this run," not "the first N" — it's applied
after already-done cases are filtered out, so repeated runs advance rather
than re-requesting the same cases forever.

**`--generate-top-k` models a real production constraint.** Retrieval depth
and generation depth are tracked separately: a case can retrieve the right
chunk at rank 3 while the model is only ever shown the first 2 (a context or
vision token budget), and the report distinguishes that as a budget problem,
not a retrieval or generation defect — both `retrieved` and `generated_with`
depths are recorded per case.


**Runtime:** ~3 LLM calls per answerable case (generation + span judging +
answer judging), paced by `--delay`. Unanswerable cases skip the judge
entirely — a decline needs no grading, only the deterministic abstention
check.


---

## Metrics Glossary — in one place

| Stage                                      | Metric                    | What It Calculates                                                                                   | Why It's Helpful / Required                                                                                          |
| ------------------------------------------ | ------------------------- | ---------------------------------------------------------------------------------------------------- | -------------------------------------------------------------------------------------------------------------------- |
| **1 — Extraction**                  | `heading_recall`        | Manifest headings the extractor found, normalized-text substring match.                              | Headings drive the parent/child chunk split; a miss mislabels every chunk beneath it.                                |
|                                            | `figure_recall`         | Manifest figures matched on the correct page.                                                        | A figure never detected is permanently unreachable by every later stage.                                             |
|                                            | `table_recall`          | Same, for tables.                                                                                    | Tables carry the dense numeric content most gold questions ask about.                                                |
|                                            | `page_accuracy`         | Restates`figure_recall` — page matching is already built into it.                                 | Kept as its own row so the report states the check explicitly rather than implying it.                               |
| **2a — Index & Artifact Integrity** | `asset_resolvable_rate` | Indexed image vectors whose file actually exists on disk.                                            | A missing file behind a vector is a 404 the moment that image is shown to a user.                                    |
|                                            | `stub_coverage`         | Indexed images with a matching text-collection caption stub.                                         | Without a stub, only the dense image tower can ever surface that image — BM25 can't.                                |
|                                            | `table_atomicity`       | Table chunks that kept a real markdown header row.                                                   | A table row with no header is unlabelled numbers — the data survives, its meaning does not.                         |
|                                            | `orphan_rate`           | Disk assets no vector points at (ceiling metric).                                                    | Some drift after re-ingestion is normal; a large number means reconciliation isn't running.                          |
| **2b — Vision Perception**          | `numeric_fidelity`      | Transcription numbers recovered in the model's reading, at production resolution.                    | The most load-bearing metric in the suite — isolates "the model misread the chart" from every other possible cause. |
|                                            | `entity_recall`         | Non-numeric labels, legend entries, and axis names recovered.                                        | A model can nail every number and still attach it to the wrong series.                                               |
|                                            | `no_fabrication_rate`   | 1 minus the share of reported values that aren't in the transcription (or an expected range).        | An invented number produces a confidently wrong, seemingly-well-read answer — worse than a gap.                     |
| **3 — Retrieval**                   | `hit_rate_at_k`         | Questions with a chunk from an expected document anywhere in the top k.                              | If the right document never surfaces, generation cannot succeed regardless of quality.                               |
|                                            | `recall_at_k`           | Gold facts found anywhere across all retrieved text + image captions.                                | Set below hit rate on purpose — a multi-fact answer needs every supporting fact, not just one hit.                  |
|                                            | `mrr`                   | Mean of 1 ÷ rank of the first hit.                                                                  | Rewards ranking the right passage early, since the generator weights earlier context more.                           |
|                                            | `context_precision`     | Share of retrieved chunks that came from an expected document.                                       | Irrelevant chunks crowd out the token budget available to the generator.                                             |
|                                            | `image_relevancy`       | *(Not implemented)* Would grade image-branch relevance against human judgment.                     | SigLIP-2 cosine similarity is uncalibrated on this corpus, unlike the text path's reranked scores.                   |
|                                            | `score_gate_agreement`  | *(Not implemented)* Would validate the 0.10 floor / 0.55 margin image score gate.                  | Depends on`image_relevancy` existing first.                                                                        |
| **4 — Answer Quality**              | `answer_correctness`    | Share of judged answers graded exactly`2` (fully matches gold).                                    | The end-to-end correctness bar, with no credit for a partial answer.                                                 |
|                                            | `span_correctness`      | Per-objective-statement support, not per-answer.                                                     | Localises a wrong clause instead of letting it hide inside a mostly-right answer.                                    |
|                                            | `groundedness`          | Currently the same underlying calculation as`span_correctness`.                                    | Answers a distinct conceptual question even though today's code shares the number — see the Stage 4 note.           |
|                                            | `answer_relevancy`      | Judged: does the answer address the question actually asked?                                         | Catches a factually correct answer to the wrong question.                                                            |
|                                            | `citation_validity`     | `[N]` / `[IN]` references that resolve to something actually retrieved.                          | A citation to nothing retrieved is a fabricated reference — pure structural check.                                  |
|                                            | `citation_support`      | *(Not implemented — always `n/a`)* Would check a citation actually supports its adjacent claim. | A required bar on paper that cannot currently fail or pass.                                                          |
|                                            | `numeric_recall`        | Gold quantities restated in the generated answer.                                                    | Policy answers are numbers; a dropped bps figure is a wrong answer.                                                  |
|                                            | `abstention_accuracy`   | Unanswerable gold cases correctly declined.                                                          | The costliest failure mode in a regulatory context is a fabricated answer, not a declined one.                       |
|                                            | `subjective_span_ratio` | Share of hedged, non-checkable statements (ceiling metric).                                          | Counterweights groundedness — a generator that hedges everything would otherwise look artificially strong.          |
