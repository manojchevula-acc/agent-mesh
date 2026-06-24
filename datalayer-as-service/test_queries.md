# DataLayer-as-a-Service — Test Queries

## Prerequisites

MySQL must be running with the `fab_semantic` schema and its 5 views loaded.

---

## Step-by-Step: Run the Service Individually

### 1. Set up environment

```bash
cd datalayer-as-service
cp .env .env          # already present; verify values below
```

Required `.env` keys:
```
MYSQL_HOST=127.0.0.1
MYSQL_PORT=9100
MYSQL_USER=root
MYSQL_PASSWORD=manoj
MYSQL_DATABASE=fab_semantic
GROQ_API_KEY=<your-groq-key>     # only needed for agent.py / app.py
```

### 2. Install dependencies

```bash
pip install -r requirements.txt
# or with uv:
uv venv && uv sync
```

### 3. Verify database connection

```bash
python scripts/test_mcp_db_connection.py
```

Expected output:
```
[OK] Connected to fab_semantic
[OK] Views found: customer_360, pricing_recommendation_view, profitability_summary, margin_analysis, rwa_impact_view
[OK] Sample query returned N rows
```

### 4a. Start as MCP server — HTTP transport (for agent-mesh integration)

```bash
MCP_TRANSPORT=http MCP_HOST=127.0.0.1 MCP_PORT=9100 python -m mcp_server.server
```

Listens on `http://127.0.0.1:9100/mcp`. This is what `agent-mesh` connects to.

### 4b. Start as MCP server — stdio (for local/Claude Desktop use)

```bash
python -m mcp_server.server
```

Communicates over stdin/stdout. No port opened.

### 4c. Start the Streamlit chat UI (optional, standalone test)

```bash
streamlit run app.py
```

Opens browser at `http://localhost:8501`. Type queries in plain English.

### 4d. Run the CLI agent (terminal chat, no browser)

```bash
python agent.py
```

Interactive prompt. Type queries, press Enter.

---

## MCP Tools Reference

All tools accept `customer_id` (string). Pass `""` to return all records (max 100).

| Tool | SQL View | Purpose |
|------|----------|---------|
| `customer_360` | `fab_semantic.customer_360` | 360° profile + deal KPIs |
| `pricing_recommendation` | `fab_semantic.pricing_recommendation_view` | Deal pricing vs policy benchmarks |
| `profitability_summary` | `fab_semantic.profitability_summary` | Profitability by product type + tier |
| `margin_analysis` | `fab_semantic.margin_analysis` | Margin decomposition vs treasury benchmark |
| `rwa_impact` | `fab_semantic.rwa_impact_view` | RWA-weighted exposure + Basel III capital |

---

## Test Queries

### T1 — Customer 360 for a single customer

**Query**
```
What is the full profile for CUST001?
```

**Tool called:** `customer_360("CUST001")`

**Expected output (shape)**
```json
[
  {
    "customer_id": "CUST001",
    "customer_name": "...",
    "segment": "...",
    "rating": "...",
    "total_exposure": ...,
    "deal_count": ...,
    ...
  }
]
```

**Flow**
```
agent.py / MCP client
  └─ customer_360(customer_id="CUST001")
       └─ SELECT * FROM fab_semantic.customer_360 WHERE customer_id = 'CUST001' LIMIT 100
            └─ returns JSON rows
```

---

### T2 — Pricing recommendation for a customer

**Query**
```
Show pricing recommendation for CUST002
```

**Tool called:** `pricing_recommendation("CUST002")`

**Expected output (shape)**
```json
[
  {
    "customer_id": "CUST002",
    "deal_id": "...",
    "product_type": "...",
    "current_rate": ...,
    "recommended_rate": ...,
    "policy_floor": ...,
    "compliance_flag": "COMPLIANT" | "NON_COMPLIANT",
    ...
  }
]
```

**Flow**
```
agent.py / MCP client
  └─ pricing_recommendation(customer_id="CUST002")
       └─ SELECT * FROM fab_semantic.pricing_recommendation_view WHERE customer_id = 'CUST002' LIMIT 100
```

---

### T3 — Margin analysis

**Query**
```
Margin analysis for CUST003
```

**Tool called:** `margin_analysis("CUST003")`

**Expected output (shape)**
```json
[
  {
    "customer_id": "CUST003",
    "deal_id": "...",
    "product_type": "...",
    "net_margin": ...,
    "treasury_benchmark": ...,
    "margin_over_benchmark": ...,
    ...
  }
]
```

**Flow**
```
agent.py / MCP client
  └─ margin_analysis(customer_id="CUST003")
       └─ SELECT * FROM fab_semantic.margin_analysis WHERE customer_id = 'CUST003' LIMIT 100
```

---

### T4 — Profitability summary across all customers

**Query**
```
Show profitability summary for all customers
```

**Tool called:** `profitability_summary("")`

**Expected output (shape)**
```json
[
  { "customer_id": "CUST001", "product_type": "...", "tier": "...", "profit": ..., ... },
  { "customer_id": "CUST002", ... },
  ...
]
```

**Note:** Empty `customer_id` removes the WHERE clause and returns up to 100 rows across all customers.

---

### T5 — RWA impact for a customer

**Query**
```
RWA impact for CUST001
```

**Tool called:** `rwa_impact("CUST001")`

**Expected output (shape)**
```json
[
  {
    "customer_id": "CUST001",
    "deal_id": "...",
    "rwa_exposure": ...,
    "basel3_capital": ...,
    "return_on_rwa": ...,
    ...
  }
]
```

---

### T6 — No records found (invalid customer)

**Query**
```
Customer 360 for CUST999
```

**Tool called:** `customer_360("CUST999")`

**Expected output**
```json
[{"message": "No records found for the given customer_id."}]
```

---

### T7 — Database connection failure

If MySQL is down:

**Expected output**
```json
[{"error": "(...) Can't connect to MySQL server on '127.0.0.1'"}]
```

The MCP server returns this JSON rather than crashing, so the calling agent can report the error gracefully.

---

## Quick smoke test (curl against HTTP MCP)

Once the HTTP MCP server is running on port 9100, you can invoke tools directly:

```bash
# List available tools
curl -s http://127.0.0.1:9100/mcp \
  -H "Content-Type: application/json" \
  -d '{"jsonrpc":"2.0","method":"tools/list","params":{},"id":1}'

# Call customer_360
curl -s http://127.0.0.1:9100/mcp \
  -H "Content-Type: application/json" \
  -d '{"jsonrpc":"2.0","method":"tools/call","params":{"name":"customer_360","arguments":{"customer_id":"CUST001"}},"id":2}'
```
