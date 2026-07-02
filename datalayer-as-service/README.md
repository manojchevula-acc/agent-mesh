# FAB Pricing Recommendation – Structured Data MCP POC

A lightweight, **fully dynamic** Python pipeline that validates, curates and builds a
MySQL **semantic layer** on top of FAB's structured banking datasets, then exposes that
layer through an **MCP server** and a **LangChain + Groq chat agent** (Streamlit UI).

The pipeline **auto-discovers every CSV** in `data/raw/` — drop a new file in and the
validate → curate → load steps pick it up automatically. The semantic views and MCP
tools power richer pricing-recommendation scenarios (new-customer pricing, competitor
comparison, price-trace explanation, policy exceptions, profitability & RWA impact, etc.).

---

## Enhanced POC Scenarios

The semantic layer + agent can answer, among others:

1. What interest rate should I offer to customer **CUST001**?
2. What rate for a **new SME** customer requesting **Trade Finance**? *(no relationship history)*
3. **Explain step-by-step** how the price was calculated for **DEAL040**.
4. A customer has a **competitor offer lower** than FAB's — what should we do? *(MATCH / COUNTER / ESCALATE / REJECT)*
5. Which deals are **non-compliant** and **why**?
6. Show **competitor pricing pressure** by product.
7. Show **operations-cost impact** on pricing.
8. Show **profitability and RWA impact** for a customer.
9. Compare **approved vs recommended vs competitor** price.
10. What is the **relationship-discount** eligibility / approval need?
11. **Win/loss insights** and pricing gaps by segment/product.
12. **Segment pricing benchmark** (target margin, floor, buffers, discount caps).

---

## Project Structure

```
datalayer-as-service/
│
├── data/
│   ├── raw/                        # Source CSVs (auto-discovered — do not modify)
│   │   ├── customer_master.csv
│   │   ├── historical_deals.csv
│   │   ├── pricing_policy.csv
│   │   ├── product_master.csv
│   │   ├── treasury_rate_sheet.csv
│   │   ├── competitor_pricing.csv
│   │   ├── credit_rating_events.csv
│   │   ├── cross_sell_recommendation_rules.csv
│   │   ├── customer_segment_pricing_rules.csv
│   │   ├── customer_similarity_mapping.csv
│   │   ├── data_dictionary.csv
│   │   ├── operations_cost.csv
│   │   ├── pricing_negotiation_memory.csv
│   │   └── prospect_customer_profile.csv
│   │
│   ├── curated/                    # Cleaned CSVs  (generated — one per raw file)
│   └── semantic/                   # Business-ready CSV views (generated)
│
├── sql/
│   ├── 01_create_schemas.sql       # Creates fab_curated & fab_semantic schemas
│   ├── 02_create_curated_tables.sql# Core DDL (new tables auto-created on load)
│   └── 03_create_semantic_views.sql# 13 semantic views (2 helpers + 11 business)
│
├── mcp_server/
│   ├── __init__.py                 # Package marker
│   ├── db.py                       # SQLAlchemy engine factory (URL-encodes password)
│   ├── tools.py                    # Pure query functions (semantic views only)
│   └── server.py                   # FastMCP server — registers 15 tools
│
├── scripts/
│   ├── test_mcp_db_connection.py   # DB smoke-test (env + connection + views + sample)
│   ├── test_semantic_views.py      # Row counts + sample queries for key views
│   └── test_agent_tools.py         # Validates all 15 MCP tools + semantic-only guarantee
│
├── .env / .env.example             # Credentials (copy example → .env)
├── 01_validate_raw_data.py         # Step 1 – Validate every raw CSV (dynamic)
├── 02_create_curated_data.py       # Step 2 – Clean & write curated CSVs (dynamic)
├── 03_create_semantic_layer.py     # Step 3 – Build semantic CSVs (file-based)
├── 03_load_curated_to_mysql.py     # Step 4 – Load curated CSVs → MySQL fab_curated
├── 04_create_semantic_views.py     # Step 5 – Create SQL views in fab_semantic
├── agent.py                        # LangChain + Groq agent (15 tools)
├── app.py                          # Streamlit chat UI
├── requirements.txt
└── README.md
```

---

## Prerequisites

- Python 3.9+
- pip
- MySQL 8.x running locally (default `127.0.0.1:3306`)
- A Groq API key (for the chat agent)

---

## Setup

```powershell
# 1. Navigate to the project folder
cd C:\MyWork\Projects\FAB\agent-mesh\datalayer-as-service

# 2. (Recommended) Create and activate a virtual environment
python -m venv .venv
.venv\Scripts\activate

# 3. Install dependencies
pip install -r requirements.txt

# 4. Configure credentials
copy .env.example .env
#    Edit .env — set GROQ_API_KEY and MYSQL_PASSWORD.
```

### Environment variables (`.env`)

```
GROQ_API_KEY=<your_groq_api_key>
MYSQL_USER=root
MYSQL_PASSWORD=<your_mysql_password>
MYSQL_HOST=127.0.0.1
MYSQL_PORT=3306
MYSQL_DATABASE=fab_semantic
MCP_TRANSPORT=stdio          # or "http" for streamable-http
MCP_HOST=127.0.0.1
MCP_PORT=8000
```

---

## Running the Pipeline

Run **in order** from the project root.

| Step | Command | What it does |
|---|---|---|
| 1 | `python 01_validate_raw_data.py` | Auto-discovers & validates **every** CSV in `data/raw/`; writes `logs/validation_summary.txt`. Non-fatal (only empty/unreadable files FAIL). |
| 2 | `python 02_create_curated_data.py` | Cleans each raw CSV (normalise columns, trim, dedupe, type-infer, fill) → `data/curated/<same-name>.csv`. |
| 3 | `python 03_create_semantic_layer.py` | *(Optional, file-based)* Builds all 13 semantic CSVs in `data/semantic/`. |
| 4 | `python 03_load_curated_to_mysql.py` | Creates schemas, loads **all** curated CSVs into `fab_curated` (new tables auto-created via `to_sql`). |
| 5 | `python 04_create_semantic_views.py` | Runs `sql/03_create_semantic_views.sql` → 13 views in `fab_semantic`; row-count smoke-test. |

> Set `MYSQL_PASSWORD` in `.env` before steps 4 & 5.

### Dynamic CSV handling

- **Column normalisation:** headers → `lower_snake_case`.
- **Numeric inference:** a column is cast to numeric only if ≥ 80 % of non-null values parse (protects codes like tenor `1M` / `60M`).
- **Date inference:** columns whose names hint at dates are parsed to ISO text.
- **Fill rules:** numeric NaN → median; string NaN → `"Unknown"`.
- Drop a new CSV into `data/raw/` and re-run steps 1–2 (+4) — no code change needed.

---

## MySQL Schema Layout

```
MySQL
├── fab_curated                    # Cleaned & typed tables (one per curated CSV)
│   ├── customer_master
│   ├── historical_deals
│   ├── pricing_policy
│   ├── product_master
│   ├── treasury_rate_sheet
│   ├── competitor_pricing
│   ├── credit_rating_events
│   ├── cross_sell_recommendation_rules
│   ├── customer_segment_pricing_rules
│   ├── customer_similarity_mapping
│   ├── data_dictionary
│   ├── operations_cost
│   ├── pricing_negotiation_memory
│   └── prospect_customer_profile
│
└── fab_semantic                   # Business views (MCP / agent read these ONLY)
    ├── segment_pricing_benchmark        (helper)
    ├── operations_cost_impact           (helper)
    ├── customer_360
    ├── pricing_recommendation_view
    ├── margin_analysis
    ├── profitability_summary
    ├── rwa_impact_view
    ├── new_customer_pricing_view
    ├── competitor_price_analysis
    ├── pricing_trace_view
    ├── relationship_discount_view
    ├── win_loss_insights
    └── policy_exception_view
```

### Recommended-price formula (used by the semantic views)

```
recommended_price_pct = funding_cost_pct
                      + target_margin_pct
                      + risk_premium_pct
                      + ops_cost_margin_pct
                      - relationship_discount_pct
```

### Competitor action logic (`competitor_price_analysis`)

| Condition | Action |
|---|---|
| competitor rate < profitability floor | **REJECT** (below floor — walk away) |
| gap ≤ 20 bps | **MATCH** |
| gap ≤ 60 bps | **COUNTER** |
| otherwise | **ESCALATE** |

---

## Semantic Views (Data Products)

| View | Purpose |
|---|---|
| `customer_360` | Customer master + aggregated deal KPIs (volume, win rate, avg margin). |
| `pricing_recommendation_view` | Deal pricing with rebuilt recommended price, policy benchmarks, compliance flags. |
| `margin_analysis` | Deal-level margin decomposition, spread over benchmark, margin-below-min flag. |
| `profitability_summary` | Revenue, funding/operating/capital cost, net profit and profitability tier. |
| `rwa_impact_view` | RWA-weighted exposure, capital required, return on RWA per won deal. |
| `new_customer_pricing_view` | Recommended price for prospects with **no** relationship history. |
| `competitor_price_analysis` | FAB vs competitor comparison + MATCH / COUNTER / ESCALATE / REJECT. |
| `pricing_trace_view` | Step-by-step recommended-price breakdown with explanation text. |
| `segment_pricing_benchmark` *(helper)* | Segment target margin, floor, buffers, discount caps. |
| `operations_cost_impact` *(helper)* | Operational cost margin per product × segment. |
| `relationship_discount_view` | Relationship-discount eligibility & approval requirement. |
| `win_loss_insights` | Won/lost aggregation with pricing gap & competitor pressure. |
| `policy_exception_view` | Per-deal policy breaches with exception reasons (`is_exception`). |

---

## MCP Server

The MCP server exposes **15 tools** over the `fab_semantic` views for any MCP-compatible
client (VS Code Copilot, Claude Desktop, etc.).

### MCP Tools

| Tool | View queried | Key inputs |
|---|---|---|
| `customer_360` | `customer_360` | `customer_id` |
| `pricing_recommendation` | `pricing_recommendation_view` | `customer_id` |
| `profitability_summary` | `profitability_summary` | `customer_id` |
| `margin_analysis` | `margin_analysis` | `customer_id` |
| `rwa_impact` | `rwa_impact_view` | `customer_id` |
| `new_customer_pricing` | `new_customer_pricing_view` | `customer_id` / `segment` / `product_id` / `risk_rating` |
| `competitor_price_analysis` | `competitor_price_analysis` | `customer_id` / `deal_id` |
| `pricing_trace` | `pricing_trace_view` | `customer_id` / `deal_id` |
| `segment_pricing_benchmark` | `segment_pricing_benchmark` | `segment` / `product_id` |
| `operations_cost_impact` | `operations_cost_impact` | `product_id` / `customer_segment` |
| `relationship_discount` | `relationship_discount_view` | `customer_id` |
| `win_loss_insights` | `win_loss_insights` | `customer_id` / `product_id` / `segment` |
| `policy_exception` | `policy_exception_view` | `customer_id` / `deal_id` |
| `non_compliant_deals` | `policy_exception_view` *(is_exception=1)* | `customer_id` |
| `compare_fab_vs_competitor` | `competitor_price_analysis` | `customer_id` / `deal_id` |

All tools return JSON-serialisable `list[dict]`, cap at 100 rows, use parameterised SQL,
and query **only** `fab_semantic`.

### Running the MCP server

```powershell
python scripts/test_mcp_db_connection.py   # verify DB first
python -m mcp_server.server                # stdio transport (default)
```

For HTTP transport, set `MCP_TRANSPORT=http` in `.env`.

### VS Code / Claude Desktop config

```json
{
  "mcpServers": {
    "fab-pricing-mcp": {
      "command": "C:/MyWork/Projects/FAB/agent-mesh/datalayer-as-service/.venv/Scripts/python.exe",
      "args": ["-m", "mcp_server.server"],
      "cwd": "C:/MyWork/Projects/FAB/agent-mesh/datalayer-as-service"
    }
  }
}
```

---

## AI Agent + Streamlit Chat UI

`agent.py` is a LangChain + Groq agent wrapping the same 15 semantic-layer query
functions as tools. `app.py` is a Streamlit chat UI featuring:

- **Suggested-question buttons** in the sidebar.
- A **Data scope** panel listing the available `fab_semantic` views.
- A **Run health check** button (tests MySQL connection + counts each view).
- Clear errors when `GROQ_API_KEY` or MySQL credentials are missing.

### Run commands

```powershell
pip install -r requirements.txt
python -m mcp_server.server     # optional, for MCP clients
streamlit run app.py            # chat GUI
python agent.py                 # optional CLI mode
```

### Example questions

- What interest rate should I offer to customer CUST001?
- What rate for a new SME customer for Trade Finance?
- Explain step by step how the price was calculated for DEAL040.
- Customer CUST001 has a lower competitor offer — what should we do?
- Which deals are non-compliant and why?
- Show operations-cost impact and profitability & RWA impact for CUST005.

---

## Tests / Validation

```powershell
python scripts/test_mcp_db_connection.py   # env + connection + view list + sample
python scripts/test_semantic_views.py      # row counts + sample rows for key views
python scripts/test_agent_tools.py         # all 15 tools + semantic-only guarantee
```

---

## Troubleshooting

| Symptom | Fix |
|---|---|
| `Missing required environment variable` in the app | Copy `.env.example` → `.env` and set `GROQ_API_KEY` + MySQL creds. |
| `Access denied` / auth errors on MySQL | Verify `MYSQL_USER` / `MYSQL_PASSWORD`; passwords with `@` are auto URL-encoded. |
| `Unknown database 'fab_semantic'` | Run steps 4 & 5 (`03_load_curated_to_mysql.py`, `04_create_semantic_views.py`). |
| A view returns `ERROR` in the health check | Re-run `python 04_create_semantic_views.py`; ensure all curated tables loaded. |
| Groq model / auth error | Confirm `GROQ_API_KEY` is valid; default model is `llama-3.3-70b-versatile`. |
| New CSV not picked up | Re-run steps 1–2 (+4). The pipeline auto-discovers files in `data/raw/`. |
| tenor / codes turned to NaN | Handled — numeric cast requires ≥80 % parse rate and excludes `tenor`. |

---

## Assumptions (POC)

- `segment_pricing_benchmark` aggregates AVG over `customer_segment + product_type + risk_category`.
- `operations_cost_impact` aggregates AVG by `product_id + customer_segment`.
- `competitor_price_analysis` is derived from `pricing_negotiation_memory` (using `memory_interaction_id` as the deal key).
- `capital_cost_aed` is approximated as `RWA × 8% × 10%`.
- Competitor-action thresholds: MATCH ≤ 20 bps, COUNTER ≤ 60 bps, else ESCALATE, REJECT below floor.
- The MCP server and agent connect **only** to `fab_semantic` — never to `fab_curated` or raw tables.
- This is a **POC**; no orchestration framework is required. Run scripts from the project root.
