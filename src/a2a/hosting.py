"""A2A hosting helpers.

Wraps an ``agent_framework`` Agent as an isolated A2A-protocol HTTP server so the
node can be reached by other agents over the network. Uses the version-correct
hosting pattern for the installed ``a2a`` SDK (Starlette + JSON-RPC routes).
"""
import sys
import pathlib

# Ensure project root is in sys.path
project_root = str(pathlib.Path(__file__).resolve().parents[2])
if project_root not in sys.path:
    sys.path.insert(0, project_root)

from typing import List

import uvicorn
from starlette.applications import Starlette
from starlette.middleware import Middleware
from starlette.middleware.base import BaseHTTPMiddleware
from a2a.server.request_handlers import DefaultRequestHandler
from a2a.server.routes import create_jsonrpc_routes, create_agent_card_routes
from a2a.server.tasks import InMemoryTaskStore
from a2a.types import AgentCapabilities, AgentCard, AgentInterface, AgentSkill
from agent_framework.a2a import A2AExecutor

from src.config import Config


class TraceContextMiddleware(BaseHTTPMiddleware):
    """Continues the caller's distributed trace on inbound A2A requests.

    Extracts W3C ``traceparent`` / ``tracestate`` (injected by the A2A client in
    ``src/a2a/clients.py``) from the request headers and attaches the resulting
    OpenTelemetry context for the duration of the request. This makes every span
    the node emits (``invoke_agent``, ``chat``, ``execute_tool``) a child of the
    orchestrator's span, yielding one coherent end-to-end distributed trace.

    Safe no-op when OpenTelemetry is not installed/configured.
    """

    async def dispatch(self, request, call_next):
        token = None
        ctx = None
        try:
            from opentelemetry import context as otel_context
            from opentelemetry.propagate import extract

            ctx = extract(dict(request.headers))
            token = otel_context.attach(ctx)
        except Exception:
            token = None
        try:
            return await call_next(request)
        finally:
            if token is not None:
                try:
                    from opentelemetry import context as otel_context
                    otel_context.detach(token)
                except Exception:
                    pass


def build_agent_card(
    name: str,
    description: str,
    port: int,
    skills: List[AgentSkill] | None = None,
) -> AgentCard:
    """Builds the public AgentCard advertised by an A2A server node."""
    url = f"http://{Config.A2A_HOST}:{port}/"
    return AgentCard(
        name=name,
        description=description,
        version="1.0.0",
        default_input_modes=["text"],
        default_output_modes=["text"],
        capabilities=AgentCapabilities(streaming=True),
        supported_interfaces=[
            AgentInterface(url=url, protocol_binding="JSONRPC"),
        ],
        skills=skills or [],
    )


def build_starlette_app(agent, card: AgentCard) -> Starlette:
    """Wraps an agent_framework Agent into a Starlette A2A application.

    Installs ``TraceContextMiddleware`` so inbound A2A calls continue the
    caller's distributed trace.
    """
    request_handler = DefaultRequestHandler(
        agent_executor=A2AExecutor(agent),
        task_store=InMemoryTaskStore(),
        agent_card=card,
    )
    return Starlette(
        middleware=[Middleware(TraceContextMiddleware)],
        routes=[
            *create_agent_card_routes(card),
            *create_jsonrpc_routes(request_handler, "/"),
        ],
    )


def serve(agent, card: AgentCard, port: int) -> None:
    """Blocks serving the agent as an A2A HTTP server on the given port."""
    app = build_starlette_app(agent, card)
    uvicorn.run(app, host=Config.A2A_HOST, port=port, log_level="warning")
