import os
import pathlib
from dotenv import load_dotenv

# Always load from agent-mesh/.env regardless of the subprocess's CWD.
# override=True ensures the file wins over any stale shell-level env vars.
_ENV_FILE = pathlib.Path(__file__).resolve().parents[1] / ".env"
load_dotenv(_ENV_FILE, override=True)

class Config:
    # LLM provider — OpenAI-compatible endpoint (Groq by default; swap for Ollama/Cerebras)
    LLM_BASE_URL: str = os.getenv("LLM_BASE_URL", "https://api.groq.com/openai/v1")
    GROQ_API_KEY: str = os.getenv("GROQ_API_KEY", "")
    GROQ_MODEL:   str = os.getenv("GROQ_MODEL",   "openai/gpt-oss-20b")

    # Per-agent model overrides — each agent is wired to the model best suited
    # for its task. Override individually via env vars; falls back to GROQ_MODEL.
    COMPLIANCE_MODEL:   str = os.getenv("COMPLIANCE_MODEL",   "openai/gpt-oss-20b")
    DATA_AGENT_MODEL:   str = os.getenv("DATA_AGENT_MODEL",   "qwen/qwen3.6-27b")
    RAG_AGENT_MODEL:    str = os.getenv("RAG_AGENT_MODEL",    "qwen/qwen3.6-27b")
    PRICE_ASSIST_MODEL: str = os.getenv("PRICE_ASSIST_MODEL", "openai/gpt-oss-120b")

    # Per-agent API keys — spread across two keys to avoid hitting rate limits.
    # Compliance + Data Agent use Key 1; RAG Agent + Price Assist use Key 2.
    # All fall back to GROQ_API_KEY if the per-agent var is unset or empty.
    COMPLIANCE_API_KEY:   str = os.getenv("COMPLIANCE_API_KEY",   "") or os.getenv("GROQ_API_KEY", "")
    DATA_AGENT_API_KEY:   str = os.getenv("DATA_AGENT_API_KEY",   "") or os.getenv("GROQ_API_KEY", "")
    RAG_AGENT_API_KEY:    str = os.getenv("RAG_AGENT_API_KEY",    "") or os.getenv("GROQ_API_KEY", "")
    PRICE_ASSIST_API_KEY: str = os.getenv("PRICE_ASSIST_API_KEY", "") or os.getenv("GROQ_API_KEY", "")

    # Ollama (local) — kept for rollback; not used when GROQ_API_KEY is set
    # OLLAMA_HOST: str = os.getenv("OLLAMA_HOST", "http://localhost:11434")
    # OLLAMA_MODEL: str = os.getenv("OLLAMA_MODEL", "llama3.1:8b")
    
    # Storage and paths
    POLICIES_FILE: str = os.getenv("POLICIES_FILE", "data/policies.json")
    AUDIT_LOG_FILE: str = os.getenv("AUDIT_LOG_FILE", "data/audit_trail.jsonl")
    TRACE_LOG_FILE: str = os.getenv("TRACE_LOG_FILE", "data/trace_log.jsonl")  # structured trace events for all mesh layers
    CONVERSATION_STORE_DIR: str = os.getenv("CONVERSATION_STORE_DIR", "data/conversations")
    # Source directory used by the batch ingest pipeline (src.cache.ingest_pipeline).
    # Defaults to the cleaned_conversations subfolder — separate from CONVERSATION_STORE_DIR
    # so the memory system and ingest pipeline can point to different directories.
    CACHE_INGEST_SOURCE_DIR: str = os.getenv("CACHE_INGEST_SOURCE_DIR", "data/conversations/cleaned_conversations")

    # ----------------------------------------------------------------------
    # Conversational memory (Option B — MAF thread memory + JSONL persistence)
    # ----------------------------------------------------------------------
    # Multi-turn memory: the orchestrator loads prior turns for a session_id and
    # injects them into the PriceAssistAgent prompt; turns are persisted per session.
    ENABLE_CONVERSATION_MEMORY: bool = os.getenv("ENABLE_CONVERSATION_MEMORY", "true").lower() in ("1", "true", "yes")
    # How many prior Q/A turns to replay into the prompt (legacy — used only when rolling summarization is off).
    CONVERSATION_MAX_TURNS: int = int(os.getenv("CONVERSATION_MAX_TURNS", "3"))
    # Storage backend: "jsonl" (active default, file-based) | "redis" (placeholder for future use).
    CONVERSATION_BACKEND: str = os.getenv("CONVERSATION_BACKEND", "jsonl")
    # Connection URL used only by the future Redis backend (placeholder — not active yet).
    CONVERSATION_REDIS_URL: str = os.getenv("CONVERSATION_REDIS_URL", "redis://127.0.0.1:6379/0")
    # Rolling LLM summarization — summarizes all prior turns into a ≤200-word block instead of truncating.
    ENABLE_ROLLING_SUMMARIZATION: bool = os.getenv("ENABLE_ROLLING_SUMMARIZATION", "true").lower() in ("1", "true", "yes")
    # Model used for summarization calls (defaults to GROQ_MODEL; override for a cheaper/faster model).
    SUMMARY_MODEL: str = os.getenv("SUMMARY_MODEL", "")

    # ----------------------------------------------------------------------
    # Observability (Microsoft Agent Framework-native OpenTelemetry + logging)
    # ----------------------------------------------------------------------
    # OBS_PROFILE selects the exporter wiring:
    #   "dev"  -> console + OTLP (Aspire/Jaeger at OTEL_EXPORTER_OTLP_ENDPOINT)
    #   "prod" -> Azure Monitor / Application Insights (requires connection string)
    #   "off"  -> file logging only, no OTel providers
    OBS_PROFILE: str = os.getenv("OBS_PROFILE", "dev")

    # Agent Framework reads these standard env vars in configure_otel_providers().
    # We surface them here so a single .env drives both the SDK and our logging.
    ENABLE_INSTRUMENTATION: bool = os.getenv("ENABLE_INSTRUMENTATION", "true").lower() in ("1", "true", "yes")
    ENABLE_SENSITIVE_DATA: bool = os.getenv("ENABLE_SENSITIVE_DATA", "false").lower() in ("1", "true", "yes")
    ENABLE_CONSOLE_EXPORTERS: bool = os.getenv("ENABLE_CONSOLE_EXPORTERS", "false").lower() in ("1", "true", "yes")
    OTEL_EXPORTER_OTLP_ENDPOINT: str = os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://localhost:4317")
    OTEL_SERVICE_NAME: str = os.getenv("OTEL_SERVICE_NAME", "agent_mesh")
    APPLICATIONINSIGHTS_CONNECTION_STRING: str = os.getenv("APPLICATIONINSIGHTS_CONNECTION_STRING", "")

    # Grafana Cloud OTLP (OBS_PROFILE=grafana)
    GRAFANA_OTLP_ENDPOINT: str = os.getenv("GRAFANA_OTLP_ENDPOINT", "")
    GRAFANA_INSTANCE_ID: str = os.getenv("GRAFANA_INSTANCE_ID", "")
    GRAFANA_API_TOKEN: str = os.getenv("GRAFANA_API_TOKEN", "")

    # Centralized application logging (durable, rotating, trace-correlated).
    LOG_LEVEL: str = os.getenv("LOG_LEVEL", "INFO").upper()
    LOG_FILE: str = os.getenv("LOG_FILE", "data/logs/agent_mesh.log")
    LOG_JSON: bool = os.getenv("LOG_JSON", "false").lower() in ("1", "true", "yes")
    LOG_MAX_BYTES: int = int(os.getenv("LOG_MAX_BYTES", str(10 * 1024 * 1024)))  # 10 MB
    LOG_BACKUP_COUNT: int = int(os.getenv("LOG_BACKUP_COUNT", "5"))

    # Per-request log files: one file per request_id under LOG_REQUEST_DIR so a
    # single request's cross-layer story is isolated and easy to investigate.
    LOG_PER_REQUEST: bool = os.getenv("LOG_PER_REQUEST", "true").lower() in ("1", "true", "yes")
    LOG_REQUEST_DIR: str = os.getenv("LOG_REQUEST_DIR", "data/logs/requests")
    LOG_REQUEST_MAX_OPEN: int = int(os.getenv("LOG_REQUEST_MAX_OPEN", "32"))  # LRU cap on open handles

    # Human-readable state-transition trace: shows MeshState field deltas and the
    # A2A payloads sent/received as the state flows executor -> executor.
    LOG_STATE_TRACE: bool = os.getenv("LOG_STATE_TRACE", "true").lower() in ("1", "true", "yes")
    STATE_TRACE_DIR: str = os.getenv("STATE_TRACE_DIR", "data/logs/state")  # one file per request_id
    STATE_TRACE_PREVIEW_CHARS: int = int(os.getenv("STATE_TRACE_PREVIEW_CHARS", "200"))

    # Keep the legacy JSONL trace sink? Off by default now that workflow/agent
    # spans cover the same ground (avoids duplicate telemetry).
    ENABLE_TRACE_JSONL: bool = os.getenv("ENABLE_TRACE_JSONL", "false").lower() in ("1", "true", "yes")

    # Custom business metrics (counters + histograms) for guardrails, RBAC,
    # compliance, routing, A2A hops, and mesh-level request outcomes.
    # Set to false to suppress custom metric cardinality in tight environments.
    ENABLE_BUSINESS_METRICS: bool = os.getenv("ENABLE_BUSINESS_METRICS", "true").lower() in ("1", "true", "yes")

    # ----------------------------------------------------------------------
    # Mesh node availability — set false when a node is NOT in START_ORDER.
    # ENABLE_PRICE_ASSIST=false: DomainExecutor calls data_agent directly,
    #   bypassing PriceAssist intent classification (data-only dev mode).
    # ENABLE_COMPLIANCE=false:   ComplianceExecutor skips the A2A call and
    #   stamps a pass verdict (deterministic guardrail + RBAC still run).
    # Both default to true so the full 4-node mesh works with no .env changes.
    # ----------------------------------------------------------------------
    ENABLE_PRICE_ASSIST: bool = os.getenv("ENABLE_PRICE_ASSIST", "true").lower() in ("1", "true", "yes")
    ENABLE_COMPLIANCE:   bool = os.getenv("ENABLE_COMPLIANCE",   "true").lower() in ("1", "true", "yes")

    # ----------------------------------------------------------------------
    # Semantic Response Cache (ChromaDB + sentence-transformers)
    # After RBAC validation, check if a semantically similar question was
    # answered recently for the same role. Cache hit skips Compliance + Domain.
    # ENABLE_RESPONSE_CACHE=false (default): CacheCheckExecutor is a no-op.
    # ----------------------------------------------------------------------
    ENABLE_RESPONSE_CACHE:      bool  = os.getenv("ENABLE_RESPONSE_CACHE",      "false").lower() in ("1", "true", "yes")
    CACHE_MAX_AGE_HOURS:        float = float(os.getenv("CACHE_MAX_AGE_HOURS",   "24.0"))
    CACHE_SIMILARITY_THRESHOLD: float = float(os.getenv("CACHE_SIMILARITY_THRESHOLD", "0.92"))
    CACHE_CHROMA_DIR:           str   = os.getenv("CACHE_CHROMA_DIR",           "data/cache/chroma")
    CACHE_EMBED_MODEL:          str   = os.getenv("CACHE_EMBED_MODEL",          "all-MiniLM-L6-v2")
    CACHE_COLLECTION_NAME:      str   = os.getenv("CACHE_COLLECTION_NAME",      "mesh_response_cache")

    # LLM Judge for gray-zone cache validation (similarity between CACHE_MISS_THRESHOLD and CACHE_INTENT_MATCH_THRESHOLD).
    # When cosine similarity is ambiguous, a lightweight LLM call decides YES/NO instead of hard-threshold rejection.
    # Set CACHE_JUDGE_ENABLED=false to restore the original single-threshold behavior.
    CACHE_MISS_THRESHOLD:  float = float(os.getenv("CACHE_MISS_THRESHOLD", "0.75"))
    CACHE_JUDGE_ENABLED:   bool  = os.getenv("CACHE_JUDGE_ENABLED", "true").lower() in ("1", "true", "yes")
    CACHE_JUDGE_MODEL:     str   = os.getenv("CACHE_JUDGE_MODEL", "openai/gpt-oss-20b")

    # Intent-match suggestion zone — when CACHE_INTENT_MATCH_ENABLED=true, queries with similarity
    # in [CACHE_MISS_THRESHOLD, CACHE_SIMILARITY_THRESHOLD) are surfaced to the user as an
    # "intent suggestion" popup instead of auto-serving (high-confidence zone) or running the LLM
    # judge silently (gray zone). The user decides whether to use the cached answer or run fresh.
    # Gray zone (CACHE_MISS_THRESHOLD ≤ sim < CACHE_INTENT_MATCH_THRESHOLD): LLM judge also runs
    # concurrently to provide a confidence signal shown in the UI.
    # Set to false (default) to preserve existing behavior with no UX changes.
    CACHE_INTENT_MATCH_ENABLED:   bool  = os.getenv("CACHE_INTENT_MATCH_ENABLED",   "false").lower() in ("1", "true", "yes")
    CACHE_INTENT_MATCH_THRESHOLD: float = float(os.getenv("CACHE_INTENT_MATCH_THRESHOLD", "0.85"))

    # Entity-aware cache gating — extract the entities a query is about (customer/
    # account/deal IDs, people, products, time scope, amounts, ...) and only allow a
    # cached candidate to survive when its entity signature EXACTLY matches the incoming
    # query's. Prevents serving CUST001's answer for a CUST002 query (same intent,
    # different entity — a collision the dense embedding scores above the HIT threshold).
    # LLM-based extraction (covers all entity kinds) with a deterministic regex fallback.
    #   hard  → entity mismatch drops the candidate (treated as MISS)  [default]
    #   soft  → entity mismatch demotes the candidate to the gray zone (LLM judge decides)
    CACHE_ENTITY_GATING_ENABLED:  bool  = os.getenv("CACHE_ENTITY_GATING_ENABLED", "true").lower() in ("1", "true", "yes")
    CACHE_ENTITY_MODEL:           str   = os.getenv("CACHE_ENTITY_MODEL", os.getenv("CACHE_JUDGE_MODEL", "openai/gpt-oss-20b"))
    CACHE_ENTITY_GATE_MODE:       str   = os.getenv("CACHE_ENTITY_GATE_MODE", "hard").lower()
    CACHE_ENTITY_EXTRACT_TIMEOUT: float = float(os.getenv("CACHE_ENTITY_EXTRACT_TIMEOUT", "5.0"))
    # Bulk ingest: extract entities for many queries per LLM call (avoids rate limits),
    # and retry transient failures (HTTP 429 / connection / SSL) with exponential backoff.
    CACHE_ENTITY_BATCH_SIZE:      int   = int(os.getenv("CACHE_ENTITY_BATCH_SIZE", "15"))
    CACHE_ENTITY_MAX_RETRIES:     int   = int(os.getenv("CACHE_ENTITY_MAX_RETRIES", "3"))

    # Phase 4 — Cross-encoder reranker (augment). A local cross-encoder re-orders the
    # retrieved candidates and drops low-relevance ones before the LLM judge runs.
    # Fully local (sentence-transformers) — no network, immune to 429/proxy-SSL issues.
    CACHE_RERANKER_ENABLED:   bool  = os.getenv("CACHE_RERANKER_ENABLED", "false").lower() in ("1", "true", "yes")
    CACHE_RERANKER_MODEL:     str   = os.getenv("CACHE_RERANKER_MODEL", "cross-encoder/ms-marco-MiniLM-L-6-v2")
    CACHE_RERANK_MIN_SCORE:   float = float(os.getenv("CACHE_RERANK_MIN_SCORE", "-5.0"))

    # Phase 7a — skip caching negative / "no data found" answers (live store path).
    CACHE_SKIP_NEGATIVE:      bool  = os.getenv("CACHE_SKIP_NEGATIVE", "true").lower() in ("1", "true", "yes")
    # Phase 7b — durable log of rejected cache HITs (false-positive signal for tuning).
    CACHE_REJECTIONS_LOG:     str   = os.getenv("CACHE_REJECTIONS_LOG", "data/cache_rejections.jsonl")

    # Phase 2 — embed a canonical form (entities → placeholders) so paraphrases of the
    # same intent cluster tightly. Changing this invalidates existing vectors → re-embed.
    CACHE_CANONICALIZE_ENABLED: bool = os.getenv("CACHE_CANONICALIZE_ENABLED", "false").lower() in ("1", "true", "yes")

    # Phase 3 — hybrid dense + sparse (BM25) retrieval fused via Reciprocal Rank Fusion.
    CACHE_HYBRID_ENABLED:     bool  = os.getenv("CACHE_HYBRID_ENABLED", "false").lower() in ("1", "true", "yes")
    CACHE_HYBRID_FETCH_K:     int   = int(os.getenv("CACHE_HYBRID_FETCH_K", "20"))

    # Inline store: when true, the orchestrator writes new Q/A pairs to ChromaDB immediately
    # after each pipeline run (original behavior). Set to false to disable inline writes and
    # use the ingest pipeline (src.cache.ingest_pipeline) for batch embedding instead.
    CACHE_INLINE_STORE_ENABLED: bool = os.getenv("CACHE_INLINE_STORE_ENABLED", "true").lower() in ("1", "true", "yes")

    # Paraphrase augmentation — ingest-time only. When enabled, each ingested Q/A pair
    # generates N additional paraphrase variants (via LLM) stored as separate ChromaDB
    # entries pointing to the same answer. Widens the cache hit radius without affecting
    # query-time latency. Disabled by default; enable for bulk ingest runs.
    CACHE_PARAPHRASE_ENABLED: bool = os.getenv("CACHE_PARAPHRASE_ENABLED", "false").lower() in ("1", "true", "yes")
    CACHE_PARAPHRASE_N: int = int(os.getenv("CACHE_PARAPHRASE_N", "3"))
    # Seconds to sleep between paraphrase LLM calls to respect provider RPM limits.
    # Entity extraction also calls the LLM, so effective call rate is 2× per Q/A pair.
    # At 5 s the combined rate stays ~12 RPM — well within Cerebras free-tier limits.
    CACHE_PARAPHRASE_DELAY_S: float = float(os.getenv("CACHE_PARAPHRASE_DELAY_S", "5.0"))

    # ----------------------------------------------------------------------
    # User feedback — thumbs up/down + comment stored for future fine-tuning.
    # Records include a fine_tune_record.messages array (OpenAI/Anthropic format)
    # so the JSONL can be exported directly to a fine-tuning job.
    # ----------------------------------------------------------------------
    FEEDBACK_LOG_FILE: str = os.getenv("FEEDBACK_LOG_FILE", "data/feedback.jsonl")

    # ----------------------------------------------------------------------
    # DevUI (Microsoft Agent Framework dev tool) — Docker-free live trace viewer
    # ----------------------------------------------------------------------
    # ``devui_app.py`` runs the whole mesh in ONE process so DevUI can capture the
    # full in-process trace tree (workflow -> executors -> agents -> tools). DevUI
    # is a development-only sample app; do not expose it as a production surface.
    DEVUI_HOST: str = os.getenv("DEVUI_HOST", "127.0.0.1")
    DEVUI_PORT: int = int(os.getenv("DEVUI_PORT", "8090"))
    # Identity stamped on DevUI requests (used for audit logging on each hop).
    DEVUI_USER: str = os.getenv("DEVUI_USER", "devui")
    DEVUI_ROLE: str = os.getenv("DEVUI_ROLE", "platform_administrator")
    DEVUI_AUTO_OPEN: bool = os.getenv("DEVUI_AUTO_OPEN", "true").lower() in ("1", "true", "yes")
    # No-auth is only honoured on loopback hosts by DevUI itself.
    DEVUI_NO_AUTH: bool = os.getenv("DEVUI_NO_AUTH", "true").lower() in ("1", "true", "yes")

    # Mesh networking: each agent is hosted as an isolated A2A server on its own port.
    A2A_HOST: str = os.getenv("A2A_HOST", "127.0.0.1")

    # name -> port. Each agent is hosted as an isolated A2A node.
    # AgentMesh 15.0.6.2026: GatewayAgent and PolicyAgent removed.
    # PriceAssistAgent is the primary orchestrator; DataAgent and RAGAgent are
    # thin MCP clients. NOTE: ports chosen to avoid Windows reserved ranges.
    # Override via PORT_* env vars if needed.
    AGENT_PORTS: dict[str, int] = {
        "compliance":  int(os.getenv("PORT_COMPLIANCE",  "8015")),
        "data_agent":  int(os.getenv("PORT_DATA_AGENT",  "8016")),
        "rag_agent":   int(os.getenv("PORT_RAG_AGENT",   "8017")),
        "price_assist": int(os.getenv("PORT_PRICE_ASSIST", "8018")),
    }

    # ----------------------------------------------------------------------
    # External services consumed by domain agents over MCP (streamable HTTP).
    # These services run independently on their own ports/processes; the mesh
    # agents are thin clients that consume the services' MCP tool surface.
    #   - DataLayer-as-a-Service: FastMCP server (5 SQL-view tools).
    #   - RAG-as-a-Service: MCP server (search_documents) wrapping its REST API.
    # ----------------------------------------------------------------------
    DATALAYER_MCP_URL: str = os.getenv("DATALAYER_MCP_URL", "http://127.0.0.1:9100/mcp")
    RAG_MCP_URL: str = os.getenv("RAG_MCP_URL", "http://127.0.0.1:9000/mcp")
    # Optional API key if the RAG MCP server is configured to require one.
    RAG_API_KEY: str = os.getenv("RAG_API_KEY", "")
    # Timeout (seconds) for an MCP tool request to an external service.
    MCP_REQUEST_TIMEOUT: int = int(os.getenv("MCP_REQUEST_TIMEOUT", "30"))

    # Timeout (seconds) for A2A calls. Groq is fast (~70+ tok/s); 60 s is generous
    # headroom for complex multi-step reasoning.
    A2A_TIMEOUT: float = float(os.getenv("A2A_TIMEOUT", "60.0"))

    @classmethod
    def agent_url(cls, name: str) -> str:
        """Returns the base A2A URL for a named agent node."""
        port = cls.AGENT_PORTS[name]
        return f"http://{cls.A2A_HOST}:{port}/"

    @classmethod
    def validate(cls):
        """Validates configuration sanity."""
        if not cls.GROQ_API_KEY:
            raise ValueError("Invalid Configuration: GROQ_API_KEY (LLM API key) is required.")
        if not cls.GROQ_MODEL:
            raise ValueError("Invalid Configuration: GROQ_MODEL (LLM model name) is required.")

    @classmethod
    def check_groq(cls) -> tuple[bool, str]:
        """Fast pre-flight check: verifies the LLM API key and model name are set.
        Does not make a network call.
        """
        if not cls.GROQ_API_KEY:
            return False, (
                "GROQ_API_KEY is not set. Add it to agent-mesh/.env. "
                "Without it, all agents will fail to connect to the LLM and return errors at inference time."
            )
        if not cls.GROQ_MODEL:
            return False, "GROQ_MODEL is not set. Add it to agent-mesh/.env."
        return True, (
            f"LLM configured — url={cls.LLM_BASE_URL} key=***{cls.GROQ_API_KEY[-4:]} | "
            f"compliance={cls.COMPLIANCE_MODEL} | data={cls.DATA_AGENT_MODEL} | "
            f"rag={cls.RAG_AGENT_MODEL} | price_assist={cls.PRICE_ASSIST_MODEL}"
        )
