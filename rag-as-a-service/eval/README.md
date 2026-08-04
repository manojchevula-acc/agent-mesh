# GERNAS RAG — Evaluation Suite

## What This Document Covers

This is the evaluation framework for the GERNAS Retrieval-Augmented
Generation (RAG) pipeline. A RAG system answers questions by first
*retrieving* relevant passages from a document collection and then using a
language model to *generate* an answer grounded in those passages. Because
several distinct steps happen between "here is a PDF" and "here is an
answer," a failure can originate almost anywhere in that chain — and this
suite exists to identify exactly where.

## Purpose

Rather than producing a single end-to-end score (for example, "62% of
answers are correct"), this suite evaluates the pipeline **stage by stage**.
An end-to-end score tells you *that* something is wrong, but not *where*. By
scoring extraction, indexing, image captioning, retrieval, and answer
generation independently, a quality regression can be traced back to its
root cause — for instance, distinguishing a retrieval ranking problem from a
generation problem — without manual debugging.

Every stage is built on the same three principles:

- **Deterministic scoring wherever possible.** Checks such as numeric-fact
  matching, character-error rate, and structural validation produce the same
  result every time they are run and require no language-model calls, which
  keeps them fast, free, and reproducible.
- **Language-model judgment only where a rule-based check cannot do the
  job**, and always cached separately from the answer-generation step, so
  re-scoring a judgment never requires re-running the more expensive
  generation step.
- **Every quality bar is documented and reviewable** in a single file
  (`eval/core/thresholds.py`), so raising or lowering a threshold is a
  visible, deliberate decision rather than a silent change.

---

## Table of Contents

1. [Pipeline Overview](#pipeline-overview)
2. [How to Run](#how-to-run)
3. [Ground Truth Data](#ground-truth-data)
4. [Stage 1 — Extraction &amp; Layout Fidelity](#stage-1--extraction--layout-fidelity)
5. [Stage 2a — Index &amp; Artifact Integrity](#stage-2a--index--artifact-integrity)
6. [Stage 2b — Image Caption Fidelity](#stage-2b--image-caption-fidelity)
7. [Stage 3 — Retrieval Quality &amp; Ordering](#stage-3--retrieval-quality--ordering)
8. [Stage 4 — Answer Quality](#stage-4--answer-quality)
9. [Reading a Report](#reading-a-report)

---

## Pipeline Overview

The pipeline moves a document through five stages before it can answer a
question:

```
Stage 1            Stage 2a             Stage 2b             Stage 3              Stage 4
Extraction    -->   Index/artifact  -->  Image captioning -->  Retrieval      -->   Answer
(Docling)           integrity            (vision model)        (hybrid search)      generation
```

| Stage                  | What It Checks                                                                                                                                       | What It's Measured Against                                                          |
| ---------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------- | ----------------------------------------------------------------------------------- |
| 1 — Extraction        | Did the extraction engine (Docling) find every figure, table, and heading in the source PDFs?                                                        | Source PDFs plus a manually reviewed layout manifest                                |
| 2a — Index Integrity  | Is every text chunk stored correctly, uniquely identified, and correctly linked to its source?                                                       | The live vector database (Qdrant) and the artifact store                            |
| 2b — Caption Fidelity | Are the AI-generated captions for figures and tables numerically accurate?                                                                           | Human transcriptions of each figure                                                 |
| 3 — Retrieval         | Does the search step return the right passages, in a useful order?                                                                                   | A curated set of gold-standard questions and answers, plus the live vector database |
| 4 — Generation        | Is the final answer correct, grounded in real evidence, properly cited, and appropriately silent when the source material doesn't support an answer? | The same gold-standard question set                                                 |

Every stage is run the same way and produces the same two outputs:

```bash
python -m eval <stage> [options]
```

| Output                  | Location                         | Purpose                                                                          |
| ----------------------- | -------------------------------- | -------------------------------------------------------------------------------- |
| Machine-readable result | `data/eval/runs/<stage>.json`  | For automated tooling and tracking trends over time                              |
| Human-readable report   | `data/eval/reports/<stage>.md` | The file to read when reviewing results                                          |
| Exit code               | `0` or `1`                   | `0` = the stage passed; `1` = a required metric failed, or an error occurred |

---

## How to Run

```bash
python -m eval                       # list all stages
python -m eval stage1 --init-manifest
python -m eval stage2a
python -m eval stage2b --init
python -m eval stage3
python -m eval stage4 --judge --ragas
python -m eval all                   # runs stage2a -> stage2b -> stage3 -> stage4, in order
```

The `all` command skips stage 1, since it re-extracts every PDF from scratch
and can take several minutes. The remaining stages run in dependency order —
earliest first — so that, for example, a stage 2 failure is understood as
the likely cause of a stage 3 failure, rather than the two being reported as
unrelated problems.

---

## Ground Truth Data

"Ground truth" refers to the small set of curated, human-reviewed reference
files that every metric in this suite is scored against. Nothing is ever
scored against the system's own output — doing so would let the system
grade its own work.

| File                                     | How It's Created                                  | What a Reviewer Checks                                                                       |
| ---------------------------------------- | ------------------------------------------------- | -------------------------------------------------------------------------------------------- |
| `data/eval/layout_manifest.json`       | Auto-drafted by`stage1 --init-manifest`         | Every figure, table, and heading the extraction engine found, checked against the source PDF |
| `data/eval/figure_transcriptions.json` | Auto-drafted by`stage2b --init`                 | Every number and label visible on each figure, transcribed by hand                           |
| `data/eval/gold_qa.json`               | Curated manually via`scripts/build_gold_set.py` | Question / expected-answer / expected-source triples                                         |

Each auto-drafted entry starts out marked `verified: false` and is
**excluded from every scored metric** until a human reviews it — scoring the
system against its own unreviewed output would be circular, since the
system generated that "ground truth" itself. Reviewing an entry means
checking it against the original source material and then flipping
`verified` to `true`.

**Stage 3 is the exception.** It has no ground-truth file of its own.
Instead, its relevance judgments — which passages actually contain the
answer to a given question — are calculated automatically from
`gold_qa.json` on every run and written to a temporary file,
`data/eval/runs/stage3_qrels.json`. This is a derived, disposable output, not
a curated input. See [Stage 3](#stage-3--retrieval-quality--ordering) for why
computing judgments this way does not bias the results in the system's
favor.

**Stage 4 also uses `gold_qa.json` as its only reference**, across every
scoring method it uses — deterministic fact-matching, the language-model
judge, and the RAGAS framework alike. There is no separate test set for any
of them.

---

## Stage 1 — Extraction & Layout Fidelity

**What it checks:** whether Docling — the document-extraction engine — finds
every figure, table, and heading in the source PDFs, on the correct page,
with a usable image crop and bounding box. A miss at this stage is invisible
to every later stage: a figure the extraction engine never detects can never
be captioned, indexed, retrieved, or cited in an answer.

**Method:** Runs the extractor with the same configuration used in
production, over every PDF in the `docs/` folder. Detected figures and
tables are matched **page by page** against the reviewed manifest — a figure
detected on the wrong page still counts as a miss, since counting matches
across the whole document would let a correct detection on page 2 hide a
missed detection on page 7.

### Metrics

| Metric                        | What It Measures                                                                  | Passing Threshold  | Why It Matters                                                                                                                     |
| ----------------------------- | --------------------------------------------------------------------------------- | ------------------ | ---------------------------------------------------------------------------------------------------------------------------------- |
| `figure_detection_recall`   | Manifest figures matched by page ÷ manifest figures expected                     | ≥ 1.00 (required) | Sets a hard ceiling for the whole system — a figure that's never detected can never appear anywhere downstream.                   |
| `table_detection_recall`    | Same calculation, for tables                                                      | ≥ 1.00 (required) | Same reasoning as figures.                                                                                                         |
| `heading_recall`            | Manifest headings found (via normalized text match) ÷ headings expected          | ≥ 0.90 (required) | Headings label every chunk beneath them; a missed heading mislabels retrieval results.                                             |
| `unmapped_label_count`      | Count of extraction labels with no defined mapping                                | ≤ 0 (required)    | An unmapped label silently downgrades a figure or table to a plain paragraph — a failure invisible everywhere else in the system. |
| `figure_image_capture_rate` | Figures with image data captured ÷ figures detected                              | ≥ 0.95 (required) | The captioning step (Stage 2b) can't run on a figure whose image was never captured.                                               |
| `bbox_presence_rate`        | Captured figures with a bounding box ÷ captured figures                          | ≥ 0.95 (required) | The bounding box is what allows a citation to be re-cropped and verified against the original page.                                |
| `ocr_routing_accuracy`      | Documents correctly routed to OCR vs. direct text extraction ÷ documents checked | ≥ 1.00 (required) | Wrong routing produces either garbled text (OCR run on a digital PDF) or no text at all (text extraction run on a scanned PDF).    |

**Runtime:** Slow — the extraction engine loads its models and processes
every page (this can take several minutes for the full document set, and
longer on the first run while models are downloaded).

---

## Stage 2a — Index & Artifact Integrity

**What it checks:** whether every chunk in the live vector index (the
searchable database the retrieval step queries) is stored correctly, keyed
in a way that prevents duplication on re-processing, and correctly linked to
its parent text and source image. This stage needs no ground-truth file — it
simply cross-checks the live database against the live file storage.

**Method:** Loads every chunk from the vector database (Qdrant), then
cross-checks it against the artifact store on disk. The most important
check, `orphan_artifact_count`, exists to catch a specific and otherwise
invisible failure: the ingestion pipeline saves a figure's image and records
a reference to it *before* the captioning step runs. If captioning then
fails, the chunk (which requires non-empty text to be usable) is silently
dropped — but the image file remains on disk as if nothing had gone wrong.
Nothing else in the system would surface this.

### Metrics

| Metric                                 | What It Measures                                                                                          | Passing Threshold                         | Why It Matters                                                                                                                                                                                                                   |
| -------------------------------------- | --------------------------------------------------------------------------------------------------------- | ----------------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `artifact_resolvable_rate`           | Media chunks whose image reference resolves and decodes correctly ÷ media chunks                         | ≥ 1.00 (required)                        | If this fails, displaying the image and its citation both break for that chunk at answer time.                                                                                                                                   |
| `orphan_artifact_count`              | Images on disk with no chunk referencing them                                                             | ≤ 0 (required)                           | **The highest-severity check in the suite.** Catches a figure that was captured, silently failed captioning, and was dropped from the index — while ingestion logs still counted it as successful.                        |
| `media_chunk_id_derivation_rate`     | Chunk IDs that correctly regenerate from their source data ÷ media chunks with an image                  | ≥ 1.00 (required)                        | This is what makes re-processing a document safe; a mismatch means re-processing duplicates figures instead of updating them.                                                                                                    |
| `media_atomicity_rate`               | Images with exactly one associated chunk ÷ total images (only measured when chunk-splitting is disabled) | ≥ 1.00 (required)                        | More than one chunk per figure risks splitting its data across chunks that may not be retrieved together.                                                                                                                        |
| `media_source_page_rate`             | Media chunks with a recorded source page ÷ media chunks                                                  | ≥ 0.95 (required)                        | Needed to trace a citation back to a specific page.                                                                                                                                                                              |
| `media_bbox_rate`                    | Media chunks with a geometrically valid bounding box ÷ media chunks                                      | ≥ 0.95 (required)                        | Same purpose as Stage 1's bounding-box check, verified again after storage.                                                                                                                                                      |
| `media_enrichment_model_rate`        | Media chunks whose text came from a successful captioning call ÷ media chunks                            | ≥ 1.00 (required)                        | A missing value means the caption text didn't come from a real transcription — the chunk exists, but its content can't be trusted.                                                                                              |
| `empty_chunk_count`                  | Chunks with no text                                                                                       | ≤ 0 (required)                           | An empty chunk can still be retrieved, but contributes nothing to an answer.                                                                                                                                                     |
| `duplicate_chunk_id_count`           | Chunk IDs stored more than once                                                                           | ≤ 0 (required)                           | Indicates a data-keying bug; duplicates distort retrieval ranking.                                                                                                                                                               |
| `text_orphan_rate`                   | Text chunks whose parent reference doesn't resolve ÷ text chunks                                         | ≤ 0.02 (required)                        | Retrieving the fuller surrounding context silently fails for these chunks at answer time.                                                                                                                                        |
| `fragmented_table_count`             | Chunks containing table rows with no header row                                                           | ≤ 0 (required)                           | A table row without its header loses the meaning of every column — a serious, easy-to-miss correctness bug.                                                                                                                     |
| `clause_reference_plausibility_rate` | Media chunks whose clause reference looks like a genuine clause number ÷ media chunks                    | ≥ 0.90 (**tracked, not required**) | A media chunk's clause reference is derived from its caption text, so a numeric value in the caption (e.g., "18.4") can be mistaken for a clause number. Use the chunk ID, not the clause reference, as the reliable identifier. |
| `caption_truncation_count`           | Captions that appear to be cut off mid-sentence                                                           | ≤ 0 (**tracked, not required**)    | Usually indicates the caption-generation token limit is set too low for that figure's content.                                                                                                                                   |

**Runtime:** Fast — no models are loaded; this stage only reads the vector
database and disk storage.

---

## Stage 2b — Image Caption Fidelity

**What it checks:** whether the AI-generated captions for figures and tables
are numerically accurate compared to what's actually printed on the image.
This addresses the single most damaging failure mode in a system that reads
images: a caption that silently drops or invents a number leads to an answer
that looks well-sourced but is factually wrong.

**Method:** For every figure with a **verified** human transcription, the
system extracts every quantity (for example, "50 bps," "AED 2.0 billion,"
"31-Dec-2024") from both the human transcription and the stored caption,
using a deterministic parser, and compares the two sets. No language model
is used for this comparison — it is pure text parsing, so it's fast, free,
and produces the same result every time.

### Metrics

| Metric                                 | What It Measures                                                                              | Passing Threshold                         | Why It Matters                                                                                                                                                                                    |
| -------------------------------------- | --------------------------------------------------------------------------------------------- | ----------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `caption_numeric_recall`             | Printed quantities that also appear in the caption ÷ printed quantities                      | ≥ 0.98 (required)                        | **The most important metric in this stage.** A number dropped from the caption means any answer built from it will be missing a fact it should have included.                               |
| `caption_numeric_hallucination_rate` | Quantities in the caption that don't appear in the transcription ÷ quantities in the caption | ≤ 0.02 (required)                        | An invented number is worse than a missing one — it produces a confidently wrong, seemingly well-cited answer.                                                                                   |
| `caption_cer` (character error rate) | Text difference between the normalized caption and the transcription                          | ≤ 0.15 (required)                        | A general fidelity check.**Caveat:** captions are formatted as markdown while transcriptions are terse plain text, so this score can look worse than it is even when every fact is correct. |
| `illegible_marker_rate`              | Captions flagged as illegible ÷ total captions                                               | ≤ 0.10 (**tracked, not required**) | A high rate usually means the source images were rendered at too low a resolution.                                                                                                                |
| `empty_caption_rate`                 | Media chunks with no caption text ÷ media chunks                                             | ≤ 0.0 (required)                         | An empty caption means total information loss for that figure.                                                                                                                                    |
| `transcription_coverage`             | Media chunks with a verified human transcription ÷ total media chunks                        | ≥ 0.80 (**tracked, not required**) | Measures how much of the ground truth has been reviewed, not how good the system is — a report based on 3 of 15 figures is under-measured, not passing.                                          |

**Runtime:** Fast — only reads the database and file storage. All gated
metrics in this stage report `0` or `n/a` until at least one figure has been
manually transcribed and verified.

---

## Stage 3 — Retrieval Quality & Ordering

**What it checks:** for each gold-standard question, does the live retrieval
pipeline (which combines keyword search and semantic search, then re-ranks
the results) surface the passage that actually contains the answer, and rank
it near the top?

**Method:** For each question, the evaluation runs the exact retrieval path
used in production — the same search logic, re-ranking model, and recency
adjustments — so the results reflect exactly what a real user would receive.

**Ground truth is calculated automatically, not hand-reviewed.** A retrieved
passage is graded relevant when its text contains the facts already recorded
in `gold_qa.json`'s expected answer.

**This is not circular scoring.** The grader never looks at what the
retrieval system actually returned — it only checks whether a passage
contains facts a human wrote down as the correct answer, independent of any
retrieval run. Grading a passage as relevant simply because the retriever
ranked it first would guarantee a perfect score every time; grading it based
on whether it contains the specific facts a human recorded beforehand does
not. Every judgment records the supporting evidence, so any grade that looks
questionable can be checked rather than taken on faith.

### Metrics — "Did we find the answer?" (based on passages graded as containing the answer)

| Metric                         | What It Measures                                                                            | Passing Threshold  | Why It Matters                                                                                                                                                                                           |
| ------------------------------ | ------------------------------------------------------------------------------------------- | ------------------ | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `hit_rate_at_5`              | Questions with at least one answer-containing passage in the top 5 results ÷ all questions | ≥ 0.95 (required) | **The headline number.** Most questions have exactly one passage that contains the answer, so this largely determines whether answer generation (Stage 4) can succeed at all.                      |
| `hit_rate_at_10`             | Same calculation, at the top 10 results                                                     | ≥ 0.98 (required) | A diagnostic check that goes deeper than what production actually shows a user.                                                                                                                          |
| `recall_at_5`                | Share of all answer-containing passages found within the top 5, averaged across questions   | ≥ 0.90 (required) | Differs from hit rate only when an answer's facts are spread across multiple passages — the bar is slightly lower because a partial multi-passage answer is a less severe failure than finding nothing. |
| `recall_at_10`               | Same calculation, at the top 10 results                                                     | ≥ 0.95 (required) | —                                                                                                                                                                                                       |
| `mrr` (Mean Reciprocal Rank) | Average of 1 ÷ (rank of the first answer-containing passage)                               | ≥ 0.80 (required) | Rewards ranking the correct passage first far more than ranking it fifth — this is the metric for ordering quality.                                                                                     |

### Metrics — "Was what we returned actually useful?" (based on passages graded as relevant or supporting)

| Metric                      | What It Measures                                                                           | Passing Threshold                         | Why It Matters                                                                                                                                                                                                                                                                                   |
| --------------------------- | ------------------------------------------------------------------------------------------ | ----------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| `precision_at_5`          | Top-5 passages graded relevant or supporting ÷ 5                                          | ≥ 0.30 (**tracked, not required**) | Treated as a**lower bound only** — since grading is anchored to the gold answer's exact facts, a genuinely useful passage that doesn't restate any of those specific facts is graded as irrelevant.                                                                                       |
| `context_precision_at_10` | Rank-weighted precision across the top 10 results                                          | ≥ 0.70 (required)                        | Order-sensitive — the same relevant passages score higher when ranked earlier, which plain precision doesn't capture.                                                                                                                                                                           |
| `context_recall`          | Gold-answer facts present anywhere in the top-10 retrieved text ÷ total gold-answer facts | ≥ 0.90 (required)                        | **Free of grading bias** — asks whether a perfect answer-writer *could* have answered using only what was retrieved, regardless of which exact passage carried each fact. Because a grading mistake can't inflate this score, it's the metric to trust when another metric looks wrong. |

Beyond the scored metrics, each report also includes diagnostic-only
tables: a breakdown by content type (figures/tables vs. plain text, showing
whether image captions retrieve as well as prose), a histogram of where the
first correct result tends to rank, a per-question detail table, and a
table showing how each question's ground truth was determined — useful
first stops when a metric looks wrong.

**Runtime:** Slow on the first run (loads the embedding model and
re-ranking model), fast on subsequent runs within the same session. A
`--score-only` flag re-scores the last saved run without loading any models.

---

## Stage 4 — Answer Quality

**What it checks:** is the final generated answer correct, grounded in what
was actually retrieved, properly cited, and appropriately silent when the
document collection doesn't contain enough information to answer? This
stage runs through the exact same code path used in production, so the
results reflect real-world behavior.

**Method:** For each gold-standard question, the system retrieves passages
and generates an answer through the real production pipeline, recording a
full trace of what happened. Scoring then runs **three independent
methods** over that same recorded answer, so any failure can be attributed
to the specific check that caught it, rather than one unexplained pass/fail
result:

1. **Deterministic checks** — numeric-fact recall and groundedness, with no
   language-model cost and no run-to-run variation.
2. **Custom language-model judge** (`--judge`) — a grading prompt built
   specifically for this system's numeric-heavy answers.
3. **RAGAS** (`--ragas`) — the industry-standard `faithfulness`,
   `answer_relevancy`, `context_precision`, and `context_recall` metrics from
   the open-source `ragas` library, providing a second, independently-built
   perspective.

All three methods are cached separately, so re-scoring one of them never
requires re-running the other two.

### Metrics — Deterministic (always computed)

| Metric                          | What It Measures                                                                  | Passing Threshold  | Why It Matters                                                                                                                                                                      |
| ------------------------------- | --------------------------------------------------------------------------------- | ------------------ | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `answer_numeric_recall`       | Gold-answer quantities restated in the generated answer ÷ gold-answer quantities | ≥ 0.95 (required) | Did the answer include the specific numbers the question actually needed?                                                                                                           |
| `answer_numeric_groundedness` | Answer quantities that can be traced to a retrieved passage ÷ answer quantities  | ≥ 0.98 (required) | Checked against the verified human transcription where one exists (rather than the AI-generated caption), so a caption error can't be mistaken for a well-grounded, correct answer. |

### Metrics — Custom Language-Model Judge (`--judge`)

| Metric                      | What It Measures                                                  | Passing Threshold  | Why It Matters                                                                                                                                         |
| --------------------------- | ----------------------------------------------------------------- | ------------------ | ------------------------------------------------------------------------------------------------------------------------------------------------------ |
| `judge_strict_pass_rate`  | Answers graded "correct" ÷ answers judged                        | ≥ 0.85 (required) | The strict correctness bar.                                                                                                                            |
| `judge_lenient_pass_rate` | Answers graded "correct" or "partially correct" ÷ answers judged | ≥ 0.95 (required) | A softer bar, useful for distinguishing a wrong answer from one that's simply incomplete.                                                              |
| `judge_unknown_rate`      | Judge responses that couldn't be parsed ÷ answers judged         | ≤ 0.02 (required) | Flags missing data rather than a real verdict — tracked separately so a malfunctioning judge shows up as a tooling problem, not a quality regression. |

### Metrics — RAGAS Framework (`--ragas`)

Scored against `gold_qa.json`'s expected answers — the same reference file
every other metric in this stage uses. The language model used for grading
is configured separately from the one used to generate answers, since a
model tuned for fast, cheap generation isn't necessarily a good grader.
**These metrics are currently informational, not gating**: they are new to
this system and have not yet been validated against real-world outcomes, so
the thresholds below are a bar to monitor rather than a release requirement.

| Metric                      | What It Measures                                                                                     | Target  | Why It Matters                                                                                                                                                      |
| --------------------------- | ---------------------------------------------------------------------------------------------------- | ------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `ragas_faithfulness`      | Share of the answer's individual claims that a judge model can verify against the retrieved passages | ≥ 0.85 | An industry-standard groundedness check — a second, independently-built perspective on the same question`answer_numeric_groundedness` answers deterministically. |
| `ragas_answer_relevancy`  | Similarity between the original question and questions reconstructed from the answer                 | ≥ 0.80 | Catches an answer that is well-grounded in evidence but doesn't actually address what was asked.                                                                    |
| `ragas_context_precision` | Rank-weighted relevance of retrieved passages against the reference answer                           | ≥ 0.75 | RAGAS's own version of Stage 3's`context_precision_at_10`, calculated here at answer-generation time.                                                             |
| `ragas_context_recall`    | Language-model-judged coverage of the reference answer by the retrieved passages                     | ≥ 0.80 | RAGAS's judged counterpart to Stage 3's deterministic`context_recall`.                                                                                            |

### Metrics — Citation & Abstention (always computed)

| Metric                     | What It Measures                                                                                        | Passing Threshold                         | Why It Matters                                                                                                                                                                                                                                                      |
| -------------------------- | ------------------------------------------------------------------------------------------------------- | ----------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `citation_presence_rate` | Answers containing at least one citation ÷ answerable questions                                        | ≥ 0.95 (required)                        | A compliance and audit requirement — an uncited claim can't be traced back to its source.                                                                                                                                                                          |
| `citation_validity_rate` | Citations pointing to a real, supplied passage ÷ total citations                                       | ≥ 1.00 (required)                        | A citation to a passage that doesn't exist is a fabricated reference.                                                                                                                                                                                               |
| `citation_support_rate`  | Cited sentences sharing a quantity or distinctive wording with the passage they cite ÷ cited sentences | ≥ 0.85 (**tracked, not required**) | A coarse check for gross citation mismatches — not a full accuracy check.                                                                                                                                                                                          |
| `abstention_accuracy`    | Unanswerable questions correctly declined ÷ unanswerable questions                                     | ≥ 1.00 (required)                        | **For a system used in a regulatory context, fabricating an answer the source material can't support is the single most costly failure mode.** Currently shows `n/a` — the gold question set does not yet include any deliberately unanswerable questions. |
