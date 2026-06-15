"""A2A client helpers — connect to remote agent nodes over the A2A protocol."""
import sys
import pathlib

# Ensure project root is in sys.path
project_root = str(pathlib.Path(__file__).resolve().parents[2])
if project_root not in sys.path:
    sys.path.insert(0, project_root)

from agent_framework.a2a import A2AAgent

from src.config import Config


def get_remote_agent(name: str) -> A2AAgent:
    """Returns an A2A client bound to the named agent node's URL."""
    return A2AAgent(
        name=name,
        url=Config.agent_url(name),
        supported_protocol_bindings=["JSONRPC"],
    )


async def ask_remote(name: str, prompt: str) -> str:
    """Sends a prompt to a remote agent node and returns its text response."""
    remote = get_remote_agent(name)
    result = await remote.run(prompt)
    return getattr(result, "text", str(result))
