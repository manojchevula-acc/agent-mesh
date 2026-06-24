import os
import pathlib
from dotenv import load_dotenv

# Always load from agent-mesh/.env regardless of the subprocess's CWD.
# override=True ensures the file wins over any stale shell-level env vars.
_ENV_FILE = pathlib.Path(__file__).resolve().parents[1] / ".env"
load_dotenv(_ENV_FILE, override=True)

class Config:
    # LLM provider — Groq (OpenAI-compatible cloud inference)
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

    # Keep the legacy JSONL trace sink? Off by default now that workflow/agent
    # spans cover the same ground (avoids duplicate telemetry).
    ENABLE_TRACE_JSONL: bool = os.getenv("ENABLE_TRACE_JSONL", "false").lower() in ("1", "true", "yes")

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
    MCP_REQUEST_TIMEOUT: int = int(os.getenv("MCP_REQUEST_TIMEOUT", "120"))

    # Timeout (seconds) for A2A calls. Groq is fast (~70+ tok/s); 60 s is generous
    # headroom for complex multi-step reasoning.
    A2A_TIMEOUT: float = float(os.getenv("A2A_TIMEOUT", "180.0"))

    @classmethod
    def agent_url(cls, name: str) -> str:
        """Returns the base A2A URL for a named agent node."""
        port = cls.AGENT_PORTS[name]
        return f"http://{cls.A2A_HOST}:{port}/"

    @classmethod
    def validate(cls):
        """Validates configuration sanity."""
        if not cls.GROQ_API_KEY:
            raise ValueError("Invalid Configuration: GROQ_API_KEY is required.")
        if not cls.GROQ_MODEL:
            raise ValueError("Invalid Configuration: GROQ_MODEL is required.")

    @classmethod
    def check_groq(cls) -> tuple[bool, str]:
        """Fast pre-flight check for Groq: verifies the API key is set and the
        model name is non-empty. Does not make a network call — Groq responds
        immediately at inference time; there is no equivalent of Ollama's /api/tags.
        """
        if not cls.GROQ_API_KEY:
            return False, (
                "GROQ_API_KEY is not set. Add it to agent-mesh/.env "
                "(e.g. GROQ_API_KEY=gsk_...). Without it, all agents will fail "
                "to connect to Groq and return errors at inference time."
            )
        if not cls.GROQ_MODEL:
            return False, "GROQ_MODEL is not set. Add it to agent-mesh/.env (e.g. GROQ_MODEL=openai/gpt-oss-20b)."
        return True, (
            f"Groq configured — key=gsk_***{cls.GROQ_API_KEY[-4:]} | "
            f"compliance={cls.COMPLIANCE_MODEL} | data={cls.DATA_AGENT_MODEL} | "
            f"rag={cls.RAG_AGENT_MODEL} | price_assist={cls.PRICE_ASSIST_MODEL}"
        )
