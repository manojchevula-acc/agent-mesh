# Semantic Response Cache — Complete Guide (Plain English)

> **Who this is for:** Anyone who wants to understand how the AgentMesh cache works — no prior knowledge of AI, embeddings, or databases required. Every technical term is explained the first time it appears, and every step uses real examples.

---

## 1. What Problem Does This Solve?

Imagine you work at a bank call center. Every time a customer calls and asks *"What is CUST001's credit limit?"*, your colleague has to spend 30 seconds looking it up, running calculations, and drafting an answer.

Now imagine a **smart notepad** that remembers every question and answer. The next time someone asks nearly the same thing, it just reads from the notepad instead of doing the full 30-second lookup. That is exactly what this cache does.

| Without cache | With cache |
|---|---|
| Every question → full AI pipeline → 5–70 seconds | Similar question seen before → serve saved answer → ~50 ms |
| 2–4 AI model calls per question | 0 AI model calls on a cache hit |
| High cost, slow response | Near-instant, free |

---

## 2. The Big Picture — How It Fits In

Before the system runs the expensive AI pipeline, it first checks: *"Have we answered something like this before?"*

```
User asks a question
        ↓
  Who are you? (RBAC)         ← checks your role: relationship_manager, credit_officer, etc.
        ↓
  Cache Check                 ← "Did we answer something like this before?"
        ↓               ↓
   YES (hit)          NO (miss)
        ↓               ↓
 Show saved answer   Run full AI pipeline → save the new answer
```

The cache sits **after** the identity check (so it knows your role) and **before** the expensive AI steps (so it can skip them entirely on a hit).

---

## 3. Core Concept: How Does the Cache "Understand" Questions?

### 3.1 Turning words into numbers (Embeddings)

A computer cannot directly compare two sentences for meaning. So we use a technique called **embedding** — converting a sentence into a list of 384 numbers (called a **vector**) that represents its *meaning*.

**Real example:**

| Sentence | What it becomes (simplified) |
|---|---|
| "What is CUST001's credit limit?" | `[0.23, -0.41, 0.87, 0.12, ...]` — 384 numbers |
| "Tell me the credit ceiling for CUST001" | `[0.25, -0.39, 0.85, 0.11, ...]` — very similar numbers |
| "What is today's weather?" | `[0.91, 0.02, -0.33, 0.77, ...]` — very different numbers |

Two sentences that *mean the same thing* produce vectors that are **close together** in space. Two sentences about totally different topics produce vectors that are **far apart**.

> **No LLM used here.** The embedding model (`all-MiniLM-L6-v2`) is a small, fast, local model — not a large language model. It runs in ~50 ms with no API call and no network dependency.

### 3.2 Measuring closeness (Cosine Similarity)

Once we have two vectors, we measure how similar they are using a score called **cosine similarity**:

```
0.0 = completely different (like comparing apples to astronomy)
0.5 = somewhat related
0.75 = quite similar
0.92 = nearly identical meaning
1.0 = exact same sentence
```

Think of it like measuring the angle between two arrows pointing in a room:
- Arrows pointing in the same direction → angle = 0° → similarity = 1.0
- Arrows pointing opposite directions → angle = 180° → similarity = 0.0

> **No LLM used here either.** This is pure math — a formula applied to the two number lists. It takes microseconds.

### 3.3 Where answers are stored (ChromaDB)

**ChromaDB** is a special database designed to store vectors (those lists of numbers) and search through them by meaning — not by exact keyword match.

Think of it like a library where books are arranged not alphabetically, but by *topic similarity*. You walk in and say "I want something about credit limits" and it leads you to the nearest shelf — even if no book title contains those exact words.

Every saved answer in ChromaDB contains:
- The original question (as text + as a vector)
- The answer
- Your role (so answers never leak between roles)
- When it was saved
- Which customer/entity it was about
- Other metadata for filtering

---

## 4. Complete Journey of a Real Query — Step by Step

Let's trace **one real query** through every single step.

**Scenario:** Sarah is a Relationship Manager. She types:
> *"What pricing should I recommend for customer CUST001?"*

Previously, someone else asked:
> *"Show me recommended pricing for CUST001"*  ← already in the cache

---

### Step 1: Query arrives, role is confirmed

Sarah logs in. The system knows she is a `relationship_manager`. The cache will only look at answers saved for that role — a credit officer's answers are in a completely separate space.

```
Sarah's query: "What pricing should I recommend for customer CUST001?"
Role confirmed: relationship_manager
```

> **No LLM used.** Role comes from the authentication/RBAC system.

---

### Step 2: Query Templating — Replace IDs with placeholders before embedding

This step is also called **canonicalization** in the code. Before the query is turned into a vector, specific identifiers are replaced with generic placeholder slots — turning the query into a **template**.

```
Raw query:       "What pricing should I recommend for customer CUST001?"
After templating: "What pricing should I recommend for customer <CUSTOMER_ID>?"
```

The three placeholder types (from the code in `entity_extractor.py`):

| Pattern matched | Replaced with | Example |
|---|---|---|
| `CUST001`, `CUST_007`, `cust-42` | `<CUSTOMER_ID>` | "profile for CUST001" → "profile for <CUSTOMER_ID>" |
| `ACC001`, `ACC-4421` | `<ACCOUNT>` | "balance for ACC-4421" → "balance for <ACCOUNT>" |
| `DEAL99`, `DEAL-007` | `<DEAL>` | "status of DEAL-007" → "status of <DEAL>" |

**The crucial split — what gets embedded vs what gets stored:**

```
User types:  "What pricing should I recommend for customer CUST001?"
                                │
                    ┌───────────┴────────────┐
                    │                        │
          EMBED THIS (the template):    STORE THIS (the raw text):
          "What pricing should I        "What pricing should I
           recommend for                 recommend for
           <CUSTOMER_ID>?"               customer CUST001?"
                    │                        │
           Used for matching            Shown to user in banner
           against other queries        Keyed in ChromaDB
```

This split is intentional:
- The **template** (with placeholder) gets a vector that matches other phrasings of the same intent, regardless of which customer ID was asked about
- The **raw query** is what gets saved and shown to the user — so the banner shows the original question, not the placeholder version

**Why this matters — with and without templating:**

```
Without templating (CACHE_CANONICALIZE_ENABLED=false):
  "CUST001 margin?"                    → vector A
  "what is the margin for CUST001?"    → vector B
  Similarity A↔B: 0.82 → gray zone → LLM judge needed

With templating (CACHE_CANONICALIZE_ENABLED=true):
  "<CUSTOMER_ID> margin?"              → vector A'
  "what is the margin for <CUSTOMER_ID>?" → vector B'
  Similarity A'↔B': 0.96 → HIT zone → no judge needed
```

The template strips out the "noise" of different phrasings and leaves only the pure intent in the vector.

> **No LLM used here at all.** Query templating is purely regex-based — the same patterns that detect `CUST001`, `ACC001`, `DEAL001` are used to replace them with placeholders. This runs in under 1ms with zero API calls. It's completely deterministic.

> **Important:** Both the lookup query AND stored queries must be templated the same way. If you turn this on after entries are already stored (with raw-text vectors), the old vectors will no longer match — the similarity scores will drop and everything will appear as a MISS. You must re-embed everything with `python -m src.cache.ingest_pipeline --overwrite` before turning this on.

---

### Step 3: Embedding — Turn the canonical query into a vector

The canonical query is fed into the embedding model:

```
Input:  "What pricing should I recommend for customer <CUSTOMER_ID>?"
Output: [0.31, -0.44, 0.72, 0.18, 0.09, ...] — 384 numbers
```

This vector is what gets searched against ChromaDB.

> **No LLM used.** The embedding model (`all-MiniLM-L6-v2`) is a small, local model bundled with ChromaDB. No API call, no network.

---

### Step 4: ChromaDB Search — Find the closest stored questions

ChromaDB searches through all stored questions (for `relationship_manager` role only) and returns the top 3 most similar ones, ranked by cosine similarity.

```
Results returned:
  #1  "Show me recommended pricing for CUST001"        similarity: 0.96
  #2  "Pricing recommendation for CUST001 please"      similarity: 0.83
  #3  "Give me CUST001 deal pricing"                   similarity: 0.78
```

> **No LLM used.** This is a vector database search — pure math (cosine similarity between vectors). ChromaDB handles it entirely.

---

### Step 5: Entity Gate — Check if it's really the same customer

The similarity score of 0.96 looks great. But wait — what if Sarah was asking about CUST002 and the stored question was about CUST001? The vectors would still be very similar (same intent, almost same wording) but the answer would be **wrong**.

The entity gate prevents this by extracting the specific IDs from both questions and comparing them:

```
Sarah's query entity:   {CUST001}
Stored query entity:    {CUST001}
Match? ✓ YES → proceed
```

Now imagine a different user asked about CUST002:

```
User's query entity:    {CUST002}
Stored query entity:    {CUST001}
Match? ✗ NO → MISS (run full pipeline fresh)
```

The entity gate has two modes:
- **Hard mode** (default): mismatch → immediate MISS, no further checks
- **Soft mode**: mismatch → demote to "gray zone", ask the LLM judge

> **LLM used? Sometimes.** Normally yes — an LLM extracts entities like customer IDs, account numbers, people's names, time periods, and amounts from the query. But if the LLM is slow or unavailable, a **regex fallback** catches structured IDs like `CUST001`, `ACC-4421`, `DEAL-99` instantly with no API call. For the most common collision case (one ID vs another), regex is sufficient.

---

### Step 6: Similarity Zone Decision

Now that entities match, the similarity score (0.96) determines what happens next:

```
0.0           0.75              0.85              0.92            1.0
 |─────────────|──────────────────|──────────────────|──────────────|
      MISS         Gray Zone           Intent Match        HIT
   Run fresh    Uncertain —         Probably the        Very likely
                Ask the judge       same thing          the same
```

Sarah's query scored **0.96** → falls in the **HIT** zone.

```
Score 0.96 → HIT zone (≥ 0.92)
Action: Show Sarah the cached answer with a confirmation banner
```

The four zones in plain English:

| Zone | Score | What it means | What happens |
|---|---|---|---|
| **MISS** | below 0.75 | Too different — fresh answer needed | Run full AI pipeline |
| **Gray Zone** | 0.75 – 0.85 | Might be the same, might not — unclear | Show banner + ask LLM judge in background |
| **Intent Match** | 0.85 – 0.92 | Probably the same intent | Show suggestion banner |
| **HIT** | 0.92 and above | Very likely the same question | Show suggestion banner |

> **No LLM used for zone decision.** This is just comparing the similarity number to three threshold values. Pure conditional logic.

---

### Step 7: Suggestion Banner — Sarah Decides

The system shows Sarah a violet banner above the answer area:

```
┌──────────────────────────────────────────────────────────────┐
│  ◈ Similar questions already answered        60s → auto fresh │
│                                                               │
│  #1  "Show me recommended pricing for CUST001"   96%  2.1h   │
│      [Use this answer]                                        │
│                                                               │
│  #2  "Pricing recommendation for CUST001 please" 83%  5.4h   │
│      LLM: ✓ Same customer and intent, wording differs         │
│      [Use this answer]                                        │
│                                                               │
│  #3  "Give me CUST001 deal pricing"              78%  0.8h   │
│      LLM: ⟳ Checking match…                                   │
│      [Use this answer]                                        │
│                                                               │
│                         [Run fresh — full pipeline]           │
└──────────────────────────────────────────────────────────────┘
```

Sarah clicks **"Use this answer"** on candidate #1.

```
Sarah accepts → Serve saved answer → Done in ~50ms
```

If Sarah clicks **"Run fresh"** → the full AI pipeline runs and a new answer is generated.

> **No LLM used to serve the cached answer.** The answer is read directly from ChromaDB. The LLM judge (shown on candidates #2 and #3 above) runs in the **background** for gray-zone candidates only — it does not block Sarah from seeing or using the cached results.

---

### Step 8 (if MISS): Full pipeline runs and answer is stored

If the cache had no good match (score below 0.75), or Sarah rejected all suggestions:

```
Full pipeline:
  → Compliance check
  → Domain Agent AI (the expensive 5–70 second LLM call)
  → Redact sensitive data (PII removal)
  → Store new answer in ChromaDB for future use
```

> **LLM used here:** Yes, this is the normal AI pipeline — multiple model calls to answer the question properly.

---

## 5. Full Flow Diagram With Real Query

```
Sarah types: "What pricing should I recommend for customer CUST001?"
                              │
                              ▼
              ┌───────────────────────────┐
              │  RBAC: role confirmed     │   No LLM — auth system
              │  role = relationship_mgr  │
              └───────────────┬───────────┘
                              │
                              ▼
              ┌───────────────────────────┐
              │  Canonicalization         │   LLM or regex
              │  CUST001 → <CUSTOMER_ID>  │   "What pricing should I
              │                           │    recommend for <CUSTOMER_ID>?"
              └───────────────┬───────────┘
                              │
                              ▼
              ┌───────────────────────────┐
              │  Embedding                │   Small local model, no API
              │  Sentence → 384 numbers   │   ~50ms
              └───────────────┬───────────┘
                              │
                              ▼
              ┌───────────────────────────┐
              │  ChromaDB Search          │   No LLM — vector math
              │  Top 3 similar entries    │   Role-filtered
              │  for relationship_mgr     │
              └───────────────┬───────────┘
                              │
                     ┌────────┴─────────┐
                     │                  │
               Candidates            No candidates
               found (top-1          found
               sim = 0.96)               │
                     │                   ▼
                     ▼            ┌─────────────┐
        ┌────────────────────┐    │ MISS         │
        │  Entity Gate        │    │ Run full     │
        │  Query: {CUST001}  │    │ pipeline     │
        │  Stored: {CUST001} │    └─────────────┘
        │  Match: ✓ YES      │
        └──────────┬──────────┘
                   │
         Entity mismatch?
         (e.g. CUST002 ≠ CUST001)
                   │ No mismatch
                   ▼
        ┌────────────────────┐
        │  Zone Decision      │   No LLM — just compare
        │  Score 0.96 ≥ 0.92 │   number to thresholds
        │  → HIT zone        │
        └──────────┬──────────┘
                   │
                   ▼
        ┌────────────────────┐
        │  Show Banner        │   No LLM — UI component
        │  3 candidates shown │
        │  Gray-zone ones get │   LLM judge in background
        │  LLM judge in BG   │   (advisory, non-blocking)
        └──────────┬──────────┘
                   │
          ┌────────┴──────────┐
          │                   │
      Sarah accepts        Sarah rejects
          │                   │
          ▼                   ▼
   Serve cached answer    Run full pipeline
   0 LLM calls           LLM calls run
   ~50ms                 5–70 seconds
   Save result
```

---

## 6. What Happens With CUST002 — The Entity Gate in Action

This is the most important safety feature. Without it, the cache would serve the wrong customer's data.

```
User asks: "What pricing should I recommend for customer CUST002?"

Step 1 — Embed (canonical: "...for <CUSTOMER_ID>?")
  → Vector almost identical to the CUST001 query vector
  → Similarity score: 0.95 ← looks like a HIT!

Step 2 — Entity Gate runs
  Query entity:   {CUST002}
  Stored entity:  {CUST001}
  ✗ MISMATCH

Step 3 — Hard mode: MISS
  → Cached answer is dropped
  → Full AI pipeline runs for CUST002
  → Fresh, correct answer served
  → New CUST002 entry saved in ChromaDB
```

**Without the entity gate:** CUST002 would silently receive CUST001's pricing — a serious data leakage bug.

**With the entity gate:** The mismatch is caught in milliseconds. The entity gate adds no noticeable delay because regex extraction is instant.

---

## 7. The Gray Zone — When It's Unclear

Sometimes a question scores between 0.75 and 0.85 — similar enough to be worth checking, but not obviously the same. These go to the **LLM Judge**.

**Example:**

| Question | Score | Situation |
|---|---|---|
| Stored: "What is Alice's credit limit?" | — | Already answered |
| New: "Has Alice's credit limit changed recently?" | ~0.80 | Similar words but DIFFERENT question |
| New: "List Alice's credit limit" | ~0.82 | Different wording but SAME question |

A simple similarity score cannot tell these two apart. Both score ~0.80. The **LLM Judge** reads both questions together and decides:

**LLM Judge prompt (simplified):**
```
Stored question: "What is Alice's credit limit?"
New question:    "Has Alice's credit limit changed recently?"
Cached answer:   "Alice's credit limit is $50,000..."

Does the cached answer fully address the new question?
Answer YES or NO with a short reason (max 12 words).
```

**LLM Judge response:**
```
NO: asks about recent changes, not the current value
```
→ Cache miss. Fresh pipeline runs.

For the other case:
```
New question: "List Alice's credit limit"
→ YES: same customer and intent, only wording differs
```
→ Cached answer served.

> **LLM used here: YES.** This is the one place in the cache pipeline where a real language model (e.g., `gemma-4-31b`) makes a judgment call. It uses very few tokens (max 60 output tokens), set to temperature 0 (fully deterministic), and has a 5-second timeout. If it times out, the system falls back to a MISS safely — it never hangs.

**Why not use an LLM for everything?** Because it takes 300–600ms and costs API tokens. For the HIT zone (≥0.92) and MISS zone (<0.75) the answer is obvious from the score alone — using an LLM there would be wasteful. The judge is reserved for the ~15% of queries that land in the genuinely uncertain middle.

---

## 8. The Phases — Problems Each One Solves

The cache was built in stages. Each phase fixes one specific weakness.

---

### Phase 1 — Entity Gate (Default: ON)

**Problem it fixes:** "show profile for CUST001" and "show profile for CUST002" produce nearly identical vectors. Without a check, CUST001's data would be served for CUST002.

**How it works:**
1. LLM (or regex) extracts identifiers from the incoming question: `{CUST001}`
2. The stored entry already has its identifiers saved: `{CUST001}`
3. Compare them — exact set match required
4. Mismatch → MISS (hard mode) or demote to gray zone (soft mode)

**Real example:**
```
Query A:  "show risk score for CUST001"  → {CUST001}
Query B:  "show risk score for CUST002"  → {CUST002}

Vector similarity: 0.97 (nearly identical text)
Entity match:      {CUST001} ≠ {CUST002} → MISS
Outcome: Query B runs fresh, gets CUST002's actual data
```

> **LLM used:** Yes for rich entity extraction (IDs + names + time periods + amounts). Regex fallback for structured IDs when LLM is unavailable.

---

### Phase 2 — Query Templating / Canonicalization (Default: OFF)

Also called "query templating" — this is the technique of turning a specific question into a **template** by replacing the concrete IDs with generic placeholders before computing the vector.

**Problem it fixes:** Two questions with different wording but identical intent produce different vectors, causing a valid cache hit to be missed.

```
"pricing for CUST001"                          → vector X
"what pricing should I recommend for CUST001"  → vector Y

Similarity(X, Y): 0.82 → falls in gray zone → LLM judge gets called → slower
```

Both questions clearly mean the same thing, but the embedding model sees different word patterns and produces slightly different vectors.

**How it works — the template approach:**

The function `canonicalize_query()` in `entity_extractor.py` uses regex to replace known ID patterns with typed placeholder slots:

```python
# These are the actual patterns from the code:
"customer_ids" → CUST001, CUST_007, cust-42   →  <CUSTOMER_ID>
"accounts"     → ACC001, ACC-4421              →  <ACCOUNT>
"deals"        → DEAL99, DEAL-007              →  <DEAL>
```

This runs before the embedding step inside `_embed_query()` in `semantic_cache.py`:

```python
def _embed_query(self, query: str) -> list[float]:
    text = query
    if Config.CACHE_CANONICALIZE_ENABLED:
        text = canonicalize_query(query)   # template it first
    return self._embed(text)              # then embed the template
```

**What changes, what stays the same:**

```
Original query:   "What pricing should I recommend for customer CUST001?"
                                          │
                         ┌────────────────┴─────────────────┐
                         │                                   │
                 EMBEDDED as template:              STORED as raw text:
                 "What pricing should I             "What pricing should I
                  recommend for customer             recommend for customer
                  <CUSTOMER_ID>?"                    CUST001?"
                         │                                   │
                  Makes similar paraphrases         Shown in the banner
                  of same intent cluster            Saved in ChromaDB
                  tightly in vector space           as the document
```

**Why this teamwork with Phase 1 (entity gate) is safe:**

You might worry: "If both CUST001 and CUST002 questions now produce the same template, won't CUST002 get CUST001's answer?"

No — because Phase 1 (entity gate) still extracts and compares the actual IDs separately. Even if templating makes the vectors nearly identical (high similarity score), the entity gate checks: are the raw customer IDs the same? Different IDs → MISS regardless of vector score.

```
CUST001 question  →  template "...for <CUSTOMER_ID>"  →  vector V
CUST002 question  →  template "...for <CUSTOMER_ID>"  →  vector V (same!)
                                                              │
                                                    Similarity: 0.99 ← looks like HIT
                                                              │
                                                    Entity gate:
                                                    {CUST001} ≠ {CUST002} → MISS ✓
```

Templating improves recall (finds more matches for the same customer); entity gate preserves precision (never serves wrong customer's data). They work as a team.

**Full before-vs-after example:**

```
Three questions about CUST001, asked by the same user on different days:
  A: "CUST001 margin?"
  B: "what is the margin for CUST001?"
  C: "tell me CUST001's margin percentage"

Without templating:
  Similarity(A, B): 0.82 → gray zone → judge runs
  Similarity(A, C): 0.79 → gray zone → judge runs
  Each paraphrase needs a judge call (~300ms each)

With templating:
  A template: "<CUSTOMER_ID> margin?"
  B template: "what is the margin for <CUSTOMER_ID>?"
  C template: "tell me <CUSTOMER_ID>'s margin percentage"
  Similarity(A', B'): 0.96 → HIT zone → served instantly
  Similarity(A', C'): 0.94 → HIT zone → served instantly
  No judge calls needed — 0ms overhead
```

> **No LLM used at all.** Purely regex. The same patterns used by entity extraction are re-used here — but instead of pulling out the values, they're replaced with placeholder strings. Zero API calls, under 1ms, completely deterministic.

> **Warning — re-embedding required:** If you turn this on after entries are already stored, those old entries were embedded using raw text (without placeholders). Their vectors will NOT match the new template-based vectors — everything looks like a MISS. You must re-embed and re-store everything: `python -m src.cache.ingest_pipeline --overwrite`. This re-reads all conversation files, re-applies templating, re-embeds, and overwrites the old vectors.

---

### Phase 2b — Ingest-Time Paraphrase Augmentation (Default: OFF)

**Problem it fixes:** Even with canonicalization, a user phrasing a question in an unusual way ("give me the ceiling rate for CUST001") might not score high enough against a stored query ("pricing for CUST001") to land in the HIT zone. The similarity is good but not great — ending up in the gray zone and triggering an expensive LLM judge call.

**How it works:** At **ingest time only**, for each Q/A pair that gets stored, the pipeline calls an LLM to generate N alternative phrasings of the same query. Each paraphrase is stored as a **separate ChromaDB entry pointing to the same answer**. The query-time path is completely unchanged — one incoming query, one vector lookup.

```
Ingest time (once, per Q/A pair):
  Original:      "pricing for CUST001"            → stored → vector A
  Paraphrase 1:  "what is the recommended price for CUST001" → stored → vector B
  Paraphrase 2:  "show me pricing for CUST001"    → stored → vector C
  Paraphrase 3:  "CUST001 pricing recommendation" → stored → vector D
  All four point to the same answer text in ChromaDB.

Query time (normal lookup — no change):
  User asks: "give me the ceiling rate for CUST001"
  → vector lands close to B or C → HIT zone → served in ~50ms
  (without augmentation: gray zone → judge call → 300ms+)
```

**Safety properties:**
- **Entity gate still fires** — all paraphrases carry the same `entities` signature (`customer_id:cust001`). A CUST002 query is still rejected regardless of how close the vector is.
- **Role isolation preserved** — paraphrases are stored with the same `role` field; ChromaDB `where role=X` filter still applies.
- **Canonicalization still applies** — `store()` calls `_embed_query()` which applies `canonicalize_query()` before vectorizing, so paraphrases also get `<CUSTOMER_ID>` substituted.
- **Idempotent** — each paraphrase gets its own `_doc_id(role, para)` (SHA256 of `role::paraphrase_text`). Re-running ingest with `--overwrite` replaces them; without `--overwrite` they are skipped if already present.

**Rate limiting:** The pipeline sleeps `CACHE_PARAPHRASE_DELAY_S` seconds after each paraphrase call. On HTTP 429 (rate limit), it retries up to 3 times with exponential backoff (10s → 20s → 40s). A failed paraphrase call never loses the original entry — it was already stored before paraphrases are attempted.

**Real ingest run result (farida_fa786044.jsonl — 5 files, 15 Q/A pairs):**
```
total_scanned:     15
newly_stored:      12   ← original entries
paraphrases_stored: 60  ← 5 paraphrases × 12 entries
skipped_cache_hit:  3   ← correctly skipped (cache_hit=true in JSONL)
errors:            []
Total ChromaDB entries after: 86
```

> **LLM used: YES — at ingest time only.** Uses the same OpenAI-compatible provider as the rest of the mesh (`LLM_BASE_URL` + `GROQ_API_KEY` + `GROQ_MODEL`). No separate API key required. Cost is paid once per entry at ingest, never at query time.

---

### Phase 3 — Hybrid Dense + Sparse Retrieval (Default: OFF, Experimental)

**Problem it fixes:** Sometimes a rare, important keyword barely moves the vector. For example, a policy code like `FAB-CRP-CONC-2024` is a very unusual string — the embedding model has rarely seen it, so it has weak influence on the vector. The right cached answer might rank poorly.

**How it works:** Two searches run in parallel and their results are blended:

1. **Dense search** (meaning-based): Embedding similarity — what we've described above
2. **Sparse search** (keyword-based, called BM25): Classic word-matching, like a search engine — finds entries containing the exact keyword

The two ranked lists are then merged using **Reciprocal Rank Fusion (RRF)** — a formula that combines rankings from both methods. An entry that scored middling on meaning but is an exact keyword match gets lifted up.

**Real example:**
```
Query: "FAB-CRP-CONC-2024 concentration limits"

Dense search ranking:
  #1  "credit concentration guidelines"       (0.83 — good meaning match)
  #2  "FAB-CRP-CONC-2024 exposure limits"     (0.79 — exact match buried low)

Sparse/BM25 search ranking:
  #1  "FAB-CRP-CONC-2024 exposure limits"     (perfect keyword hit)
  #2  "credit concentration guidelines"       (no keyword match)

After RRF fusion:
  #1  "FAB-CRP-CONC-2024 exposure limits"     ← lifted to top by keyword match
  #2  "credit concentration guidelines"
```

> **No LLM used.** BM25 is a pure math formula over word frequencies. RRF is also pure math. No model calls.

---

### Phase 4 — Cross-Encoder Reranker (Default: ON)

**Problem it fixes:** The embedding model scores each question *in isolation* — it embeds the new question once, embeds stored questions once each, then compares. This is fast but can misorder close candidates. When two candidates look equally similar, it can put the less relevant one first.

Also, the LLM judge was slow (300–600ms remote call) and could fail on rate limits or network errors.

**How it works:** After the initial similarity shortlist, a **cross-encoder** model reads the new question and one candidate *together* (as a pair) and outputs a relevance score. This is far more accurate because the model can compare them directly.

```
Bi-encoder (old way — fast but less precise):
  Embed "what is CUST001's exposure limit?"     → vector X
  Embed "CUST001 credit exposure ceiling"       → vector Y
  Embed "CUST001 risk limit by policy"          → vector Z
  Similarity(X, Y) = 0.88
  Similarity(X, Z) = 0.86
  → Y ranked above Z

Cross-encoder (new way — slower but precise):
  Input: ("what is CUST001's exposure limit?", "CUST001 credit exposure ceiling")
  Output: 0.93 (very relevant)

  Input: ("what is CUST001's exposure limit?", "CUST001 risk limit by policy")
  Output: 0.41 (different framing — drops this candidate)
  → Z dropped before the LLM judge even sees it
```

The cross-encoder runs **locally** — no API call, no network. So it never fails due to rate limits or proxy SSL errors.

> **No LLM used** in the remote-API sense. The cross-encoder is a small local model (downloaded once from HuggingFace, e.g. `cross-encoder/ms-marco-MiniLM-L-6-v2`). It runs on-device. If unavailable, the system falls back to the original dense similarity ordering.

---

### Phase 6 — Observability (Always ON)

**Problem it fixes:** Without data, you cannot know whether the cache thresholds (0.75 / 0.85 / 0.92) are well-tuned. A threshold that's too loose causes wrong answers to be served. A threshold that's too tight causes too many cache misses.

**How it works:** Every cache outcome is recorded as a counter:

```
Cache HIT        → increment hit_count
Cache MISS       → increment miss_count
User accepted    → increment hit_accepted
User rejected    → increment hit_rejected   ← this is your false-positive rate
Entity gate drop → increment entity_gate_drops
```

If `hit_rejected` climbs high → thresholds are too loose → tighten them.
If `miss_count` is very high → thresholds are too tight → loosen them.

These are visible at `GET /api/cache/stats` and also shown in the UI's Execution Trace panel for each request.

> **No LLM used.** Pure counters and timers.

---

### Phase 7 — Negative Answer Guard + Reject Feedback (Default: ON)

**Problem 7a — What it fixes:** If the system answered *"No data found for CUST099"* and that gets cached, then when CUST099's data is later added, everyone asking about CUST099 would still get *"no data found"* — a stale, wrong answer.

**Solution 7a:** Before storing any answer, it checks for phrases like `"no ... data found"`, `"unable to retrieve"`, `"not found"`. If the answer is a non-answer, it is **never stored**.

```
Answer: "Customer CUST099 profile is not available in our system."
→ Detected as negative → skip cache store
→ Next time someone asks about CUST099, the full pipeline runs fresh
```

**Problem 7b — What it fixes:** When a user clicks "Run fresh" instead of accepting a cache suggestion, that rejection is a strong signal the cache was wrong. This signal was being discarded.

**Solution 7b:** An explicit user rejection is logged to `data/cache_rejections.jsonl`:
```json
{
  "ts": "2026-07-30T10:22:00Z",
  "role": "relationship_manager",
  "query": "What pricing should I recommend for CUST001?",
  "chosen_entry_id": "abc123",
  "similarity": 0.93,
  "confidence": "high"
}
```
This file is a record of false positives — cache hits that were actually wrong. Reviewing it helps tune the thresholds or build a "demote list" for specific entries.

The system also distinguishes between:
- **Explicit reject**: user clicked "Run fresh" — logged
- **60-second timeout**: user walked away — not logged (no signal to record)

> **No LLM used.** Negative detection is a regex/keyword check. Logging is a simple file write.

---

### Phase 5 — Event-Driven Invalidation (Deferred — Not Built Yet)

**The idea:** When CUST001's credit rating changes in the underlying database, automatically delete all cached answers about CUST001 so no one gets stale data.

**Why not built yet:** The data source is a static mock service with no "data changed" event. The cache has no delete-by-entity method. The foundation is there (entity signatures are stored per entry), but the event source doesn't exist yet.

For now, staleness is handled by `CACHE_MAX_AGE_HOURS` — answers older than this are never served (default: 144 hours / 6 days).

---

## 9. Complete Pipeline Order

Here is the exact order of operations inside the cache check, with LLM usage called out at each step:

```
1. Dense embedding search (ChromaDB)
   → No LLM. Small local embedding model. ~50ms.

2. Hybrid BM25 fusion (Phase 3, if enabled)
   → No LLM. Math formula. Lifts keyword-match candidates.

3. Entity gate (Phase 1)
   → LLM (or regex fallback). Extracts identifiers. ~100ms with LLM, <1ms with regex.
   → Hard mismatch → MISS immediately. Soft mismatch → demote to gray zone.

4. Cross-encoder rerank (Phase 4, if enabled)
   → Local model, no API. Re-orders candidates by co-reading query+candidate pairs.
   → Drops irrelevant candidates before the LLM judge sees them.

5. LLM Judge (gray zone only, if CACHE_JUDGE_ENABLED=true)
   → LLM (remote API). 300–600ms. Max 60 tokens. Temperature 0.
   → Binary YES/NO decision. Timeout → MISS (safe fallback).

6. Zone decision
   → No LLM. Compare score to three threshold numbers.

7. Show suggestion banner (or serve MISS)
   → No LLM. UI component with user interaction.

8. User accepts → serve cached answer
   → No LLM. Read answer from ChromaDB. ~50ms total.

9. User rejects OR MISS → run full pipeline
   → LLM. The normal expensive 5–70s AI pipeline runs.

10. Store result (on MISS path only)
    → LLM for entity extraction (or regex). Then ChromaDB write.
```

---

## 10. Sequence Diagram — Full Suggestion Flow

This shows what happens across every system component for a query that hits the cache and the user accepts it:

```
Sarah          Frontend         API Server      Orchestrator    ChromaDB     LLM Judge
  │                │                │               │              │             │
  │ Types query    │                │               │              │             │
  │──────────────>│                │               │              │             │
  │                │ POST /stream   │               │              │             │
  │                │───────────────>│               │              │             │
  │                │                │ run(query)    │              │             │
  │                │                │──────────────>│              │             │
  │                │                │               │ lookup_top_n │             │
  │                │                │               │─────────────>│             │
  │                │                │               │ [3 results]  │             │
  │                │                │               │<─────────────│             │
  │                │                │               │              │             │
  │                │                │               │ [gray-zone candidate]       │
  │                │                │               │─────────────────────────>  │
  │                │                │               │ (non-blocking background)  │
  │                │                │               │              │             │
  │                │ SSE: banner    │               │              │             │
  │                │<───────────────│<──────────────│              │             │
  │ Sees banner    │                │               │              │             │
  │<──────────────│                │               │              │             │
  │                │                │               │              │             │
  │                │                │               │ [judge returns YES]         │
  │                │                │               │<────────────────────────── │
  │ #2 shows ✓     │                │               │              │             │
  │<──────────────│                │               │              │             │
  │                │                │               │              │             │
  │ Clicks         │                │               │              │             │
  │ "Use this"     │                │               │              │             │
  │──────────────>│                │               │              │             │
  │                │ POST /intent-decision          │              │             │
  │                │───────────────>│               │              │             │
  │                │                │ resolve(accept)│             │             │
  │                │                │──────────────>│              │             │
  │                │                │               │ increment    │             │
  │                │                │               │ variant_count│             │
  │                │                │               │─────────────>│             │
  │                │                │               │              │             │
  │                │ SSE: result    │               │              │             │
  │                │<───────────────│<──────────────│              │             │
  │ Sees answer    │                │               │              │             │
  │<──────────────│                │               │              │             │
```

Total time: ~50–150ms (vs 5–70s for full pipeline)

---

## 11. Storage — What Gets Saved Per Answer

Every cached answer stored in ChromaDB contains these fields:

| Field | What it holds | Example |
|---|---|---|
| `role` | Who this answer is for | `relationship_manager` |
| `answer` | The actual answer text | `"CUST001's recommended pricing is..."` |
| `route` | Which AI path generated it | `"Data Layer"` |
| `session_id` | Which chat session created it | `"sess_abc123"` |
| `ts_iso` | When it was saved | `"2026-07-28T09:15:00Z"` |
| `entities` | Entity signature for the gate | `"customer_id:cust001"` |
| `variant_count` | Times a similar query reused this answer | `3` |
| `reasoning` | The AI's reasoning from original run | `[{...}]` |

The **document ID** is a fingerprint of `role + query` — so the same question asked twice by the same role always maps to the same storage slot (no duplicates).

---

## 12. Role Isolation — Why Answers Never Leak Between Users

Every lookup filters by role first. A `relationship_manager` and a `credit_officer` asking the identical question get completely separate cache spaces:

```
"What is CUST001's exposure?"  asked by relationship_manager
  → doc_id: uuid5("relationship_manager::What is CUST001's exposure?")
  → stored with role = "relationship_manager"

"What is CUST001's exposure?"  asked by credit_officer
  → doc_id: uuid5("credit_officer::What is CUST001's exposure?")
  → stored with role = "credit_officer"
  → completely different entry, even if text is identical
```

When searching, ChromaDB filters: `where role = "relationship_manager"` — so a credit officer's more detailed answer never shows up for a relationship manager, and vice versa.

---

## 13. What Happens at Server Startup

When the API server starts, it immediately:
1. Opens the ChromaDB collection (or creates it if first time)
2. Loads the embedding model into memory
3. Runs any pending batch ingest

This "warmup" ensures the first real user query doesn't experience a 1–3 second cold-start delay.

---

## 14. Populating the Cache From Historical Data (Ingest Pipeline)

The cache doesn't start knowing anything. It needs to be populated. There are two ways:

**Option A — Live mode:** As users ask questions and get answers, results are stored automatically (requires `CACHE_INLINE_STORE_ENABLED=true`).

**Option B — Batch ingest:** A background process reads conversation files from `data/conversations/cleaned_conversations/` and loads them all at once. This is the recommended approach because it gives control over quality.

**Data source:** Only `data/conversations/cleaned_conversations/` (controlled by `CACHE_INGEST_SOURCE_DIR`). The audit trail source (`data/audit_trail.jsonl`) has been removed — cleaned conversation files are the single source of truth.

```
cleaned_conversations/*.jsonl
       ↓
Read Q&A pairs (user + assistant turns only)
  Rolling summary records are automatically skipped (no 'role' key)
       ↓
Skip bad entries:
  - Blocked answers (blocked=true)
  - Answers that were themselves cache hits (cache_hit=true)
  - Answers older than max age
       ↓
Check if already stored (by fingerprint ID = SHA256 of role::query)
  Already there? → Skip (idempotent)
  New entry?     → Embed + store
       ↓
Paraphrase augmentation (if CACHE_PARAPHRASE_ENABLED=true)
  → Generate N paraphrases via LLM
  → Store each as a separate ChromaDB entry → same answer
  → Sleep CACHE_PARAPHRASE_DELAY_S seconds (rate limit protection)
  → On 429: retry up to 3× with exponential backoff (10s → 20s → 40s)
```

**CLI commands:**

```bash
# Preview what would be stored (no actual writes)
python -m src.cache.ingest_pipeline --dry-run --max-age-hours 99999

# Store everything from cleaned_conversations
python -m src.cache.ingest_pipeline --overwrite --max-age-hours 99999

# Store with paraphrase augmentation (generates 5 variants per entry)
# Run from agent-mesh/ directory; CACHE_PARAPHRASE_ENABLED=true is already in .env
python -m src.cache.ingest_pipeline --overwrite --max-age-hours 99999

# Target a specific role only
python -m src.cache.ingest_pipeline --role relationship_manager --max-age-hours 99999

# Backfill entity signatures for entries stored without them
python -m src.cache.ingest_pipeline --backfill-entities
```

**IngestReport fields:**

| Field | Meaning |
|---|---|
| `total_scanned` | Q/A pairs read from JSONL files |
| `newly_stored` | Original entries written to ChromaDB |
| `paraphrases_stored` | Paraphrase variants written (0 when disabled) |
| `already_present` | Skipped — fingerprint already in ChromaDB |
| `skipped_cache_hit` | Skipped — assistant turn had `cache_hit=true` |
| `skipped_stale` | Skipped — older than `CACHE_MAX_AGE_HOURS` |
| `skipped_empty` | Skipped — missing query/answer/role or blocked |

---

## 15. Quick Reference — LLM Usage Summary

| Step | LLM Used? | Why / Why Not |
|---|---|---|
| Embedding | No | Small local model — fast, free, no API |
| ChromaDB search | No | Pure vector math |
| Query Templating (Canonicalization) | No — pure regex | Replaces `CUST001` → `<CUSTOMER_ID>` etc. before embedding. Zero API calls. |
| Entity extraction | Yes (LLM) + regex fallback | Structured IDs caught by regex; names/dates/amounts need LLM |
| Zone decision (score vs thresholds) | No | Pure number comparison |
| Cross-encoder rerank | No LLM (local model) | Runs on-device, no network |
| LLM Judge (gray zone only) | Yes | Genuinely ambiguous — needs reading comprehension |
| Serving cached answer | No | Direct ChromaDB read |
| Full pipeline (cache miss) | Yes | Normal AI pipeline — unavoidable |
| Negative answer detection | No | Regex/keyword pattern match |
| Storing new entry | No (for write itself) | ChromaDB upsert |
| **Paraphrase augmentation (ingest only)** | **Yes — at ingest time only** | Generates N phrasings per entry via project LLM provider. Cost paid once; zero query-time cost. |

---

## 16. Configuration Quick Reference

```bash
# Turn the whole cache on/off
ENABLE_RESPONSE_CACHE=true

# How long a cached answer stays valid
CACHE_MAX_AGE_HOURS=150.0

# Similarity thresholds
CACHE_SIMILARITY_THRESHOLD=0.92    # above this = HIT (very confident)
CACHE_INTENT_MATCH_THRESHOLD=0.85  # above this = Intent Match
CACHE_MISS_THRESHOLD=0.75          # below this = MISS (always run fresh)

# Show the suggestion banner for all zones (not just HIT)
CACHE_INTENT_MATCH_ENABLED=true

# LLM that judges gray-zone candidates
CACHE_JUDGE_MODEL=gemma-4-31b

# Prevent wrong customer answers
CACHE_ENTITY_GATING_ENABLED=true
CACHE_ENTITY_GATE_MODE=hard        # hard=drop  soft=ask judge

# Don't cache "no data found" answers
CACHE_SKIP_NEGATIVE=true

# --- Ingest pipeline source -------------------------------------------------
# Directory the batch ingest pipeline reads from (separate from CONVERSATION_STORE_DIR
# which is used by the memory system).
CACHE_INGEST_SOURCE_DIR=data/conversations/cleaned_conversations

# --- Ingest-time paraphrase augmentation ------------------------------------
# Generate N alternative phrasings per entry at ingest time using the project LLM.
# Each paraphrase is stored as a separate ChromaDB entry pointing to the same answer.
# Disabled by default — enable for bulk ingest runs.
CACHE_PARAPHRASE_ENABLED=false    # set true to enable during ingest
CACHE_PARAPHRASE_N=5              # paraphrases generated per Q/A pair
CACHE_PARAPHRASE_DELAY_S=5.0     # sleep between LLM calls (rate limit protection)
                                  # at 5s → ~12 RPM, safe for Cerebras free-tier
```

### Tuning the thresholds

| If you see this problem | Try this fix |
|---|---|
| Users often reject cache suggestions (false positives) | Raise `CACHE_SIMILARITY_THRESHOLD` (e.g. 0.95) |
| Too many cache misses on similar questions | Lower `CACHE_SIMILARITY_THRESHOLD` (e.g. 0.88) |
| Gray zone taking too long (judge timeouts) | Enable cross-encoder reranker to filter before judge |
| Cache serving wrong customer data | Ensure `CACHE_ENTITY_GATING_ENABLED=true` and mode=`hard` |
| 429 errors during paraphrase ingest | Raise `CACHE_PARAPHRASE_DELAY_S` (e.g. 10.0); pipeline already retries 3× with backoff |
| Paraphrase ingest too slow | Lower `CACHE_PARAPHRASE_N` (e.g. 3) or `CACHE_PARAPHRASE_DELAY_S` if on paid plan |

---

## 17. Known Limitations

| Limitation | Plain English |
|---|---|
| Answers can go stale | Cached answer doesn't update when underlying data changes. Set `CACHE_MAX_AGE_HOURS` to control maximum age. |
| Single server only | ChromaDB uses a local SQLite file. Cannot run on multiple servers at once. |
| Decisions lost on restart | If the server restarts while a user is looking at a suggestion banner, the 60-second timeout kicks in and a fresh answer runs. |
| Paraphrase ingest is slow | Paraphrase augmentation adds ~5s per entry (rate limit protection). For 100 entries × 5 paraphrases = ~8 minutes total. Run overnight for large datasets. |
| Paraphrase quality depends on LLM | Low-quality paraphrases may miss intent or rephrase entities incorrectly. The entity gate still protects data isolation but a bad paraphrase may waste a ChromaDB slot. |

---

## 18. Key Files

| File | What it does |
|---|---|
| [src/cache/semantic_cache.py](../agent-mesh/src/cache/semantic_cache.py) | Core store: embedding, search, ChromaDB lifecycle |
| [src/cache/entity_extractor.py](../agent-mesh/src/cache/entity_extractor.py) | Extract entities from queries; canonicalize; regex fallback |
| [src/cache/intent_decision_store.py](../agent-mesh/src/cache/intent_decision_store.py) | Pause/resume while user decides (accept/reject) |
| [src/cache/ingest_pipeline.py](../agent-mesh/src/cache/ingest_pipeline.py) | Batch ingest from `cleaned_conversations/`; paraphrase augmentation; entity backfill |
| [src/mesh/workflow.py](../agent-mesh/src/mesh/workflow.py) | `CacheCheckExecutor` — the four decision branches |
| [src/mesh/orchestrator.py](../agent-mesh/src/mesh/orchestrator.py) | Intercepts user's accept/reject and routes accordingly |
| [src/config.py](../agent-mesh/src/config.py) | All `CACHE_*` configuration variables including ingest and paraphrase settings |
| [data/conversations/cleaned_conversations/](../agent-mesh/data/conversations/cleaned_conversations/) | Curated JSONL files used as the ingest source |
