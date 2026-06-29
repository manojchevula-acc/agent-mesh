"""Production HTTP API server for the Agent Mesh UI.

Wraps the mesh orchestrator in a Starlette + uvicorn HTTP server so the
React frontend (frontend/) can interact with the mesh over a standard REST
API. Uses the exact same Starlette patterns as src/a2a/hosting.py — no new
Python dependencies required (starlette + uvicorn are already in
requirements.txt).

Endpoints
---------
GET  /health              Liveness probe (mirrors A2A node /health schema).
GET  /api/users           List all demo users with roles.
POST /api/login           Body: {username} → User JSON.
POST /api/query           Body: {username, query, session_id?} → MeshResult JSON.
GET  /api/mesh/status     Fan-out GET /health to all A2A nodes → per-node status.
GET  /api/conversations/{session_id}  Stored conversation history for UI restore.

Run
---
    Ensure the mesh is already running:  python launch_mesh.py
    Then in a second terminal:           python api_server.py

    Dev:   frontend proxies /api and /health to http://localhost:8000 (vite.config.ts).
    Prod:  set API_SERVER_HOST / API_SERVER_PORT env vars as needed.
"""
from __future__ import annotations

import asyncio
import contextlib
import dataclasses
import os
import pathlib
import sys
import time

project_root = str(pathlib.Path(__file__).resolve().parent)
if project_root not in sys.path:
    sys.path.insert(0, project_root)

os.environ.setdefault("PYTHONWARNINGS", "ignore")

from src.observability import setup_observability
setup_observability(service_name="agent_mesh_api")

import httpx
import uvicorn
from starlette.applications import Starlette
from starlette.middleware import Middleware
from starlette.middleware.cors import CORSMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

from src.a2a.hosting import TraceContextMiddleware
from src.auth.identity_provider import login, list_users
from src.config import Config
from src.mesh.orchestrator import handle_request
from src.memory import ConversationStore
from src.observability import get_logger, CAT_SYSTEM, flush_observability
from src.tracing.execution_trace import ExecutionTracer, set_active_tracer, clear_active_tracer

_log = get_logger(CAT_SYSTEM)
_SERVER_START_TIME = time.time()

# ---------------------------------------------------------------------------
# Route handlers
# ---------------------------------------------------------------------------

async def health(request: Request) -> JSONResponse:
    """Liveness probe. Same JSON schema as the A2A node /health endpoints."""
    return JSONResponse({
        "status": "ok",
        "node": "api_server",
        "uptime_seconds": round(time.time() - _SERVER_START_TIME, 1),
        "model": Config.GROQ_MODEL,
        "service": "agent_mesh_api",
    })


async def get_users(request: Request) -> JSONResponse:
    """Return all demo users with their roles."""
    users = list_users()
    return JSONResponse([
        {
            "username": u.username,
            "display_name": u.display_name,
            "role": u.role.value,
        }
        for u in users
    ])


async def post_login(request: Request) -> JSONResponse:
    """Resolve a username to a User object. Unknown names default to employee."""
    try:
        body = await request.json()
        username = str(body.get("username", "")).strip() or "bob"
    except Exception:
        username = "bob"

    user = login(username)
    return JSONResponse({
        "username": user.username,
        "display_name": user.display_name,
        "role": user.role.value,
    })


async def post_query(request: Request) -> JSONResponse:
    """Submit a query to the mesh and return the MeshResult.

    Body: {"username": str, "query": str, "session_id"?: str}
    Response: MeshResult JSON — answer, blocked, block_stage, trail, session_id,
    plus full execution summary and event stream for the UI transparency panel.
    """
    try:
        body = await request.json()
        username = str(body.get("username", "bob")).strip() or "bob"
        query = str(body.get("query", "")).strip()
        session_id = str(body.get("session_id", "")).strip() or None
    except Exception:
        return JSONResponse(
            {"error": "Invalid JSON body. Expected {username, query}."},
            status_code=400,
        )

    if not query:
        return JSONResponse({"error": "query must not be empty."}, status_code=400)

    user = login(username)

    tracer = ExecutionTracer(user=user.username, query=query)
    token = set_active_tracer(tracer)
    try:
        result = await handle_request(user, query, session_id)
    except Exception as exc:
        _log.exception("mesh query error: %s", exc)
        return JSONResponse(
            {"error": "Mesh query failed. Is the mesh running?", "detail": str(exc)},
            status_code=502,
        )
    finally:
        clear_active_tracer(token)

    summary = tracer.summary()
    return JSONResponse({
        "answer": result.answer,
        "blocked": result.blocked,
        "block_stage": result.block_stage,
        "trail": result.trail,
        "session_id": result.session_id,
        # Execution summary (mirrors ExecutionSummary dataclass fields)
        "request_id": summary.request_id,
        "domain": summary.domain,
        "route": summary.route,
        "execution_path": summary.execution_path,
        "agents_invoked": summary.agents_invoked,
        "tools_used": summary.tools_used,
        "total_duration_ms": summary.total_duration_ms,
        "confidence": summary.confidence,
        # Full event stream for the UI transparency panel
        "events": [dataclasses.asdict(e) for e in summary.events],
    })


async def get_mesh_status(request: Request) -> JSONResponse:
    """Fan-out GET /health to all A2A nodes and return per-node status."""
    nodes = []
    async with httpx.AsyncClient(timeout=5.0) as client:
        tasks = {
            name: asyncio.ensure_future(
                client.get(f"{Config.agent_url(name)}/health")
            )
            for name in Config.AGENT_PORTS
        }
        for name, port in Config.AGENT_PORTS.items():
            task = tasks[name]
            try:
                resp = await task
                data = resp.json()
                nodes.append({
                    "name": name,
                    "port": port,
                    "status": data.get("status", "unknown"),
                    "uptime_seconds": data.get("uptime_seconds"),
                    "model": data.get("model"),
                    "url": Config.agent_url(name),
                })
            except Exception as exc:
                nodes.append({
                    "name": name,
                    "port": port,
                    "status": "error",
                    "uptime_seconds": None,
                    "model": None,
                    "url": Config.agent_url(name),
                    "error": str(exc),
                })
    return JSONResponse(nodes)


async def get_conversation(request: Request) -> JSONResponse:
    """Return the stored message history for a session (for UI restore on reload).

    Path: GET /api/conversations/{session_id}
    Response: {"session_id": str, "messages": [{"role", "content", "ts"}, ...]}
    """
    session_id = request.path_params.get("session_id", "").strip()
    if not session_id:
        return JSONResponse({"error": "session_id is required."}, status_code=400)
    try:
        messages = ConversationStore().load_messages(session_id)
    except Exception as exc:
        _log.warning("conversation load failed session=%s: %s", session_id, exc)
        messages = []
    return JSONResponse({"session_id": session_id, "messages": messages})


# ---------------------------------------------------------------------------
# App assembly
# ---------------------------------------------------------------------------

@contextlib.asynccontextmanager
async def _lifespan(app):
    """Flush pending OTel telemetry on graceful shutdown.

    Without this, the Grafana metrics PeriodicExportingMetricReader may not
    have fired its export tick before the process exits, silently dropping
    all metrics from the current session.
    """
    yield
    _log.info("api_server shutting down — flushing observability exporters.")
    flush_observability()


_API_SERVER_HOST = os.getenv("API_SERVER_HOST", "127.0.0.1")
_API_SERVER_PORT = int(os.getenv("API_SERVER_PORT", "8000"))

_CORS_ORIGINS = [
    "http://localhost:5173",   # Vite dev server
    "http://127.0.0.1:5173",
    "http://localhost:4173",   # Vite preview
    "http://127.0.0.1:4173",
]

app = Starlette(
    lifespan=_lifespan,
    middleware=[
        Middleware(
            CORSMiddleware,
            allow_origins=_CORS_ORIGINS,
            allow_methods=["GET", "POST", "OPTIONS"],
            allow_headers=["*"],
        ),
        Middleware(TraceContextMiddleware),
    ],
    routes=[
        Route("/health",          health,          methods=["GET"]),
        Route("/api/users",       get_users,       methods=["GET"]),
        Route("/api/login",       post_login,      methods=["POST"]),
        Route("/api/query",       post_query,      methods=["POST"]),
        Route("/api/mesh/status", get_mesh_status, methods=["GET"]),
        Route("/api/conversations/{session_id}", get_conversation, methods=["GET"]),
    ],
)

# HTTP-level OTel spans (method, path, status code, latency) for Grafana Tempo.
# Requires: opentelemetry-instrumentation-starlette in requirements.txt
try:
    from opentelemetry.instrumentation.starlette import StarletteInstrumentor
    StarletteInstrumentor().instrument_app(app)
except Exception:
    pass  # package not installed — degrade gracefully, mesh still works


def main() -> None:
    Config.validate()

    ok, msg = Config.check_groq()
    if not ok:
        _log.warning("Groq not configured at startup: %s", msg)
        print(f"[api_server] WARNING: {msg}")
    else:
        print(f"[api_server] {msg}")

    profile = Config.OBS_PROFILE.lower()
    if profile == "grafana":
        obs_dest = Config.GRAFANA_OTLP_ENDPOINT or "<GRAFANA_OTLP_ENDPOINT not set>"
    elif profile == "prod":
        obs_dest = "Azure Monitor / Application Insights"
    elif profile == "off":
        obs_dest = "disabled (file logging only)"
    else:
        obs_dest = Config.OTEL_EXPORTER_OTLP_ENDPOINT or "localhost:4317"

    print("=" * 64)
    print("  AGENT MESH — REST API Server")
    print("=" * 64)
    print(f"  URL:    http://{_API_SERVER_HOST}:{_API_SERVER_PORT}")
    print(f"  CORS:   {', '.join(_CORS_ORIGINS)}")
    print("  Routes: GET /health  GET /api/users  POST /api/login")
    print("          POST /api/query  GET /api/mesh/status")
    print(f"  Observability: profile={profile} → {obs_dest}")
    print("  Note:   Ensure mesh is running first (python launch_mesh.py)")
    print("=" * 64)

    uvicorn.run(
        app,
        host=_API_SERVER_HOST,
        port=_API_SERVER_PORT,
        log_level="info",
    )


if __name__ == "__main__":
    main()
