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
