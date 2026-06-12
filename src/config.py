import os
from dotenv import load_dotenv

# Load environment variables from .env
load_dotenv()

class Config:
    OLLAMA_HOST: str = os.getenv("OLLAMA_HOST", "http://localhost:11434")
    OLLAMA_MODEL: str = os.getenv("OLLAMA_MODEL", "llama3.2")
    
    # Storage and paths
    POLICIES_FILE: str = os.getenv("POLICIES_FILE", "data/policies.json")
    AUDIT_LOG_FILE: str = os.getenv("AUDIT_LOG_FILE", "data/audit_trail.jsonl")
    CONVERSATION_STORE_DIR: str = os.getenv("CONVERSATION_STORE_DIR", "data/conversations")
    
    @classmethod
    def validate(cls):
        """Validates configuration sanity."""
        if not cls.OLLAMA_HOST:
            raise ValueError("Invalid Configuration: OLLAMA_HOST is required.")
        if not cls.OLLAMA_MODEL:
            raise ValueError("Invalid Configuration: OLLAMA_MODEL is required.")
