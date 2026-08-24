"""Docker-free single-process dev entry point for the agent mesh.

Why this exists
---------------
Runs the entire mesh in one process — no A2A HTTP hops — so every span stays
in-process and is easy to inspect with a local OTLP collector (Aspire/Jaeger).
The production mesh spawns four separate processes via launch_mesh.py; this
entry point collapses them into one for local development and debugging.

How it works
------------
Instead of ``ask_remote()`` (httpx POST over A2A), we build every node agent
in-process and patch ``src.mesh.orchestrator.ask_remote`` with a local
transport that calls each agent's ``ainvoke()`` directly. The same
``handle_request()`` orchestrator and ``build_mesh_workflow()`` graph are
used — only the transport layer differs.

Run
---
    1. Fill in .env with a valid GROQ_API_KEY / Cerebras key.
    2. ``python devui_app.py``  -> interactive REPL at the terminal.
    3. Type a query and press Enter. Type 'exit' to quit.

DevUI is a development tool only — not a production hosting surface.
"""
import os
import asyncio
import sys
import pathlib

# Single-process mode: use dev OTel profile so no external collector is required.
os.environ.setdefault("OBS_PROFILE", "dev")
os.environ.setdefault("PYTHONWARNINGS", "ignore")

project_root = str(pathlib.Path(__file__).resolve().parent)
if project_root not in sys.path:
    sys.path.insert(0, project_root)

from src.observability import setup_observability, get_logger, CAT_SYSTEM

setup_observability(service_name="agent_mesh_devui")
_log = get_logger(CAT_SYSTEM)

from src.config import Config
from src.auth.identity_provider import login
from src.agents.node_registry import NODE_NAMES, build_node
from langchain_core.messages import HumanMessage


def _make_local_ask(agents: dict):
    """In-process replacement for the A2A ``ask_remote`` transport.

    Calls the target node agent's ``ainvoke()`` directly instead of an HTTP hop,
    so the full trace stays in one process. Signature matches ``ask_remote``.
    """
    async def local_ask(name: str, prompt: str, *_args, **_kwargs) -> str:
        agent = agents.get(name)
        if agent is None:
            raise ValueError(f"Unknown mesh node '{name}'. Valid: {', '.join(NODE_NAMES)}")
        result = await agent.ainvoke({"messages": [HumanMessage(content=prompt)]})
        messages = result.get("messages", [])
        if messages:
            last = messages[-1]
            return getattr(last, "content", str(last))
        return str(result)

    return local_ask


async def _run_repl() -> None:
    Config.validate()

    ok, msg = Config.check_groq()
    if not ok:
        _log.error("DevUI startup blocked: %s", msg)
        print(f"[devui] ERROR: {msg}")
        sys.exit(1)
    print(f"[devui] {msg}")

    # Build all node agents in-process.
    agents = {name: build_node(name)[0] for name in NODE_NAMES}

    # Patch the orchestrator's ask_remote with our in-process local transport.
    import src.mesh.orchestrator as _orch
    _orch.ask_remote = _make_local_ask(agents)

    from src.mesh.orchestrator import handle_request

    # Resolve the DevUI user from the mock corporate directory.
    user = login(Config.DEVUI_USER)

    print("=" * 72)
    print("  AGENT MESH — DevUI (single-process, in-process transport)")
    print("=" * 72)
    print(f"  User:   {user.username}  Role: {user.role.value}")
    print(f"  Nodes:  {', '.join(NODE_NAMES)}")
    print("  Type a query and press Enter. Type 'exit' to quit.")
    print("-" * 72)

    while True:
        try:
            query = input("\n[devui] > ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\n[devui] Exiting.")
            break

        if not query or query.lower() in ("exit", "quit", "q"):
            print("[devui] Exiting.")
            break

        try:
            result = await handle_request(user=user, query=query)
            print("\n[devui] Answer:")
            print(result.answer)
            if result.blocked:
                print(f"[devui] BLOCKED at stage: {result.block_stage}")
        except Exception as exc:
            _log.exception("DevUI request failed: %s", exc)
            print(f"[devui] ERROR: {exc}")


def main() -> None:
    asyncio.run(_run_repl())


if __name__ == "__main__":
    main()
