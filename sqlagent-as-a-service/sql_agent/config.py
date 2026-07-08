"""Section 13 — settings, env, constants.

Central configuration object, loaded once from the environment (.env in dev).

LLM configuration is two-level:
  * A DEFAULT provider + model (e.g. groq / llama-3.3-70b-versatile).
  * Optional PER-STEP overrides, so a different model can be used for each step of
    the pipeline — SQL generation, error correction (self-correction), the ReAct
    agent node, and an LLM-as-judge. Any step that has no override falls back to the
    default. See sql_agent/llm/factory.py for resolution.
"""

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    # --- Database (SELECT-only credential, set via env) -----------------------
    db_dsn: str = ""
    # Active SQL dialect: postgres | mysql. Blank = infer from the DSN scheme
    # (e.g. mysql+pymysql://... -> mysql, postgresql+psycopg2://... -> postgres).
    db_dialect: str = ""

    # --- Default LLM ----------------------------------------------------------
    # provider: groq | openai | azure | anthropic
    llm_provider: str = "groq"
    llm_model: str = "llama-3.3-70b-versatile"
    llm_temperature: float = 0.0

    # --- Per-step LLM overrides (blank = fall back to the default above) -------
    # step: agent (ReAct tool selection) | generation (tier-3 SQL) |
    #       correction (self-correction retry) | judge (LLM-as-judge)
    llm_agent_provider: str = ""
    llm_agent_model: str = ""
    llm_generation_provider: str = ""
    llm_generation_model: str = ""
    llm_correction_provider: str = ""
    llm_correction_model: str = ""
    llm_judge_provider: str = ""
    llm_judge_model: str = ""

    # --- Provider credentials -------------------------------------------------
    groq_api_key: str = ""
    openai_api_key: str = ""
    anthropic_api_key: str = ""
    azure_openai_endpoint: str = ""
    azure_openai_api_key: str = ""
    azure_openai_deployment: str = "gpt-4o"
    azure_openai_api_version: str = "2024-06-01"

    # --- Service / CORS -------------------------------------------------------
    # Comma-separated allowed origins for the FastAPI service. "*" = any (dev).
    cors_allow_origins: str = "*"

    # --- Observability --------------------------------------------------------
    # Root level for the "sql_agent" logger tree. INFO includes the request->tool->
    # tier->output flow AND the executed SQL for every tier; DEBUG adds the rest of
    # the per-step trace.
    log_level: str = "INFO"
    # File the logger tree also writes to, in addition to stdout. Blank disables the
    # file handler. Rotates at log_file_max_bytes, keeping log_file_backup_count files.
    log_file: str = "logs/sql_agent.log"
    log_file_max_bytes: int = 10_000_000
    log_file_backup_count: int = 5

    # --- Safety / execution constants ----------------------------------------
    row_cap: int = 50
    statement_timeout_seconds: int = 10
    max_tool_calls_per_turn: int = 10
    max_dynamic_calls_per_turn: int = 2
    max_self_correction_attempts: int = 3
    cache_ttl_seconds: int = 60

    # --- Agent metadata DB (memory/feedback/examples) -------------------------
    # Separate from db_dsn (the governed, read-only business DB). Blank in dev =>
    # in-memory checkpointer + every metadata writer becomes a no-op.
    agent_db_dsn: str = ""

    # --- Conversation memory --------------------------------------------------
    # memory  = in-process RAM, lost on restart (default, no infra needed)
    # sqlite  = single .db file, durable, no server (recommended starting point)
    # postgres = connection pool, durable, multi-worker safe (production)
    checkpointer_backend: str = "memory"  # memory | sqlite | postgres
    short_term_max_messages: int = 20     # how many recent messages to send to the model
    agent_db_pool_max: int = 10           # max Postgres connections (prod checkpointer pool)

    # --- Feedback / examples --------------------------------------------------
    feedback_enabled: bool = True
    examples_enabled: bool = True         # inject approved few-shot examples into generation
    max_fewshot_examples: int = 5

    # --- Production upgrade: intent detection (Component A) --------------------
    # Every new stage below is flag-gated and defaults to today's behaviour.
    intent_detection_enabled: bool = False   # run the intent node (shadow / advisory)
    intent_detection_enforced: bool = False  # act on out_of_scope short-circuit
    llm_intent_provider: str = ""            # small/fast model; blank => default
    llm_intent_model: str = ""
    llm_plan_provider: str = ""              # schema-link planner; blank => default
    llm_plan_model: str = ""

    # --- Production upgrade: schema retrieval (Component B) --------------------
    schema_retrieval_enabled: bool = False   # prune schema for the dynamic tier
    embedding_backend: str = "local"         # local | azure | none
    embedding_model: str = "BAAI/bge-base-en-v1.5"  # local; strong short-doc retrieval
    embedding_query_prefix: str = (          # bge/e5 models want a query-side instruction
        "Represent this sentence for retrieving relevant tables: ")
    embedding_top_k: int = 8                  # candidate tables after RRF fusion
    rrf_k: int = 60                           # Reciprocal Rank Fusion constant
    schema_link_enabled: bool = True          # LLM precision step over candidates

    # Vector index — WHERE the schema embeddings live / are searched (separate from the
    # embedding model, which is HOW documents are vectorised).
    vector_backend: str = "memory"            # memory | faiss | qdrant
    vector_index_path: str = ""               # faiss: file to persist/load; blank = in-RAM
    # Qdrant connection precedence: url (server) -> path (local on-disk, no server/Docker)
    # -> in-memory. qdrant_path runs Qdrant's engine IN-PROCESS, durable, no Docker.
    qdrant_url: str = ""                       # server mode, e.g. http://localhost:6333
    qdrant_path: str = ""                      # local on-disk mode, e.g. ./qdrant_data
    qdrant_api_key: str = ""
    qdrant_collection: str = "sql_agent_schema"

    # --- Production upgrade: validation (Component C) --------------------------
    join_graph_check_enabled: bool = True     # validator check #7 (only fires w/ join pairs)
    column_binding_check_enabled: bool = True  # check #8: qualified col must belong to its table
    execution_guard_enabled: bool = False     # validator check #8 (EXPLAIN); on at scale
    execution_cost_max: float = 1_000_000     # reject EXPLAIN plans above this est. cost
    answer_validation_enabled: bool = False   # run the answer judge (shadow)
    answer_validation_enforced: bool = False  # act on a mismatch verdict
    answer_validation_min_confidence: float = 0.7

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")


settings = Settings()
