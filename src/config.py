import os
from dotenv import load_dotenv

# Load environment variables from .env
load_dotenv()

class Config:
    OLLAMA_HOST: str = os.getenv("OLLAMA_HOST", "http://localhost:11434")
    OLLAMA_MODEL: str = os.getenv("OLLAMA_MODEL", "llama3.2")
    
    # Storage and paths
    POLICIES_FILE: str = os.getenv("POLICIES_FILE", "data/policies.json")
    JOB_POSTINGS_FILE: str = os.getenv("JOB_POSTINGS_FILE", "data/job_postings.json")
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
    # Identity stamped on DevUI requests. Default role is leadership so the finance
    # domain isn't access-blocked, letting you exercise every node from the UI.
    DEVUI_USER: str = os.getenv("DEVUI_USER", "devui")
    DEVUI_ROLE: str = os.getenv("DEVUI_ROLE", "leadership")
    DEVUI_AUTO_OPEN: bool = os.getenv("DEVUI_AUTO_OPEN", "true").lower() in ("1", "true", "yes")
    # No-auth is only honoured on loopback hosts by DevUI itself.
    DEVUI_NO_AUTH: bool = os.getenv("DEVUI_NO_AUTH", "true").lower() in ("1", "true", "yes")

    # Mesh networking: each agent is hosted as an isolated A2A server on its own port.
    A2A_HOST: str = os.getenv("A2A_HOST", "127.0.0.1")

    # name -> port. The gateway is the single front-door; the rest are specialist nodes.
    # NOTE: ports chosen to avoid Windows excluded/reserved ranges (e.g. 8005 is
    # commonly reserved by WinNAT/Hyper-V). Override via PORT_* env vars if needed.
    AGENT_PORTS: dict[str, int] = {
        "gateway": int(os.getenv("PORT_GATEWAY", "8010")),
        "finance": int(os.getenv("PORT_FINANCE", "8011")),
        "hr": int(os.getenv("PORT_HR", "8012")),
        "internal_job": int(os.getenv("PORT_INTERNAL_JOB", "8013")),
        "policy": int(os.getenv("PORT_POLICY", "8014")),
        "compliance": int(os.getenv("PORT_COMPLIANCE", "8015")),
    }

    # Timeout (seconds) for A2A calls. LLM responses can be slow under parallel load;
    # 180 s covers 3 sequential Ollama completions (~30-60 s each) with headroom.
    A2A_TIMEOUT: float = float(os.getenv("A2A_TIMEOUT", "180.0"))

    @classmethod
    def agent_url(cls, name: str) -> str:
        """Returns the base A2A URL for a named agent node."""
        port = cls.AGENT_PORTS[name]
        return f"http://{cls.A2A_HOST}:{port}/"

    @classmethod
    def validate(cls):
        """Validates configuration sanity."""
        if not cls.OLLAMA_HOST:
            raise ValueError("Invalid Configuration: OLLAMA_HOST is required.")
        if not cls.OLLAMA_MODEL:
            raise ValueError("Invalid Configuration: OLLAMA_MODEL is required.")

    @classmethod
    def check_ollama(cls, timeout: float = 3.0) -> tuple[bool, str]:
        """Fast health check for the Ollama backend.

        Returns ``(ok, message)``. ``ok`` is False if Ollama is unreachable or the
        configured model is not pulled — either of which makes every mesh agent
        silently fall back to echoing the request (no real LLM answer). Callers
        should surface ``message`` clearly so the failure is obvious at startup
        instead of showing up as an "echo" at query time.
        """
        import json as _json
        import urllib.request as _urlreq
        import urllib.error as _urlerr

        url = cls.OLLAMA_HOST.rstrip("/") + "/api/tags"
        try:
            with _urlreq.urlopen(url, timeout=timeout) as resp:
                data = _json.loads(resp.read().decode("utf-8", errors="replace"))
        except (_urlerr.URLError, OSError) as e:
            return False, (
                f"Ollama is not reachable at {cls.OLLAMA_HOST} ({e}). "
                f"Start it (e.g. 'ollama serve') and pull the model "
                f"('ollama pull {cls.OLLAMA_MODEL}'). Without it, agents cannot "
                f"answer and requests will appear to echo the prompt."
            )
        except Exception as e:  # malformed response, etc.
            return False, f"Ollama health check failed at {cls.OLLAMA_HOST} ({e})."

        models = [m.get("name", "") for m in data.get("models", [])]
        base = cls.OLLAMA_MODEL.split(":")[0]
        if not any(name == cls.OLLAMA_MODEL or name.split(":")[0] == base for name in models):
            available = ", ".join(models) or "<none>"
            return False, (
                f"Ollama is running but model '{cls.OLLAMA_MODEL}' is not pulled. "
                f"Run 'ollama pull {cls.OLLAMA_MODEL}'. Available: {available}."
            )
        return True, f"Ollama OK at {cls.OLLAMA_HOST} (model '{cls.OLLAMA_MODEL}')."
