import sys
import pathlib

# Ensure project root is in sys.path
project_root = str(pathlib.Path(__file__).resolve().parents[2])
if project_root not in sys.path:
    sys.path.insert(0, project_root)

from typing import List, Optional
from agent_framework import Agent, AgentMiddleware
from agent_framework.ollama import OllamaChatClient
from src.config import Config
from src.middleware.audit_middleware import AuditMiddleware

def create_demo_agent(
    name: str, 
    instructions: str, 
    extra_middlewares: Optional[List[AgentMiddleware]] = None,
    log_path: str = None
) -> Agent:
    """
    Creates and returns a Microsoft Agent Framework Agent powered by Ollama.
    """
    # 1. Instantiate local Ollama client
    client = OllamaChatClient(
        model=Config.OLLAMA_MODEL,
        host=Config.OLLAMA_HOST
    )

    # 2. Setup standard middleware (Audit trail)
    audit = AuditMiddleware(log_path=log_path)
    
    middlewares = [audit]
    if extra_middlewares:
        middlewares.extend(extra_middlewares)

    # 3. Create Agent
    agent_instance = Agent(
        client=client,
        name=name,
        instructions=instructions,
        middleware=middlewares
    )
    return agent_instance
