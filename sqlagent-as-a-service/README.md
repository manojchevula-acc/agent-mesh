# SQL Agent — GERNAS v3.0 / FAB

Production-grade, multi-agent **ReAct SQL Agent**: the structured-data arm of the
GERNAS Data Access Layer (DAL). It converts a natural-language data need into a
**safe, validated, auditable, read-only** SQL query against the FAB pricing schema
and returns a typed result.

It is a **tool/service**, not a chatbot — bound to a parent agent's LLM as a set of
callable functions. It performs read-only access exclusively; it never writes and
never owns a business decision (price/approval). See the *SQL Agent Design Document*
for rationale and the *Technical Build Specification* for the code-level contract.

## The three query tiers

The tier is a property of the tool library, not of the question phrasing:

| Tier                           | Who writes the SQL                            | What the LLM does                   | Risk    |
| ------------------------------ | --------------------------------------------- | ----------------------------------- | ------- |
| **Parameterised**        | Human, at code time (one fixed shape)         | Picks tool + arg values             | Lowest  |
| **Semi-dynamic**         | Human clause-builder over a fixed filter menu | Picks which filter keys to populate | Low     |
| **Full dynamic** (gated) | The LLM, from the semantic layer              | Writes the SELECT                   | Highest |

All three converge on the same **six-check validator** before execution.

## Repository layout

```
sql_agent/
├── config.py                  # Section 13 — settings, env, constants
├── semantic_layer/            # Section 3 — the schema contract (single source of truth)
│   ├── schema.yaml
│   ├── loader.py              # parses schema.yaml -> typed objects + allow-lists
│   └── renderer.py            # renders schema.yaml -> prompt text
├── db/
│   ├── connection.py          # pooled, SELECT-only credential
│   ├── dialect.py             # Postgres/MySQL portability (CONCAT vs ||, timeout)
│   └── executor.py            # execute(sql, params) -> rows, timing (validates first)
├── tools/                     # Sections 4-6 — the full tool catalogue
│   ├── customer_tools.py      # 4.1
│   ├── product_tools.py       # 4.2
│   ├── treasury_tools.py      # 4.3
│   ├── policy_tools.py        # 4.4
│   ├── deal_tools.py          # 4.5
│   ├── calculation_tools.py   # 4.6 — parameterised compute_* tools
│   ├── search_tools.py        # 5   — semi-dynamic find_* tools
│   ├── analytical_tool.py     # 6   — the one gated dynamic tool
│   └── registry.py            # ALL_TOOLS map (bound per caller scope)
├── llm/                       # Configurable multi-provider LLM, per-step selection
│   └── factory.py             # get_llm(Step.GENERATION|CORRECTION|AGENT|JUDGE)
├── calculations/              # Section 7 — pure formulas, zero SQL, zero LLM
│   ├── pricing.py
│   ├── risk.py
│   └── eligibility.py
├── validation/                # Section 8 — six-check pipeline
│   ├── sql_validator.py
│   └── exceptions.py
├── routing/                   # Section 9 — tier decision + dispatch
│   ├── tier_router.py
│   └── query_engine.py
├── agent/                     # Section 10-11 — LangGraph ReAct definition
│   ├── graph.py
│   ├── state.py
│   └── prompts.py
├── formatting/                # Section 12 — typed JSON/markdown output + audit
│   ├── response_formatter.py
│   └── audit_logger.py
└── service/
    └── api.py                 # FastAPI wrapper (out-of-process DAL mode)
```

## Governed tables (read-only)

`customer_master` · `product_master` · `treasury_rate_sheet` · `pricing_policy` · `historical_deals`

## Quick start

Requires Python 3.13.

```bash
uv venv --python 3.13
uv sync                      # installs runtime deps + the dev group (pytest)
cp .env.example .env         # fill in DB_DSN (SELECT-only) + your LLM provider key
uv run pytest                # calculations + validator + router + tools run with no DB
```

## LLM configuration (provider- and step-configurable)

The LLM is **configurable per provider and per pipeline step** — no code changes to
swap models. Set a default, then optionally override individual steps in `.env`:

| Setting                          | Meaning                                                       |
| -------------------------------- | ------------------------------------------------------------- |
| `LLM_PROVIDER` / `LLM_MODEL` | Default provider + model for every step                       |
| `LLM_GENERATION_*`             | Model for tier-3 dynamic SQL generation                       |
| `LLM_CORRECTION_*`             | Model for the self-correction retry (after a validator error) |
| `LLM_AGENT_*`                  | Model for the ReAct agent node (tool selection)               |
| `LLM_JUDGE_*`                  | Model for an optional LLM-as-judge / evaluation hook          |

Supported providers: **`groq`** (default), `openai`, `azure`, `anthropic`. Code asks
for its model by step:

```python
from sql_agent.llm import Step, get_llm
llm = get_llm(Step.GENERATION)   # resolves provider+model from config
```

Add a new provider by adding one builder to `sql_agent/llm/factory.py`.

## Build order (Technical Spec §15)

1. Semantic layer (`schema.yaml`, `loader.py`, `renderer.py`)
2. DB (`connection.py`, `executor.py` — SELECT-only, statement timeout)
3. `calculations/*` + pinned unit tests (no dependencies)
4. `validation/sql_validator.py` + pass/fail suite
5. Parameterised tools (Section 4), then semi-dynamic tools (Section 5)
6. `analytical_query` (Section 6) wired to validator + generation/self-correction prompts
7. `routing/tier_router.py` + circuit breaker (Section 9.3)
8. `agent/graph.py` (Section 10) + multi-tier smoke test (Section 9.2)
9. `formatting/audit_logger.py` — every call (incl. hard rejects) produces a log line
10. `service/api.py` only once a second consuming agent needs the tool

## Database support (PostgreSQL · MySQL · SQLite)

The agent runs on **PostgreSQL, MySQL, or SQLite** from the same code — pick one with
the DSN. The dialect is inferred from the DSN scheme (override with `DB_DIALECT`):

```bash
# Postgres
DB_DSN=postgresql+psycopg2://sql_agent_readonly:****@host:5432/fab_pricing
# MySQL
DB_DSN=mysql+pymysql://sql_agent_readonly:****@host:3306/fab_pricing
# SQLite (local dev/POC)
DB_DSN=sqlite:///./fab_pricing.db
DB_DIALECT=          # optional: postgres | mysql | sqlite (blank = inferred)
```

Drivers (`psycopg2-binary`, `pymysql`) ship as dependencies; SQLite is built into
Python. Everything dialect-specific is isolated in
[`db/dialect.py`](sql_agent/sql_agent/db/dialect.py):

| Concern                       | PostgreSQL            | MySQL                  | SQLite           |
| ----------------------------- | --------------------- | ---------------------- | ---------------- |
| String concat (CSV filters)   | `a \|\| b`            | `CONCAT(a, b)`       | `a \|\| b`       |
| Per-statement timeout         | `statement_timeout` | `max_execution_time` | `busy_timeout` |
| Row cap                       | `LIMIT 50`          | `LIMIT 50`           | `LIMIT 50`     |
| Validator parse (`sqlglot`) | `postgres`          | `mysql`              | `sqlite`       |

**The tier-3 dynamic SQL generator is dialect-aware:** the configured engine and its
SQL rules are injected into the generation prompt, so the LLM writes correct SQL for
whichever backend is active (e.g. `CONCAT(...)` on MySQL vs `||` on Postgres/SQLite),
and the validator parses it with the matching `sqlglot` dialect.

Whichever credential you use should hold **SELECT-only** grants (on SQLite, open
read-only with `sqlite:///./fab_pricing.db?mode=ro&uri=true`).

## Loading the POC data

Load the synthetic Excel workbook into any of the three backends with the same script
(use a **write-capable** credential — separate from the agent's read-only one):

```bash
# SQLite (no server needed)
uv run python scripts/load_excel_to_db.py --dsn "sqlite:///./fab_data.db" --drop
# MySQL
uv run python scripts/load_excel_to_db.py --dsn "mysql+pymysql://admin:pw@localhost:3306/fab_data" --drop
# Postgres
uv run python scripts/load_excel_to_db.py --dsn "postgresql+psycopg2://admin:pw@localhost:5432/fab_data" --drop
```

Tables are created from the semantic layer, so columns/types/keys stay in lock-step
with the rest of the agent.

## Safety guarantees

- **Read-only, always** — SELECT-only DB grant, enforced independently of the validator.
- **Validate every query before execution** — all six checks run for every tier, including hand-written parameterised SQL.
- **Deterministic calculations** — banking figures come from unit-tested formula code, never the LLM.
- **Everything is audited** — every call carries an `audit_id`; hard rejects raise a security event.
