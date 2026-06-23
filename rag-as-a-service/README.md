# GERNAS RAG Layer

Production-grade **hybrid retrieval service** for FAB GERNAS (the bank's regulatory
and credit-policy assistant). It ingests policy/regulatory PDFs, indexes them into a
vector database, and answers natural-language questions with **cited, freshness-aware,
grounded** responses.

The retrieval stack combines dense + sparse embeddings (BGE-M3), Reciprocal Rank
Fusion, cross-encoder reranking, freshness penalties, parent-chunk expansion, an
optional LLM answer-generation step, and a RAGAS evaluation harness.

Everything is **configurable via `.env` / YAML** — switching the vector DB, embedding
model, LLM provider, chunking strategy, or extraction engine requires **no code
change**, only a config flag. This is enforced architecturally through a
provider/factory pattern (see [Design principles](#design-principles)).

---

## Table of contents

1. [What this system does](#what-this-system-does)
2. [High-level architecture](#high-level-architecture)
3. [Design principles](#design-principles)
4. [The two end-to-end flows](#the-two-end-to-end-flows)
   - [Flow A — Ingestion](#flow-a--ingestion-document--searchable-index)
   - [Flow B — Retrieval &amp; answering](#flow-b--retrieval--answering-query--cited-answer)
5. [Component-by-component deep dive](#component-by-component-deep-dive)
   - [Configuration layer](#1-configuration-layer)
   - [Document extraction](#2-document-extraction)
   - [Chunking](#3-chunking)
   - [Metadata extraction](#4-metadata-extraction)
   - [Embeddings](#5-embeddings)
   - [Vector database](#6-vector-database)
   - [Hybrid search + RRF](#7-hybrid-search--reciprocal-rank-fusion)
   - [Cross-encoder reranking](#8-cross-encoder-reranking)
   - [Freshness penalty](#9-freshness-penalty)
   - [Parent-chunk expansion](#10-parent-chunk-expansion)
   - [Answer generation (LLM)](#11-answer-generation-llm)
   - [Caching](#12-caching)
   - [Evaluation (RAGAS)](#13-evaluation-ragas)
   - [API layer](#14-api-layer)
   - [Cross-cutting utilities](#15-cross-cutting-utilities)
   - [Frontend](#16-frontend)
6. [Technology choices &amp; rejected alternatives](#technology-choices--rejected-alternatives)
7. [Setup &amp; operations](#setup--operations)
8. [Project layout](#project-layout)

---

## What this system does

FAB's credit and regulatory teams need fast, **auditable** answers grounded in
authoritative policy documents — e.g. *"What is the minimum pricing floor for a
BB-rated corporate term loan with 4-year tenor in AED?"* A plain LLM would
hallucinate or quote stale rules. This service instead:

1. **Indexes** the authoritative corpus (pricing policy, CBUAE circulars, model-risk
   framework, concentration limits, product manuals) into a hybrid vector index.
2. **Retrieves** the most relevant clauses for a query using both semantic (dense)
   and lexical (sparse/keyword) signals, then reranks them with a cross-encoder.
3. **Penalises stale documents** so superseded policy ranks lower and is flagged.
4. **Optionally generates** a grounded answer that **cites the source document and
   clause** for every claim, and refuses to answer when the context is insufficient.
5. **Evaluates** answer quality with RAGAS metrics (faithfulness, relevancy,
   precision, recall) against a curated FAB test set.

The mock corpus lives in [`docs/`](docs/) and includes the FAB Credit Pricing Policy,
the CBUAE AI-Governance circular, the Model Risk Management framework, the Credit
Concentration Limits policy, and the Corporate Term Loan product manual.

---

## High-level architecture

```
                          ┌──────────────────────────────────────────┐
   INGESTION              │           RETRIEVAL / ANSWERING           │
                          │                                            │
 Document file            │   Query                                    │
     │                    │     │                                      │
     ▼                    │     ▼                                      │
  Extract (Docling)       │  Encode query (dense + sparse, BGE-M3)     │
     │                    │     │                                      │
     ▼                    │     ▼                                      │
  Chunk (hierarchical,    │  Metadata filter (drop deprecated, …)      │
  parent + child)         │     │                                      │
     │                    │     ├─────────────┬───────────────┐        │
     ▼                    │     ▼             ▼               │        │
  Embed (batched,         │  Dense ANN     Sparse BM25        │        │
  dense + SPLADE)         │     └──────┬──────┘               │        │
     │                    │            ▼                      │        │
     ▼                    │       RRF merge                   │        │
  Upsert → Vector DB ─────┼──▶  Cross-encoder rerank          │        │
  (Qdrant, named vectors) │            ▼                      │        │
                          │       Freshness penalty           │        │
                          │            ▼                      │        │
                          │       Parent-chunk expand         │        │
                          │            ▼                      │        │
                          │   [optional] LLM answer + cite    │        │
                          │            ▼                      │        │
                          │   Cache (Redis) + return          │        │
                          └────────────────────────────────────────────┘
```

The service is a **FastAPI** application ([`src/gernas_rag/main.py`](src/gernas_rag/main.py)).
On startup (the lifespan handler) it constructs the embedder, vector DB client, LLM
client, and cache once, builds the ingestion and retrieval pipelines, and stores them
on `app.state` — a lightweight dependency-injection container that FastAPI routes pull
from via [`api/deps.py`](src/gernas_rag/api/deps.py). Heavy model weights are therefore
loaded **once per process**, not per request.

A separate **React SPA** in [`frontend/`](frontend/) provides Search, Upload,
Evaluation, and Admin pages over the same API.

---

## Design principles

These are the rules the whole codebase follows; understanding them explains *why* the
code is structured the way it is.

### 1. Provider + factory pattern → swappable everything

Every pluggable concern (embeddings, vector DB, LLM, extraction, chunking) has:

- an **abstract base class** defining the contract (e.g.
  [`embeddings/base.py`](src/gernas_rag/embeddings/base.py),
  [`vectordb/base.py`](src/gernas_rag/vectordb/base.py),
  [`llm/base.py`](src/gernas_rag/llm/base.py)),
- one or more **concrete implementations**, and
- a **factory** (`factory.py`) that returns the right implementation based on a config
  enum.

Example — [`embeddings/factory.py`](src/gernas_rag/embeddings/factory.py):

```python
def get_embedder(config: EmbeddingConfig) -> BaseEmbedder:
    match config.provider:
        case EmbeddingProvider.BGEM3:               return BGEM3Embedder(config)
        case EmbeddingProvider.SENTENCE_TRANSFORMER: return SentenceTransformerEmbedder(config)
```

The pipelines depend only on the **base class**, never a concrete provider. Switching
Qdrant → Milvus is `RAG__VECTORDB__PROVIDER=milvus` in `.env`. **Why:** vendor
lock-in is a real procurement risk for a bank; the abstraction means a vendor decision
made later (or differently per environment) never touches business logic.

### 2. Async-first, with CPU-bound work pushed to a thread pool

The API is fully `async`. The embedder, reranker, and Docling extractor are CPU/GPU
bound (they'd block the event loop), so each dispatches its heavy `compute` call to a
`ThreadPoolExecutor` via `asyncio.run_in_executor`. Dense and sparse searches run
**concurrently** with `asyncio.gather`. **Why:** keeps the service responsive under
concurrent load while still using blocking ML libraries.

### 3. Graceful degradation everywhere

Optional components fail soft, never hard: the **reranker** falls back to fused-rank
truncation if its model can't load; the **cache** degrades to a miss on any Redis
error; **ingestion** logs and reports per-file errors instead of crashing the batch.
**Why:** retrieval is the core promise — enhancements must not be able to take it down.

### 4. Idempotent ingestion via deterministic IDs

Chunk IDs are a deterministic MD5 of `document_name::reference`
([`utils/hashing.py`](src/gernas_rag/utils/hashing.py)), converted to a deterministic
UUIDv5 for Qdrant point IDs. Re-ingesting the same document **upserts** the same
points instead of duplicating them. **Why:** re-indexing after a policy update must be
safe to run repeatedly.

### 5. Config precedence: env > YAML > defaults

[`config/settings.py`](src/gernas_rag/config/settings.py) layers
`default.yaml → <environment>.yaml → local.yaml → CONFIG_FILE`, then lets environment
variables / `.env` override the lot. Nested config uses the `RAG__SECTION__FIELD`
convention. **Why:** YAML expresses per-environment baselines committed to git;
secrets and machine-specific values stay in env vars and never get committed.

---

## The two end-to-end flows

### Flow A — Ingestion (document → searchable index)

Orchestrated by [`ingestion/pipeline.py`](src/gernas_rag/ingestion/pipeline.py)
(`IngestionPipeline`). Triggered by the `POST /api/v1/ingest` endpoint (async, returns
a `job_id`) or the [`scripts/ingest_docs.py`](scripts/ingest_docs.py) CLI.

| # | Step                     | Component               | What happens                                                                                                                                                            |
| - | ------------------------ | ----------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| 1 | **Extract**        | `DoclingExtractor`    | Convert PDF/DOCX into structure-preserving Markdown + a typed element list (headings, paragraphs, tables, lists).                                                       |
| 2 | **Build metadata** | `MetadataExtractor`   | Derive `document_name`, `document_type`, `effective_date`, `product_applicability` from the filename/content (or use the values the caller supplied).           |
| 3 | **Chunk**          | `HierarchicalChunker` | Split into**parent** sections and **child** sub-clauses at semantic boundaries; attach metadata + a deterministic ID + a detected clause reference to each. |
| 4 | **Embed**          | `BGEM3Embedder`       | Encode chunk texts in batches into dense (1024-d) + SPLADE sparse vectors. Runs in a thread pool.                                                                       |
| 5 | **Upsert**         | `QdrantVectorDB`      | Write points (dense + sparse named vectors + full metadata payload) into the collection. Idempotent.                                                                    |

Directory ingestion (`ingest_directory`) processes files concurrently with a bounded
`asyncio.Semaphore` (`max_concurrent_documents`, default 3) so a large corpus doesn't
exhaust memory or saturate the model.

### Flow B — Retrieval & answering (query → cited answer)

Orchestrated by [`retrieval/pipeline.py`](src/gernas_rag/retrieval/pipeline.py)
(`RetrievalPipeline`). Triggered by `POST /api/v1/retrieve`.

| # | Step                          | Component             | What happens                                                                                                              |
| - | ----------------------------- | --------------------- | ------------------------------------------------------------------------------------------------------------------------- |
| 0 | **Cache check**         | `RAGCache`          | Hash the request; return the cached response on a hit (`cache_hit=true`).                                               |
| 1 | **Encode query**        | `BGEM3Embedder`     | One model pass → dense + sparse query vectors.                                                                           |
| 2 | **Hybrid search**       | `HybridSearcher`    | Dense ANN (top 40)**and** sparse BM25 (top 40) in parallel, with metadata pre-filtering (deprecated docs excluded). |
| 3 | **RRF merge**           | `HybridSearcher`    | Fuse the two ranked lists with Reciprocal Rank Fusion (`k=60`) → top 20 candidates.                                    |
| 4 | **Rerank**              | `Reranker`          | Cross-encoder scores each `(query, chunk)` pair and keeps the top-`k`.                                                |
| 5 | **Freshness penalty**   | `FreshnessFilter`   | Decay scores of chunks older than `freshness_max_age_days`; re-sort; tag stale chunks.                                  |
| 6 | **Parent expand**       | `RetrievalPipeline` | Fetch the parent section text for each child chunk so the answer has full context.                                        |
| 7 | **Generate (optional)** | `ResponseGenerator` | If `generate_answer=true`, build a grounded prompt and call the LLM for a cited answer.                                 |
| 8 | **Cache + return**      | `RAGCache`          | Store the response in Redis (background task) and return it with latency + freshness flags.                               |

---

## Component-by-component deep dive

### 1. Configuration layer

**Files:** [`config/settings.py`](src/gernas_rag/config/settings.py) and the per-domain
sub-configs (`embedding.py`, `vectordb.py`, `llm.py`, `chunking.py`, `retrieval.py`,
`ingestion.py`, `evaluation.py`).

- Built on **Pydantic Settings v2** (`BaseSettings`). Each domain is its own typed
  `BaseModel`, composed under the root `Settings`.
- **Why Pydantic Settings:** type validation at startup (a bad enum or out-of-range
  int fails fast, not mid-request), automatic env-var binding, and free generation of
  the OpenAPI schema for the request/response models.
- `get_settings()` is `@lru_cache`'d → a single immutable settings singleton per
  process, imported everywhere.
- **Precedence** is implemented by deep-merging YAML layers and then overlaying only
  the explicitly-set env fields (`model_dump(exclude_unset=True)`), so env always wins.

Key tunables (defaults shown):

| Section   | Field                              | Default         | Meaning                                |
| --------- | ---------------------------------- | --------------- | -------------------------------------- |
| retrieval | `dense_top_k` / `sparse_top_k` | 40 / 40         | Candidate pool size per modality       |
| retrieval | `rrf_k`                          | 60              | RRF smoothing constant                 |
| retrieval | `pre_rerank_top_k`               | 20              | Candidates sent to the reranker        |
| retrieval | `final_top_k`                    | 5               | Results returned                       |
| retrieval | `freshness_max_age_days`         | 180             | Grace period before staleness decay    |
| retrieval | `freshness_max_penalty`          | 0.3             | Max 30% score reduction for stale docs |
| chunking  | `chunk_size` / `chunk_overlap` | 400 / 64 tokens | Child chunk geometry                   |
| chunking  | `parent_chunk_size`              | 1500 tokens     | Parent section size                    |
| embedding | `dense_dim`                      | 1024            | BGE-M3 dense dimension                 |
| embedding | `batch_size`                     | 32              | Chunks per encode batch                |

### 2. Document extraction

**Files:** [`extraction/`](src/gernas_rag/extraction/) — `base.py`,
`docling_extractor.py` (primary), `unstructured_extractor.py`, `pymupdf_extractor.py`,
`factory.py`.

- **Primary: IBM Docling.** Converts PDF/DOCX/PPTX/HTML/MD while **preserving heading
  hierarchy, tables, and reading order**, and exports clean Markdown. The chunker
  relies on that structure (Markdown headings, numbered clauses) to split
  semantically. Runs in a thread pool (CPU-bound). MIT-licensed.
- **`ExtractionResult`** carries both a typed `elements` list (`HEADING`, `PARAGRAPH`,
  `TABLE`, `LIST_ITEM`, `CAPTION`) and the `raw_markdown` used downstream.
- **Why Docling over alternatives:**
  - **Unstructured.io** (kept as a fallback for scanned/OCR PDFs via `hi_res`) is
    heavier, has more native dependencies, and its layout output is noisier for the
    clean, born-digital policy PDFs in this corpus.
  - **PyMuPDF** (kept as a fast utility extractor) is extremely fast but returns
    **flat text with no structure** — it would destroy the heading/clause hierarchy
    that hierarchical chunking and clause-reference detection depend on.
  - Docling is the best fit for **structured, born-digital policy documents** where
    clause/section fidelity is the whole point.

### 3. Chunking

**Files:** [`chunking/`](src/gernas_rag/chunking/) — `base.py`, `hierarchical.py`
(primary), `fixed_size.py` (fallback), `factory.py`.

- **Primary: hierarchical parent/child chunking.** Two `RecursiveCharacterTextSplitter`
  instances (from `langchain-text-splitters`):
  - a **parent splitter** that cuts at top-level headings (`\n# `, `\n## `) into large
    ~1500-token sections,
  - a **child splitter** that cuts each parent into ~400-token sub-clauses using a
    prioritised separator list: Markdown headings → numbered clauses (`4.2.1`, `4.2`)
    → `Article N` / `Section N` markers → paragraph → line → word (last resort).
- Each child stores a `parent_chunk_id`, enabling **small-to-big retrieval**: search
  on precise child chunks, but return the parent's full context to the LLM.
- A regex **clause-reference detector** (`_extract_clause_ref`) tags each chunk with
  its clause number (e.g. `4.2.1`, `Article 15`) for citation.
- Token budgets are converted to char budgets via an approximate `_CHARS_PER_TOKEN = 4`
  factor; chunks below `min_chunk_size` are dropped.
- **Why hierarchical over fixed-size:** fixed-size chunking (the `fixed_size.py`
  fallback) splits blindly every N tokens — it cuts mid-clause, mixes unrelated rules,
  and loses the section context an auditor needs. For regulatory text where a single
  clause is the unit of meaning, semantic boundaries matter. The parent/child split
  specifically solves the dense-retrieval dilemma that *small chunks retrieve
  precisely but read poorly, and large chunks read well but retrieve imprecisely.*

### 4. Metadata extraction

**File:** [`ingestion/metadata.py`](src/gernas_rag/ingestion/metadata.py).

- Infers `document_type` from filename keywords (e.g. `pricing` → `pricing_policy`,
  `cbuae`/`circular` → `regulatory`, `mrm` → `mrm`) and `effective_date` via regex
  over the first 2000 chars (ISO dates and "effective `<date>`" phrasing).
- Caller-supplied values always take precedence over inference.
- **Why:** metadata drives the **filtering** and **freshness** layers. Document type
  enables scoped queries ("only pricing policy"); effective date drives staleness.
  Auto-inference means an operator can drop a file in without hand-labelling, while
  still allowing explicit overrides for correctness.

### 5. Embeddings

**Files:** [`embeddings/`](src/gernas_rag/embeddings/) — `base.py`, `bgem3.py`
(primary), `sentence_transformer.py` (alternative), `factory.py`.

- **Primary: BAAI/bge-m3.** A single model that produces **both** a 1024-d dense
  vector **and** SPLADE-style sparse (lexical) weights in one forward pass — exactly
  what hybrid search needs. Self-hosted, Apache-2.0, multilingual.
- Lazy-loaded on first use; all `encode` calls run in a thread pool
  (`run_in_executor`) because FlagEmbedding is CPU/GPU bound. `use_fp16` halves memory
  with negligible quality loss on GPU.
- The output (`EmbeddingOutput`) carries `dense_vectors`, `sparse_indices`,
  `sparse_values` — sparse vectors are stored as `{token_id: weight}` pairs.
- **Why BGE-M3 over alternatives:**
  - **One model for dense + sparse** avoids running and maintaining two separate models
    (a dense encoder + a separate SPLADE model), halving memory and keeping the two
    representations aligned.
  - **Self-hosted** matters for a bank — policy text never leaves the perimeter, so an
    **OpenAI/Cohere embedding API was rejected** on data-residency grounds (the
    `openai_compat` provider exists only as an escape hatch).
  - **sentence-transformers** (e.g. `e5-large-v2`) is kept as a swappable alternative,
    but it is **dense-only** — choosing it disables the sparse half of hybrid search,
    which is why the pipeline disables the reranker too when that provider is selected.

### 6. Vector database

**Files:** [`vectordb/`](src/gernas_rag/vectordb/) — `base.py`, `qdrant_client.py`
(primary), `milvus_client.py`, `chromadb_client.py`, `factory.py`.

- **Primary: Qdrant** via `AsyncQdrantClient`. Chosen for **native hybrid search**:
  a single collection holds two **named vectors** — `dense` (cosine) and `sparse` —
  so dense ANN and sparse retrieval hit the same store with the same filters.
- On collection creation it also builds **payload indexes** on `document_type`,
  `product_applicability`, `deprecated`, and `effective_date` for fast metadata
  filtering.
- **Filtering** (`_build_filter`) always enforces `deprecated == false`, then adds the
  caller's `document_type` / `product_applicability` constraints — so superseded
  documents are excluded at the database level before scoring.
- Point IDs are deterministic UUIDv5s → idempotent upserts. Writes/reads are wrapped
  in `@async_retry` (3 attempts, exponential backoff) for transient network errors.
- Supports an **embedded in-process mode** (`qdrant_path`) requiring no server/Docker —
  handy for local dev and tests.
- **Why Qdrant over alternatives:**
  - **Milvus** (kept as an alternative for billion-scale) requires heavier infra
    (etcd + MinIO + multiple pods) than this corpus justifies; overkill operationally.
  - **ChromaDB** (kept for dev/test) lacks first-class production sparse-hybrid support
    and clustering/replication.
  - Qdrant hits the sweet spot: production-grade, single-binary, native sparse+dense
    hybrid, rich filtering, async client.

### 7. Hybrid search + Reciprocal Rank Fusion

**File:** [`retrieval/hybrid_search.py`](src/gernas_rag/retrieval/hybrid_search.py).

- Runs `dense_search` and `sparse_search` **concurrently** (`asyncio.gather`), each
  retrieving its own top-`k` (40) with the same metadata filter.
- Merges them with **Reciprocal Rank Fusion**: each result contributes
  `1 / (k + rank)` (with `k = 60`) to its document's fused score; scores are summed
  across the two lists and re-sorted. Documents found by *both* methods rise to the top.
- **Why hybrid + RRF:**
  - **Dense alone** misses exact identifiers, rare tokens, and numbers ("BB-rated",
    "260 bps", "Article 5.1.1") — precisely the high-value tokens in policy queries.
  - **Sparse/BM25 alone** misses paraphrases and semantic intent.
  - **RRF over score-weighted fusion:** dense (cosine) and sparse (BM25) scores live on
    incomparable scales; naively summing them is meaningless. RRF fuses on **rank**,
    not score, so it needs no calibration, no tuning of dense/sparse weights, and is
    robust — it's the standard, well-proven fusion method. (`dense_weight`/
    `sparse_weight` exist in config only for DBs that require explicit weighting.)

### 8. Cross-encoder reranking

**File:** [`retrieval/reranker.py`](src/gernas_rag/retrieval/reranker.py).

- **Model: BAAI/bge-reranker-v2-m3** (FlagEmbedding `FlagReranker`). It scores each
  `(query, chunk)` pair **jointly** through a single transformer (a cross-encoder),
  rather than comparing two independently-computed vectors.
- Takes the ~20 fused candidates and returns the top-`k`. Runs in a thread pool.
- **Degrades gracefully:** if the model can't load/score, it logs a warning, sets
  `_unavailable`, and falls back to fused-rank truncation — retrieval still works.
- Disabled automatically when the embedding provider is plain
  `sentence_transformer` (no paired reranker assumed in that path).
- **Why a cross-encoder reranker:**
  - Bi-encoder retrieval (dense/sparse) is fast but approximate — it compresses query
    and document into separate vectors. A **cross-encoder reads query and document
    together**, capturing fine-grained interactions, and is far more accurate at
    ordering the final few results.
  - The classic **retrieve-then-rerank** pattern: use the cheap bi-encoder to fetch a
    broad candidate pool, then spend the expensive cross-encoder only on those ~20 —
    getting cross-encoder precision at near bi-encoder cost.
  - `bge-reranker-v2-m3` pairs naturally with the `bge-m3` embedder (same family,
    same licensing, self-hosted).

### 9. Freshness penalty

**File:** [`retrieval/freshness.py`](src/gernas_rag/retrieval/freshness.py).

- Computes a **freshness score** per chunk from its `effective_date`: `1.0` while the
  document is within `freshness_max_age_days` (180), then **linearly decays** to `0.0`
  at twice that age. The score multiplies into the relevance score via
  `score × (1 − penalty)`, where `penalty = (1 − freshness) × freshness_max_penalty`
  (max 30%). Results are then re-sorted.
- Parses several date formats; missing/unparseable dates are treated as fresh (`1.0`)
  to avoid unfairly penalising undated content.
- Writes `freshness_score` back into metadata so the pipeline can raise a
  `freshness_warning` (threshold 0.7) on individual chunks and globally.
- **Why:** in regulatory retrieval, a semantically perfect but **superseded** clause is
  worse than useless — it's a compliance risk. Hard-deleting old docs loses audit
  history, so instead they're **down-weighted and visibly flagged** (the answer prompt
  marks them `⚠ STALE`), letting genuinely newer guidance win while keeping the trail.

### 10. Parent-chunk expansion

**File:** [`retrieval/pipeline.py`](src/gernas_rag/retrieval/pipeline.py) (step 5).

- For each retrieved child chunk, fetches its parent section text by `parent_chunk_id`
  (`get_by_ids`) and attaches it as `parent_text`.
- **Why:** this is the read side of small-to-big retrieval. Matching happens on precise
  child chunks (high retrieval accuracy), but the LLM receives the **full surrounding
  section** (high answer quality) — so an answer isn't truncated mid-clause and has the
  definitions/conditions that live elsewhere in the same section.

### 11. Answer generation (LLM)

**Files:** [`generation/generator.py`](src/gernas_rag/generation/generator.py),
[`llm/`](src/gernas_rag/llm/) — `base.py`, `groq_llm.py` (primary),
`anthropic_llm.py`, `huggingface_llm.py`, `openai_compat.py`, `factory.py`.

- Only runs when the request sets `generate_answer=true`.
- `ResponseGenerator` builds a numbered **context block** from the retrieved chunks,
  each headed with `Source · Clause · Effective date` and a `⚠ STALE` marker where
  applicable, preferring the parent text. A strict **system prompt** instructs the
  model to:
  - answer **only** from the provided context,
  - **cite** `[source · clause]` for every factual claim,
  - explicitly say so (and not speculate) when the answer isn't present,
  - flag stale/deprecated context.
- `temperature=0.0` for deterministic, reproducible answers. Calls are retried with
  backoff.
- **Primary LLM: Groq (`llama-3.1-70b-versatile`).** Chosen for very low latency
  (Groq's LPU inference) at near-zero cost during the POC, with a capable open model.
- **Why this provider set:**
  - **Anthropic** (`anthropic_llm.py`) is the swappable choice for highest-quality
    grounded reasoning when answer quality outranks latency/cost.
  - **HuggingFace** (`huggingface_llm.py`) enables a fully self-hosted, air-gapped
    deployment (e.g. Mistral-7B on-prem) where no answer text may leave the network.
  - **`openai_compat`** targets any OpenAI-compatible gateway (vLLM, TGI, LiteLLM,
    Azure OpenAI) so an internal model server can be dropped in without new code.
  - The point isn't any single model — it's that the **prompt/grounding contract is
    fixed** and the model behind it is a config flag.

### 12. Caching

**File:** [`cache/redis_cache.py`](src/gernas_rag/cache/redis_cache.py).

- Caches whole retrieval responses in **Redis / Valkey**, keyed by a SHA-256 hash of
  the full request JSON (so different filters/`top_k`/`generate_answer` are distinct
  keys). TTL defaults to 900 s.
- Writes happen as a FastAPI **background task** so the response isn't delayed by the
  cache write; cache hits return immediately with `cache_hit=true`.
- **Fails soft:** any Redis error (or `enabled=false`) degrades to a normal cache miss,
  never an error.
- **Why:** policy queries are highly repetitive (many users ask the same canonical
  questions). Caching avoids re-running the embed → search → rerank → LLM pipeline,
  which dominates latency and (for the LLM step) cost. **Valkey** is used as the
  drop-in, BSD-licensed Redis fork.

### 13. Evaluation (RAGAS)

**Files:** [`evaluation/`](src/gernas_rag/evaluation/) — `evaluator.py`,
`test_dataset.py`, `metrics.py`; CLI [`scripts/run_evaluation.py`](scripts/run_evaluation.py);
endpoints in [`api/routers/evaluate.py`](src/gernas_rag/api/routers/evaluate.py).

- **RAGAS** scores the RAG system end-to-end. Two modes:
  - **Reference-based** (default): `faithfulness` (≥0.85), `answer_relevancy` (≥0.80),
    `context_precision` (≥0.75), `context_recall` (≥0.80) — needs a `ground_truth`.
  - **Reference-free**: `faithfulness`, `answer_relevancy`, `context_utilization`
    (the no-reference variant of precision) — runs over arbitrary/production queries
    with no gold answer.
- A curated **7-question FAB test set** ([`test_dataset.py`](src/gernas_rag/evaluation/test_dataset.py))
  covers pricing floors, approval authority, CBUAE AI governance, MRM evidence,
  concentration limits, eligible tenors, and submission documentation.
- The evaluator **reuses the already-loaded embedder** (via a sync `_EmbeddingsBridge`
  that runs the async coroutine in a worker thread) so it doesn't load a second copy.
  The **LLM judge** is a *separate, smaller* model (`llama-3.1-8b-instant`) to stay
  within free-tier token limits, and contexts are truncated to keep judge prompts small.
- A single-answer endpoint (`/evaluate/answer`) scores one live Q+A+contexts triple
  reference-free in ~10–30 s, for in-UI "rate this answer" feedback.
- **Why RAGAS:** it provides RAG-specific, LLM-as-judge metrics that separately measure
  **retrieval quality** (precision/recall/utilization) from **generation quality**
  (faithfulness/relevancy) — so a regression can be localised to the retriever or the
  generator. The pass/fail thresholds turn evaluation into a CI-style gate.

### 14. API layer

**Files:** [`main.py`](src/gernas_rag/main.py), [`api/`](src/gernas_rag/api/) —
`deps.py`, `auth.py`, `middleware.py`, and routers under `api/routers/`.

Endpoints:

| Method & path                       | Router   | Purpose                                                 |
| ----------------------------------- | -------- | ------------------------------------------------------- |
| `GET /health`                     | health   | Liveness (process up).                                  |
| `GET /ready`                      | health   | Readiness (vector DB reachable).                        |
| `POST /api/v1/retrieve`           | retrieve | Hybrid retrieval (+ optional LLM answer), cached.       |
| `POST /api/v1/ingest`             | ingest   | Upload a document; async ingestion; returns `job_id`. |
| `GET /api/v1/ingest/{job_id}`     | ingest   | Poll ingestion job status.                              |
| `POST /api/v1/evaluate`           | evaluate | Run RAGAS over the FAB test set.                        |
| `POST /api/v1/evaluate/answer`    | evaluate | Reference-free score for one answer.                    |
| `GET /api/v1/evaluate/test-cases` | evaluate | Return the 7 test cases.                                |
| `POST /api/v1/admin/reindex`      | admin    | Drop + recreate the collection.                         |
| `DELETE /api/v1/admin/collection` | admin    | Delete the collection.                                  |

- **Auth** ([`auth.py`](src/gernas_rag/api/auth.py)) is tiered: a Bearer **JWT** is
  required if `jwt_secret` is set (production); otherwise a matching **`X-API-Key`** if
  `api_key` is set (dev); otherwise open (local). **Why:** one dependency adapts from
  frictionless local dev to JWT-secured production via config alone.
- **Middleware** ([`middleware.py`](src/gernas_rag/api/middleware.py)): a
  `RequestIDMiddleware` stamps/propagates `X-Request-ID` and binds it to the structured
  log context, and a `StructuredLoggingMiddleware` logs every request with latency.
  CORS is configurable.
- **Async ingestion**: uploads are saved to a temp file and processed in a background
  task tracked in an in-memory `job_id → result` map (the POC store; a durable queue
  would replace it in production).
- **Telemetry**: OpenTelemetry instruments the FastAPI app
  ([`utils/telemetry.py`](src/gernas_rag/utils/telemetry.py)).

### 15. Cross-cutting utilities

**Files:** [`utils/`](src/gernas_rag/utils/) — `retry.py`, `hashing.py`,
`telemetry.py`, `logging.py`.

- **`async_retry`** — decorator adding exponential backoff (1s, 2s, 4s…) to async
  calls; wraps every Qdrant read/write and LLM call. **Why:** transient network/
  rate-limit blips shouldn't fail a request.
- **`hashing`** — deterministic chunk IDs (MD5) and Qdrant point UUIDs (UUIDv5);
  the foundation of idempotent ingestion.
- **`logging`** — **structlog** structured JSON logging, so every log line is a queryable
  event with bound `request_id` context.
- **`telemetry`** — OpenTelemetry tracing setup.

### 16. Frontend

**Files:** [`frontend/`](frontend/) — see [`frontend/README.md`](frontend/README.md).

- A **React 18 + TypeScript (strict) + Vite 5** SPA, styled with **Tailwind CSS**,
  server state via **TanStack Query**, routing via **React Router**, HTTP via a shared
  **Axios** client. It replaced an earlier Streamlit prototype.
- Four pages: **Search** (hybrid retrieval with answer toggle, top-k slider, doc-type
  filter, latency/cache/freshness metrics, per-chunk parent/child tabs), **Upload**
  (drag-and-drop ingestion with job-status polling), **Evaluation** (RAGAS scorecards),
  and **Admin** (readiness, reindex, delete with confirm dialogs).
- In dev, Vite **proxies** `/api`, `/health`, `/ready` to the backend so requests are
  same-origin and CORS is bypassed. The `VITE_API_KEY` is sent as `X-API-Key`.
- **Why React over the original Streamlit:** an interactive, production-grade SPA gives
  finer control over UX (streaming results, per-chunk drill-down, confirm dialogs) than
  Streamlit's re-run-the-script model, and decouples the UI from the Python process.

---

## Technology choices & rejected alternatives

| Layer      | **Chosen (default)**                                      | Alternatives (in repo, config-swappable)                   | Why the default                                             |
| ---------- | --------------------------------------------------------------- | ---------------------------------------------------------- | ----------------------------------------------------------- |
| Embeddings | **BAAI/bge-m3** (dense **+** SPLADE sparse, 1 pass) | sentence-transformers (dense-only); OpenAI-compat          | One self-hosted model for both vectors; data stays on-prem  |
| Vector DB  | **Qdrant** (native named-vector hybrid)                   | Milvus (billion-scale, heavier infra); ChromaDB (dev only) | Production hybrid + filtering without operational overkill  |
| Fusion     | **Reciprocal Rank Fusion** (`k=60`)                     | weighted score fusion (config-only)                        | Rank-based → no score calibration needed; robust, standard |
| Reranker   | **bge-reranker-v2-m3** (cross-encoder)                    | none / disabled                                            | Joint query-doc scoring; precision on the final few         |
| Extraction | **Docling** (structure-preserving)                        | Unstructured (OCR/scanned); PyMuPDF (fast/flat)            | Preserves headings/clauses that chunking depends on         |
| Chunking   | **Hierarchical** (parent/child)                           | Fixed-size                                                 | Semantic boundaries + small-to-big retrieval                |
| LLM        | **Groq llama-3.1-70b**                                    | Anthropic; HuggingFace (on-prem); OpenAI-compat            | Low latency + low cost for the POC; provider is a flag      |
| Cache      | **Redis / Valkey**                                        | —                                                         | Repetitive queries; soft-fail                               |
| Eval       | **RAGAS** (judge: llama-3.1-8b)                           | —                                                         | RAG-specific, separates retrieval vs generation quality     |
| Logging    | **structlog** (JSON) + OpenTelemetry                      | —                                                         | Queryable events + tracing                                  |

The recurring theme: **the default optimises for a bank's constraints** (data residency,
auditability, cost, operational simplicity), and **every choice is reversible via config**
because the procurement/compliance decision may differ per environment or arrive later.

---

## Setup & operations

### Backend

```bash
cp .env.example .env
# Edit .env — set RAG__LLM__GROQ_API_KEY at minimum (and API_KEY for auth)
uv venv
uv sync
docker compose up -d qdrant redis      # or set RAG__VECTORDB__QDRANT_PATH for embedded mode
```

### Ingest documents

```bash
python scripts/ingest_docs.py \
  --path ./docs \
  --document-type pricing_policy \
  --product-applicability 'corporate_loan,revolving_credit' \
  --effective-date '2024-06-01'
# Omit --document-type to auto-infer from filename/content.
```

### Run the API

```bash
uvicorn gernas_rag.main:app --reload --app-dir src
# OpenAPI docs (when DEBUG=true): http://localhost:8000/docs
```

### Test retrieval

```bash
curl -X POST http://localhost:8000/api/v1/retrieve \
  -H 'X-API-Key: dev-secret-key-change-in-production' \
  -H 'Content-Type: application/json' \
  -d '{"query": "pricing floor BB-rated 4-year AED loan", "top_k": 5, "generate_answer": true}'
```

### Run evaluation

```bash
python scripts/run_evaluation.py                  # full (needs ground truth)
python scripts/run_evaluation.py --reference-free # no ground truth required
```

### Run the frontend

```bash
cd frontend
cp .env.example .env       # set VITE_API_KEY to match backend API_KEY
npm install && npm run dev # http://localhost:5173
```

### Switch vector DB / embedding model / LLM (no code change)

```bash
# .env
RAG__VECTORDB__PROVIDER=milvus
RAG__VECTORDB__MILVUS_HOST=localhost

RAG__EMBEDDING__PROVIDER=sentence_transformer
RAG__EMBEDDING__MODEL_NAME=intfloat/e5-large-v2

RAG__LLM__PROVIDER=anthropic
RAG__LLM__ANTHROPIC_API_KEY=sk-ant-...
```

### Full stack via Docker Compose

```bash
docker compose up -d        # qdrant + valkey + rag-api
# Milvus variant:
docker compose -f docker-compose.milvus.yml up -d
```

### Tests

```bash
uv run pytest               # unit + integration
```

---

## Project layout

```
src/gernas_rag/
├── config/          # Pydantic settings + per-domain sub-configs
├── models/          # domain models (Chunk, Document, Retrieval, Ingestion)
├── extraction/      # Docling (primary), Unstructured, PyMuPDF + factory
├── chunking/        # hierarchical (primary), fixed-size + factory
├── ingestion/       # IngestionPipeline + MetadataExtractor
├── embeddings/      # BGE-M3 (primary), sentence-transformers + factory
├── vectordb/        # Qdrant (primary), Milvus, ChromaDB + factory
├── retrieval/       # HybridSearcher, Reranker, FreshnessFilter, RetrievalPipeline
├── generation/      # ResponseGenerator (grounded, cited prompt)
├── llm/             # Groq (primary), Anthropic, HuggingFace, OpenAI-compat + factory
├── cache/           # RAGCache (Redis/Valkey)
├── evaluation/      # RAGEvaluator, FAB test set, metric thresholds
├── api/             # FastAPI app: routers, deps, auth, middleware
└── utils/           # retry, hashing, structured logging, telemetry

config/              # default/<env>/local YAML layers
docs/                # mock FAB corpus (PDFs)
scripts/             # ingest, evaluate, setup, inspect CLIs
tests/               # unit/ + integration/
frontend/            # React + TypeScript SPA
```

See module docstrings for per-file detail; the factories (`*/factory.py`) are the entry
points for adding a new provider.
