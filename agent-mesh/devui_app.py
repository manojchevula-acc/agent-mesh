"""Docker-free live trace + agent visualization for the agent mesh via DevUI.

Why this exists
---------------
You can't run container-based collectors (Aspire/Jaeger) in this environment, so
instead we use the Microsoft Agent Framework **DevUI** — a built-in, local web app
that renders agent interactions and the OpenTelemetry trace tree in real time.

The catch: Python DevUI only visualizes traces for entities it runs **in its own
process** (it captures spans with an in-memory collector, not a network OTLP
receiver). The production mesh runs each node in a separate A2A server process, so
those spans never reach DevUI. This entrypoint therefore runs the **entire mesh in
one process**: the orchestration workflow calls each node agent directly via an
in-process adapter instead of over A2A. DevUI then captures the full trace tree:

    workflow.run
      └─ executor.process input_guardrail / compliance / policy / output_redaction
            └─ invoke_agent <Node>  └─ chat <model>  └─ execute_tool <fn>

The distributed A2A mesh (``launch_mesh.py`` + ``run.py``) is unchanged; this is a
separate, dev-only lens onto the same agents and the same workflow graph.

Run
---
    1. Ensure Ollama is running (``ollama serve``) and the model is pulled.
    2. ``python devui_app.py``  -> opens http://127.0.0.1:8090
    3. Pick the ``agent_mesh_pipeline`` workflow to watch a full request flow, or
       chat any single node agent directly. Open the Debug panel to see traces.

DevUI is a development tool only — it is not a production hosting surface.
"""
import os

# Single-process mode: no A2A hops, so skip OTLP/Aspire export wiring (which would
# otherwise try to reach a non-existent collector at :4317). DevUI provides the
# tracing surface itself via ``instrumentation_enabled=True`` below. We still want
# the rich, trace-correlated console/file logging, so set the profile to "off"
# (logging only) BEFORE importing the observability setup.
os.environ.setdefault("PYTHONWARNINGS", "ignore")
os.environ["OBS_PROFILE"] = "off"

import sys
import pathlib

project_root = str(pathlib.Path(__file__).resolve().parent)
if project_root not in sys.path:
    sys.path.insert(0, project_root)

from src.observability import setup_observability, get_logger, CAT_SYSTEM

# Activate centralized logging for this process (no OTel exporters; see above).
setup_observability(service_name="agent_mesh_devui")

from agent_framework.devui import serve

from src.config import Config
from src.agents.node_registry import NODE_NAMES, build_node
from src.mesh.workflow import build_devui_workflow

_log = get_logger(CAT_SYSTEM)


def _build_agents() -> dict:
    """Builds every mesh node agent once, in-process.

    The same instances back both the workflow's in-process transport and the
    individually browsable DevUI entities, so a trace started by the workflow and
    a direct chat hit the exact same agent objects.
    """
    return {name: build_node(name)[0] for name in NODE_NAMES}


def _make_local_ask(agents: dict):
    """In-process replacement for the A2A ``ask_remote`` transport.

    Calls the target node agent directly (``agent.run``) instead of crossing an
    A2A boundary, keeping the whole trace in one process for DevUI to capture.
    Signature matches ``ask_remote`` so the workflow executors are unchanged.
    """
    async def local_ask(name: str, prompt: str, *_args, **_kwargs) -> str:
        agent = agents.get(name)
        if agent is None:
            raise ValueError(f"Unknown mesh node '{name}'. Valid: {', '.join(NODE_NAMES)}")
        res = await agent.run(prompt)
        return getattr(res, "text", str(res))

    return local_ask


def main() -> None:
    Config.validate()

    # Fail fast if Ollama is down: otherwise every agent silently echoes the prompt
    # and the traces would look "successful" while answers are meaningless.
    ok, msg = Config.check_ollama()
    if not ok:
        _log.error("DevUI startup blocked: %s", msg)
        print(f"[devui] ERROR: {msg}")
        sys.exit(1)
    print(f"[devui] {msg}")

    agents = _build_agents()
    workflow = build_devui_workflow(
        ask=_make_local_ask(agents),
        user_name=Config.DEVUI_USER,
        role=Config.DEVUI_ROLE,
    )

    # Register the full pipeline AND each node agent so you can watch an end-to-end
    # request or poke a single agent. ``instrumentation_enabled=True`` turns on the
    # framework's OpenTelemetry so DevUI's trace panel is populated.
    entities = [workflow, *agents.values()]

    print("=" * 72)
    print("  AGENT MESH — DevUI (Docker-free live traces)")
    print("=" * 72)
    print(f"  URL:        http://{Config.DEVUI_HOST}:{Config.DEVUI_PORT}")
    print(f"  Identity:   user={Config.DEVUI_USER} role={Config.DEVUI_ROLE}")
    print(f"  Entities:   agent_mesh_pipeline (workflow) + {len(agents)} agents "
          f"({', '.join(NODE_NAMES)})")
    print("-" * 72)

    serve(
        entities=entities,
        host=Config.DEVUI_HOST,
        port=Config.DEVUI_PORT,
        auto_open=Config.DEVUI_AUTO_OPEN,
        instrumentation_enabled=True,
        auth_enabled=not Config.DEVUI_NO_AUTH,
    )


if __name__ == "__main__":
    main()
