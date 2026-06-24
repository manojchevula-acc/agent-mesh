# RAG-as-a-Service — Test Queries

## Prerequisites

Qdrant (vector DB) must be running, and at least one document must be ingested.
Redis is optional (used for caching); the service degrades gracefully without it.

---

## Step-by-Step: Run the Service Individually

### 1. Set up environment

```bash
cd rag-as-a-service
cp .env .env          # already present; verify the keys below
```

Key `.env` values to check:
```
API_KEY=<your-api-key>                      # sent as X-API-Key header in all requests
RAG__LLM__PROVIDER=groq
RAG__LLM__GROQ_API_KEY=<your-groq-key>
RAG__EMBEDDING__PROVIDER=bgem3             # downloads BAAI/bge-m3 on first run
RAG__VECTORDB__PROVIDER=qdrant
RAG__VECTORDB__QDRANT_PATH=./qdrant_storage  # embedded mode — no Qdrant server needed
RAG_GENERATE_ANSWER=false                   # set true to get an LLM-synthesised answer
MCP_TRANSPORT=http
MCP_HOST=127.0.0.1
MCP_PORT=9000
```

### 2. Install dependencies

```bash
pip install -r requirements.txt
# or with uv:
uv venv && uv sync
```

### 3. Ingest policy documents

Place PDF/DOCX files in `./docs/`, then:

```bash
python scripts/ingest_docs.py --path ./docs
```

With optional metadata:
```bash
python scripts/ingest_docs.py \
  --path ./docs \
  --document-type pricing_policy \
  --product-applicability "corporate_loan,revolving_credit" \
  --effective-date "2024-06-01"
```

Wait for the job to complete. Each document gets chunked, embedded, and stored in Qdrant.

### 4. Start the REST API server

```bash
uvicorn gernas_rag.main:app --reload --app-dir src
```

Listens on `http://0.0.0.0:8000`. OpenAPI docs at `/docs` (requires `DEBUG=true`).

### 5. Start the MCP server — HTTP transport (for agent-mesh integration)

```bash
MCP_TRANSPORT=http MCP_HOST=127.0.0.1 MCP_PORT=9000 python -m mcp_integration.server
```

Listens on `http://127.0.0.1:9000/mcp`. This is what `agent-mesh` connects to.

### 5b. Start MCP server — stdio (for Claude Desktop / local use)

```bash
python -m mcp_integration.server
```

---

## Auth

All REST endpoints require authentication. Send the `X-API-Key` header:

```bash
-H "X-API-Key: <your API_KEY from .env>"
```

---

## MCP Tool Reference

| Tool | Parameters | Purpose |
|------|-----------|---------|
| `search_documents` | `query` (str), `top_k` (int, 1–20, default 5), `generate_answer` (bool) | Retrieve grounded, cited passages from FAB policy documents |

**Response fields:**
```json
{
  "results": [
    {
      "source": "document_name",
      "clause": "4.2.1",
      "section": "Pricing Floors",
      "effective_date": "2024-06-01",
      "stale": false,
      "score": 0.91,
      "text": "..."
    }
  ],
  "total_results": 5,
  "freshness_warning": false,
  "latency_ms": 123.45,
  "answer": "..."   // only present if generate_answer=true
}
```

**Retrieval pipeline inside the service:**
```
search_documents(query)
  └─ POST /api/v1/retrieve (REST internally)
       ├─ Dense ANN search (BAAI/bge-m3, top_k=40 candidates)
       ├─ Sparse BM25 search (top_k=40 candidates)
       ├─ RRF fusion (k=60) → top 20 candidates
       ├─ Cross-encoder reranking → top 5
       ├─ Freshness penalty (age > 180 days → up to -30% score)
       ├─ Parent chunk expansion (returns parent text for full context)
       └─ Dedup by (source + first 200 chars)
```

---

## Test Queries

### T1 — Pricing floor for a rated loan

**REST call**
```bash
curl -s -X POST http://localhost:8000/api/v1/retrieve \
  -H "Content-Type: application/json" \
  -H "X-API-Key: <API_KEY>" \
  -d '{
    "query": "What is the pricing floor for a BB-rated AED loan?",
    "top_k": 3,
    "generate_answer": false
  }'
```

**MCP call (via agent-mesh or direct)**
```
search_documents(query="What is the pricing floor for a BB-rated AED loan?", top_k=3)
```

**Expected output (shape)**
```json
{
  "chunks": [
    {
      "text": "For BB-rated obligors, the minimum spread for AED-denominated corporate loans is ...",
      "source": "FAB_Pricing_Policy_2024.pdf",
      "section_heading": "Pricing Floors by Rating",
      "clause_reference": "Section 4.2.1",
      "score": 0.93,
      "effective_date": "2024-06-01",
      "freshness_warning": false
    }
  ],
  "total_results": 3,
  "latency_ms": 210.4,
  "freshness_warning_global": false
}
```

**Flow**
```
POST /api/v1/retrieve
  └─ embed query with BAAI/bge-m3
  └─ dense search (Qdrant) → 40 candidates
  └─ sparse search (BM25) → 40 candidates
  └─ RRF merge → 20
  └─ reranker → top 3
  └─ return chunks with source + clause
```

---

### T2 — Credit policy on fee waivers

**REST call**
```bash
curl -s -X POST http://localhost:8000/api/v1/retrieve \
  -H "Content-Type: application/json" \
  -H "X-API-Key: <API_KEY>" \
  -d '{
    "query": "What does the credit policy say about fee waivers?",
    "top_k": 5
  }'
```

**Expected output:** Chunks from the credit policy document citing the fee waiver conditions, with `clause_reference` and `source` populated.

---

### T3 — With generated answer

**REST call**
```bash
curl -s -X POST http://localhost:8000/api/v1/retrieve \
  -H "Content-Type: application/json" \
  -H "X-API-Key: <API_KEY>" \
  -d '{
    "query": "What are the AML/KYC requirements for onboarding a new corporate client?",
    "top_k": 5,
    "generate_answer": true
  }'
```

**Expected output (shape)**
```json
{
  "chunks": [...],
  "answer": "According to FAB_KYC_Policy_2024.pdf Section 3.1, new corporate clients must provide ... [citation]",
  "latency_ms": 890.2,
  "cache_hit": false
}
```

**Flow (additional step)**
```
... (same retrieval pipeline as T1)
  └─ top chunks passed to LLM (Groq llama-3.1-70b-versatile)
  └─ LLM synthesises cited answer
  └─ answer returned alongside chunks
```

---

### T4 — KYC requirements

**MCP call**
```
search_documents(query="What are the KYC requirements for onboarding?", top_k=3, generate_answer=true)
```

**Expected:** Grounded answer citing specific document, section, and clause.

---

### T5 — Concentration limits

**REST call**
```bash
curl -s -X POST http://localhost:8000/api/v1/retrieve \
  -H "Content-Type: application/json" \
  -H "X-API-Key: <API_KEY>" \
  -d '{"query": "What is the sector concentration limit?", "top_k": 3}'
```

---

### T6 — Stale document warning

If a retrieved chunk comes from a document older than 180 days, the response includes:

```json
{
  "chunks": [
    {
      "freshness_warning": true,
      "effective_date": "2022-01-01",
      ...
    }
  ],
  "freshness_warning_global": true
}
```

The RAGAgent in agent-mesh will surface this to the user as a `⚠ stale policy` warning.

---

### T7 — Health and readiness checks

```bash
# Liveness
curl http://localhost:8000/health
# → {"status": "ok"}

# Readiness (checks Qdrant connection)
curl http://localhost:8000/ready
# → {"status": "ready", "vectordb": true}
# → {"status": "degraded", "vectordb": false}  if Qdrant is down
```

---

### T8 — Run the built-in RAGAS evaluation suite

```bash
curl -s -X POST http://localhost:8000/api/v1/evaluate \
  -H "X-API-Key: <API_KEY>" \
  -d ''
```

Runs 7 FAB test cases through the retrieval pipeline and scores them with RAGAS:

| Metric | What it measures |
|--------|-----------------|
| `faithfulness` | Answer only contains claims supported by retrieved context |
| `answer_relevancy` | Answer is on-topic for the question |
| `context_precision` | Retrieved chunks are relevant (precision) |
| `context_recall` | Retrieved chunks cover the answer (recall) |

---

## Quick MCP smoke test (curl against HTTP MCP)

```bash
# List available tools
curl -s http://127.0.0.1:9000/mcp \
  -H "Content-Type: application/json" \
  -d '{"jsonrpc":"2.0","method":"tools/list","params":{},"id":1}'

# Call search_documents
curl -s http://127.0.0.1:9000/mcp \
  -H "Content-Type: application/json" \
  -d '{
    "jsonrpc":"2.0",
    "method":"tools/call",
    "params":{
      "name":"search_documents",
      "arguments":{
        "query":"What is the pricing floor for a BB-rated AED loan?",
        "top_k":3
      }
    },
    "id":2
  }'
```
