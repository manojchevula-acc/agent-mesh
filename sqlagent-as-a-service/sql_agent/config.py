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
    # Groq reasoning models (e.g. openai/gpt-oss-120b) spend part of their output budget
    # on hidden reasoning before the final answer; left unset, a model can under-invest in
    # the final answer even with the right tool result in context. "low" biases it toward
    # actually answering. Applied only to models whose name contains "gpt-oss" (see
    # llm/factory.py _build_groq) — harmless/ignored for non-reasoning models like llama.
    groq_reasoning_effort: str = "low"

    # --- Per-step LLM overrides (blank = fall back to the default above) -------
    # step: agent (ReAct tool selection) | generation (tier-3 SQL) |
    #       correction (self-correction retry) | judge (LLM-as-judge) |
    #       synthesis (final-answer writer, see below)
    llm_agent_provider: str = ""
    llm_agent_model: str = ""
    # Tool selection is a precision/decision task, not prose — keep it deterministic
    # regardless of what llm_temperature (the global default) is set to for other uses.
    llm_agent_temperature: float = 0.0
    llm_generation_provider: str = ""
    llm_generation_model: str = ""
    llm_correction_provider: str = ""
    llm_correction_model: str = ""
    llm_judge_provider: str = ""
    llm_judge_model: str = ""

    # --- Production upgrade: dedicated response-synthesis step ------------------
    # Flag-gated, defaults to today's behaviour (the tool-selection model's own text
    # IS the final answer). When enabled, once the ReAct loop decides no more tools
    # are needed, a SEPARATE call writes the user-facing answer from ONLY the
    # question + this turn's retrieved rows — no tool schemas, no rung/tool-choice
    # rules competing for the model's attention. Added after gpt-oss-120b was
    # observed to under-synthesize (e.g. reporting one field when a "full profile"
    # was asked for and all fields were already in the tool result).
    response_synthesis_enabled: bool = False
    # Deliberately a smaller/cheaper model than Step.AGENT: this step's only job is
    # turning already-retrieved rows into prose, not tool selection, so it doesn't
    # need a large or reasoning model — a smaller plain-instruct model also sidesteps
    # the gpt-oss-style under-answering quirk entirely.
    llm_synthesis_provider: str = ""
    llm_synthesis_model: str = "llama-3.1-8b-instant"
    # Higher than llm_temperature (0.0, used for deterministic tool selection) — this
    # step is prose generation, not a decision, so some variation is fine and reads
    # more natural than a flat, deterministic recitation of fields.
    llm_synthesis_temperature: float = 0.7

    # --- Provider credentials -------------------------------------------------
    groq_api_key: str = ""
    # Optional PER-STEP Groq API keys. Spread pipeline steps across several Groq keys to
    # raise the effective per-minute token/request limit (each key has its own quota).
    # Blank => that step falls back to groq_api_key. Resolved in llm/factory.py; only
    # used when the step's resolved provider is groq.
    groq_api_key_agent: str = ""
    groq_api_key_generation: str = ""
    groq_api_key_correction: str = ""
    groq_api_key_judge: str = ""
    groq_api_key_intent: str = ""
    groq_api_key_plan: str = ""
    groq_api_key_synthesis: str = ""
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
    max_fewshot_examples: int = 5         # static ReAct grounding (tool selection)
    # Intent-aware Pattern Retriever: how many RELEVANT examples to inject into the
    # dynamic-tier SQL generator prompt. Kept small (few-shot, not many-shot) so the
    # examples steer the model without blowing the provider's per-minute token budget.
    examples_top_k: int = 3
    # Confidence floor for example injection: the best retrieved example must have a
    # cosine similarity >= this to the question, else NO examples are injected (the
    # generator falls back to schema-only, today's baseline) — so a novel question that
    # isn't in the example set never gets a misleading example. 0.0 => gate off. Only
    # applies when the dense embedding backend is available (it is the confidence signal).
    examples_min_score: float = 0.0
    # RRF fusion weights (Pattern Retriever only, see example_index.py): DENSE carries
    # semantic/logical similarity, BM25 carries exact banking-jargon/column overlap.
    # Weighted above 50/50 toward dense so a lexically-similar-but-logically-different
    # example (shares nouns, different SQL pattern) doesn't out-vote a genuine semantic
    # match — RRF is rank-based, so without weights the two rankers get an equal vote
    # regardless of how strong the dense similarity actually is.
    examples_dense_weight: float = 0.7
    examples_bm25_weight: float = 0.3
    # bge/e5 query-side instruction for EXAMPLE retrieval specifically — deliberately
    # separate from embedding_query_prefix (which is worded for schema/table retrieval)
    # since the instruction measurably conditions the embedding space.
    examples_embedding_query_prefix: str = (
        "Represent this business question for retrieving a SQL example with the same "
        "query logic and business intent: ")

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

    # Bounded widen for the dynamic-tier self-correction retry (query_engine.py). On a
    # validator error, DB error, or CANNOT_ANSWER against the narrow retrieval slice,
    # the retry re-selects this many candidates instead of falling back to literally
    # every table/view in the schema — the full-schema render is the single biggest
    # prompt component and was blowing the provider's per-minute token budget on
    # retries. Keep well under embedding_top_k*3 (the dense-ranking pool size in
    # selector.py) so there is always enough already-computed candidate depth to widen
    # into without an extra retrieval pass.
    schema_retrieval_widen_top_k: int = 14

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
    # Separate collection for the few-shot example vectors (Pattern Retriever). Shares the
    # SAME Qdrant client/connection as the schema collection — Qdrant local/on-disk mode
    # file-locks per path, so one client hosts both collections. Blank faiss path => the
    # faiss backend keeps example vectors in-RAM.
    examples_qdrant_collection: str = "sql_agent_examples"
    examples_vector_index_path: str = ""       # faiss backend for examples; blank = in-RAM

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
