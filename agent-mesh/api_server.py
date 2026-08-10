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
import json
import os
import pathlib
import sys
import time
import uuid

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
from starlette.responses import JSONResponse, StreamingResponse
from starlette.routing import Route

from src.a2a.hosting import TraceContextMiddleware
from src.auth.identity_provider import login, list_users
from src.config import Config
from src.feedback.store import record_feedback, record_structured_feedback, get_structured_feedback_list
from src.mesh.orchestrator import handle_request, handle_request_stream
from src.hitl.approval_store import approval_store, CHECKPOINT_DIR
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

    Body: {"username": str, "query": str, "session_id"?: str, "bypass_cache"?: bool}
    Response: MeshResult JSON — answer, blocked, block_stage, trail, session_id,
    plus full execution summary and event stream for the UI transparency panel.
    """
    try:
        body = await request.json()
        username = str(body.get("username", "bob")).strip() or "bob"
        query = str(body.get("query", "")).strip()
        session_id = str(body.get("session_id", "")).strip() or None
        bypass_cache = bool(body.get("bypass_cache", False))

    except Exception:
        return JSONResponse(
            {"error": "Invalid JSON body. Expected {username, query}."},
            status_code=400,
        )

    if not query:
        return JSONResponse({"error": "query must not be empty."}, status_code=400)

    user = login(username)

    shared_request_id = uuid.uuid4().hex[:8].upper()
    tracer = ExecutionTracer(user=user.username, query=query, request_id=shared_request_id)
    token = set_active_tracer(tracer)
    try:
        result = await handle_request(user, query, session_id, request_id=shared_request_id, bypass_cache=bypass_cache)
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
        # Captured LLM reasoning entries for the AI Reasoning explainability panel.
        # On a cache hit the tracer has no live reasoning — replay from stored entries.
        "llm_reasoning": summary.llm_reasoning or result.cache_reasoning,
        # Semantic cache provenance — visible in the UI
        "cache_hit": result.cache_hit,
        "cache_age_hours": result.cache_age_hours,
        "cache_similarity": result.cache_similarity,
        "cache_reasoning": result.cache_reasoning,
        "cache_judge_invoked": result.cache_judge_invoked,
        "cache_judge_decision": result.cache_judge_decision,
        "cache_judge_reason": result.cache_judge_reason,
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

    Path: GET /api/conversations/{session_id}?username=<user>
    The optional ``username`` query param enforces session ownership: if the session
    was created by a different user, 403 is returned. Sessions created before ownership
    tracking was introduced are accessible to all users (backward-compatible).

    Response: {"session_id": str, "messages": [{"role", "content", "ts"}, ...]}
    """
    session_id = request.path_params.get("session_id", "").strip()
    if not session_id:
        return JSONResponse({"error": "session_id is required."}, status_code=400)

    requesting_user = request.query_params.get("username", "").strip()
    store = ConversationStore()
    if requesting_user and not store.check_owner(session_id, requesting_user):
        _log.warning(
            "conversation access denied session=%s requesting_user=%s",
            session_id, requesting_user,
        )
        return JSONResponse(
            {"error": "Access denied: this conversation belongs to a different user."},
            status_code=403,
        )

    try:
        messages = store.load_messages(session_id)
    except Exception as exc:
        _log.warning("conversation load failed session=%s: %s", session_id, exc)
        messages = []
    return JSONResponse({"session_id": session_id, "messages": messages})


async def post_feedback(request: Request) -> JSONResponse:
    """Record user thumbs-up/down feedback on an assistant response.

    Body: {request_id, session_id, user, role, rating ("up"|"down"),
           query, answer, route?, blocked?, comment?}
    Response: {success: true, feedback_id}
    """
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": "Invalid JSON body."}, status_code=400)

    required = {"request_id", "session_id", "user", "role", "rating", "query", "answer"}
    if missing := required - body.keys():
        return JSONResponse({"error": f"Missing required fields: {sorted(missing)}"}, status_code=400)
    if body["rating"] not in ("up", "down"):
        return JSONResponse({"error": "rating must be 'up' or 'down'."}, status_code=400)

    try:
        feedback_id = record_feedback(
            request_id=str(body["request_id"]),
            session_id=str(body["session_id"]),
            user=str(body["user"]),
            role=str(body["role"]),
            rating=str(body["rating"]),
            query=str(body["query"]),
            answer=str(body["answer"]),
            route=body.get("route"),
            blocked=bool(body.get("blocked", False)),
            comment=str(body.get("comment", "")),
        )
    except Exception as exc:
        _log.warning("feedback write failed: %s", exc, extra={"status": "ERROR"})
        return JSONResponse({"error": "Failed to save feedback."}, status_code=500)

    _log.info(
        "Feedback recorded id=%s user=%s rating=%s",
        feedback_id, body["user"], body["rating"],
        extra={"status": "SUCCESS"},
    )
    return JSONResponse({"success": True, "feedback_id": feedback_id})


async def post_structured_feedback(request: Request) -> JSONResponse:
    """Record structured 7-dimension feedback. POST /api/feedback/structured

    Body: { request_id, session_id, user, dimensions: { <key>: { rating?, codes?, note? }, ... } }
    Response: { success: true, structured_feedback_id }
    """
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": "Invalid JSON body."}, status_code=400)

    required = {"request_id", "session_id", "user", "dimensions"}
    if missing := required - body.keys():
        return JSONResponse({"error": f"Missing required fields: {sorted(missing)}"}, status_code=400)

    dimensions = body.get("dimensions")
    if not isinstance(dimensions, dict) or not dimensions:
        return JSONResponse({"error": "dimensions must be a non-empty object."}, status_code=400)

    _VALID_DIMENSION_KEYS = {"intent", "workflow", "tools", "policy", "output", "correction", "effort"}
    unknown = set(dimensions.keys()) - _VALID_DIMENSION_KEYS
    if unknown:
        return JSONResponse({"error": f"Unknown dimension keys: {sorted(unknown)}"}, status_code=400)

    _VALID_RATINGS = {"good", "partial", "poor"}
    for dim_key, dim_val in dimensions.items():
        if not isinstance(dim_val, dict):
            return JSONResponse({"error": f"Dimension '{dim_key}' must be an object."}, status_code=400)
        rating = dim_val.get("rating")
        if rating is not None and rating not in _VALID_RATINGS:
            return JSONResponse(
                {"error": f"Dimension '{dim_key}' has invalid rating '{rating}'."},
                status_code=400,
            )

    try:
        sfb_id = record_structured_feedback(
            request_id=str(body["request_id"]),
            session_id=str(body["session_id"]),
            user=str(body["user"]),
            dimensions=dimensions,
            role=body.get("role"),
            rating=body.get("rating"),
            comment=body.get("comment"),
            query=body.get("query"),
            answer=body.get("answer"),
            route=body.get("route"),
            blocked=body.get("blocked"),
        )
    except Exception as exc:
        _log.warning("structured feedback write failed: %s", exc, extra={"status": "ERROR"})
        return JSONResponse({"error": "Failed to save structured feedback."}, status_code=500)

    _log.info(
        "Structured feedback recorded id=%s user=%s dims=%s",
        sfb_id, body["user"], list(dimensions.keys()),
        extra={"status": "SUCCESS"},
    )
    return JSONResponse({"success": True, "structured_feedback_id": sfb_id})


async def get_feedback_stats(request: Request) -> JSONResponse:
    """Return aggregate feedback counts (total, up, down, with_comment)."""
    path = Config.FEEDBACK_LOG_FILE
    if not os.path.exists(path):
        return JSONResponse({"total": 0, "up": 0, "down": 0, "with_comment": 0})
    up = down = commented = 0
    try:
        with open(path, encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                    if rec.get("record_type") == "structured":
                        continue
                    if rec.get("rating") == "up":
                        up += 1
                    elif rec.get("rating") == "down":
                        down += 1
                    if rec.get("comment"):
                        commented += 1
                except Exception:
                    pass
    except Exception as exc:
        _log.warning("feedback stats read failed: %s", exc)
    return JSONResponse({"total": up + down, "up": up, "down": down, "with_comment": commented})


async def get_structured_feedback_list_handler(request: Request) -> JSONResponse:
    """Return all structured feedback records, newest-first."""
    records = get_structured_feedback_list()
    return JSONResponse({"records": records, "total": len(records)})


async def get_logs_list(request: Request) -> JSONResponse:
    """Return structured log data from agent_mesh.log grouped by request_id.

    Groups entries by request_id to expose the parent-child (request → pipeline stage → log line)
    hierarchy. Entries with request_id == "-" (system startup noise) are returned separately.
    """
    path = Config.LOG_FILE
    # Collect the active log file plus all rotated backups (.1 … .LOG_BACKUP_COUNT)
    log_files = []
    if os.path.exists(path):
        log_files.append(path)
    for i in range(1, Config.LOG_BACKUP_COUNT + 1):
        rotated = f"{path}.{i}"
        if os.path.exists(rotated):
            log_files.append(rotated)

    if not log_files:
        return JSONResponse({
            "groups": [], "system_entries": [],
            "total_entries": 0, "unique_requests": 0,
            "error_count": 0, "warning_count": 0, "loggers": [],
        })

    entries = []
    for log_path in log_files:
        try:
            with open(log_path, encoding="utf-8") as fh:
                for line in fh:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        rec = json.loads(line)
                        if isinstance(rec, dict):
                            entries.append(rec)
                    except Exception:
                        pass
        except Exception as exc:
            _log.warning("logs list read failed (%s): %s", log_path, exc)

    total = len(entries)
    error_count = sum(1 for e in entries if e.get("level") == "ERROR")
    warning_count = sum(1 for e in entries if e.get("level") == "WARNING")
    loggers = sorted(set(e.get("logger", "") for e in entries if e.get("logger")))

    # Separate system/startup entries (no request context) from request-bound entries
    system_entries = [e for e in entries if e.get("request_id", "-") == "-"]
    request_entries = [e for e in entries if e.get("request_id", "-") != "-"]

    # Build an audit token index: audit_index[request_id][agent_name] = token counts.
    # Used below to inject per-step token data into mesh.agent log entries.
    # For records written before token tracking was added (no token fields), we
    # back-fill estimates from prompt/response character lengths (~4 chars/token).
    audit_index: dict[str, dict[str, dict]] = {}
    audit_path = Config.AUDIT_LOG_FILE
    if os.path.exists(audit_path):
        try:
            with open(audit_path, encoding="utf-8") as fh:
                for line in fh:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        rec = json.loads(line)
                        rid_a  = str(rec.get("request_id", "-")).upper()
                        agent  = rec.get("agent_name", "")
                        if rid_a == "-" or not agent:
                            continue
                        it        = int(rec.get("input_tokens",  0) or 0)
                        ot        = int(rec.get("output_tokens", 0) or 0)
                        estimated = bool(rec.get("tokens_estimated", False))
                        # Back-fill from text length for old records with no token data
                        if it == 0 and ot == 0:
                            inputs_text = " ".join(rec.get("inputs") or [])
                            output_text = rec.get("output", "") or ""
                            it  = max(1, len(inputs_text) // 4) if inputs_text  else 0
                            ot  = max(1, len(output_text)  // 4) if output_text  else 0
                            estimated = True
                        audit_index.setdefault(rid_a, {})[agent] = {
                            "input_tokens":     it,
                            "output_tokens":    ot,
                            "total_tokens":     it + ot,
                            "tokens_estimated": estimated,
                        }
                    except Exception:
                        pass
        except Exception:
            pass

    # Group by request_id; preserve insertion order (entries arrive chronologically)
    groups_map: dict[str, list] = {}
    for e in request_entries:
        rid = e["request_id"]
        groups_map.setdefault(rid, []).append(e)

    groups = []
    for rid, ents in groups_map.items():
        ents_sorted = sorted(ents, key=lambda x: x.get("ts", ""))
        first_ts = ents_sorted[0].get("ts", "")
        last_ts = ents_sorted[-1].get("ts", "")
        # Compute duration in ms from ISO timestamps
        try:
            from datetime import datetime, timezone
            t0 = datetime.fromisoformat(first_ts)
            t1 = datetime.fromisoformat(last_ts)
            duration_ms = max(0, int((t1 - t0).total_seconds() * 1000))
        except Exception:
            duration_ms = 0
        user = next((e.get("user") for e in ents_sorted if e.get("user")), None)
        session_id = next((e.get("session_id") for e in ents_sorted if e.get("session_id")), None)

        # Inject per-step token data onto mesh.agent entries using the audit index.
        rid_upper = rid.upper()
        for entry in ents_sorted:
            agent_name = entry.get("agent", "")
            if agent_name and rid_upper in audit_index and agent_name in audit_index[rid_upper]:
                tok = audit_index[rid_upper][agent_name]
                entry["input_tokens"]     = tok["input_tokens"]
                entry["output_tokens"]    = tok["output_tokens"]
                entry["total_tokens"]     = tok["total_tokens"]
                entry["tokens_estimated"] = tok["tokens_estimated"]

        # Request-level token totals (sum across all agents in this request).
        tok_map = audit_index.get(rid_upper, {})
        token_input    = sum(v["input_tokens"]  for v in tok_map.values())
        token_output   = sum(v["output_tokens"] for v in tok_map.values())
        token_total    = token_input + token_output
        token_estimated = any(v.get("tokens_estimated") for v in tok_map.values())

        groups.append({
            "request_id":      rid,
            "trace_id":        ents_sorted[0].get("trace_id", ""),
            "user":            user,
            "session_id":      session_id,
            "first_ts":        first_ts,
            "last_ts":         last_ts,
            "duration_ms":     duration_ms,
            "entry_count":     len(ents_sorted),
            "has_error":       any(e.get("level") == "ERROR"   for e in ents_sorted),
            "has_warning":     any(e.get("level") == "WARNING" for e in ents_sorted),
            "token_input":     token_input,
            "token_output":    token_output,
            "token_total":     token_total,
            "token_estimated": token_estimated,
            "entries":         ents_sorted,
        })
    # Sort groups newest-first
    groups.sort(key=lambda g: g["first_ts"], reverse=True)

    return JSONResponse({
        "groups": groups,
        "system_entries": system_entries,
        "total_entries": total,
        "unique_requests": len(groups),
        "error_count": error_count,
        "warning_count": warning_count,
        "loggers": loggers,
    })


async def get_audit_list(request: Request) -> JSONResponse:
    """Return all audit trail records newest-first with aggregate stats.

    Strips bulky inputs/output fields — returns previews only.
    Use GET /api/audit/{request_id} for the full record.
    """
    path = Config.AUDIT_LOG_FILE
    if not os.path.exists(path):
        return JSONResponse({"records": [], "total": 0, "success_count": 0, "error_count": 0, "avg_latency_ms": 0})
    records = []
    success = error = total_latency = 0
    try:
        with open(path, encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                    latency = rec.get("latency_ms", 0) or 0
                    status = rec.get("status", "UNKNOWN")
                    if status == "SUCCESS":
                        success += 1
                    else:
                        error += 1
                    total_latency += latency
                    # Build a slimmed record with previews instead of full inputs/output
                    inputs = rec.get("inputs") or []
                    input_preview = (inputs[0][:200] if inputs else "")
                    output_preview = (rec.get("output", "") or "")[:200]
                    slim = {k: v for k, v in rec.items() if k not in ("inputs", "output")}
                    slim["input_preview"] = input_preview
                    slim["output_preview"] = output_preview
                    records.append(slim)
                except Exception:
                    pass
    except Exception as exc:
        _log.warning("audit list read failed: %s", exc)
    records.reverse()
    total = success + error
    return JSONResponse({
        "records": records,
        "total": total,
        "success_count": success,
        "error_count": error,
        "avg_latency_ms": round(total_latency / total) if total else 0,
    })


async def get_audit_detail(request: Request) -> JSONResponse:
    """Return full inputs + output for a single audit record by request_id."""
    request_id = request.path_params.get("request_id", "").strip().upper()
    if not request_id:
        return JSONResponse({"error": "request_id is required."}, status_code=400)
    path = Config.AUDIT_LOG_FILE
    if not os.path.exists(path):
        return JSONResponse({"error": "Audit log not found."}, status_code=404)
    try:
        with open(path, encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                    if str(rec.get("request_id", "")).upper() == request_id:
                        return JSONResponse(rec)
                except Exception:
                    pass
    except Exception as exc:
        _log.warning("audit detail read failed: %s", exc)
    return JSONResponse({"error": f"Record {request_id} not found."}, status_code=404)


async def get_trace_list(request: Request) -> JSONResponse:
    """Return all trace span records newest-first with aggregate stats.

    Handles the known formatting issue where multiple JSON objects may appear
    on a single line by using raw_decode() to extract them iteratively.
    """
    path = Config.TRACE_LOG_FILE
    if not os.path.exists(path):
        return JSONResponse({"records": [], "total": 0, "success_count": 0, "avg_duration_ms": 0, "max_duration_ms": 0})
    records = []
    success = total_dur = max_dur = 0
    decoder = json.JSONDecoder()
    try:
        with open(path, encoding="utf-8") as fh:
            content = fh.read()
        idx = 0
        while idx < len(content):
            # Skip whitespace / newlines
            while idx < len(content) and content[idx] in " \t\r\n":
                idx += 1
            if idx >= len(content):
                break
            try:
                rec, length = decoder.raw_decode(content, idx)
                idx += length
                dur = rec.get("duration_ms", 0) or 0
                if rec.get("status") == "SUCCESS":
                    success += 1
                total_dur += dur
                if dur > max_dur:
                    max_dur = dur
                records.append(rec)
            except Exception:
                idx += 1
    except Exception as exc:
        _log.warning("trace list read failed: %s", exc)
    records.reverse()
    total = len(records)
    return JSONResponse({
        "records": records,
        "total": total,
        "success_count": success,
        "avg_duration_ms": round(total_dur / total) if total else 0,
        "max_duration_ms": max_dur,
    })


async def get_conversations_list(request: Request) -> JSONResponse:
    """Return all conversation sessions with full message history."""
    store_dir = pathlib.Path(Config.CONVERSATION_STORE_DIR)
    if not store_dir.exists():
        return JSONResponse({"sessions": [], "total_sessions": 0, "total_messages": 0, "unique_users": 0})
    sessions = []
    total_messages = 0
    users: set[str] = set()
    try:
        for jf in sorted(store_dir.glob("*.jsonl"), key=lambda p: p.stat().st_mtime, reverse=True):
            session_id = jf.stem  # filename without .jsonl
            # Infer user from session_id prefix (e.g. "alice_37ce2a8d" → "alice")
            user = session_id.rsplit("_", 1)[0] if "_" in session_id else session_id
            messages = []
            try:
                with open(jf, encoding="utf-8") as fh:
                    for line in fh:
                        line = line.strip()
                        if not line:
                            continue
                        try:
                            rec = json.loads(line)
                            if not isinstance(rec, dict) or rec.get("role") not in ("user", "assistant"):
                                continue
                            messages.append(rec)
                        except Exception:
                            pass
            except Exception:
                pass
            if not messages:
                continue
            users.add(user)
            total_messages += len(messages)
            first_ts = messages[0].get("ts", "")
            last_ts = messages[-1].get("ts", "")
            first_query = next(
                (m.get("content", "")[:200] for m in messages if m.get("role") == "user"),
                "",
            )
            sessions.append({
                "session_id": session_id,
                "user": user,
                "message_count": len(messages),
                "first_ts": first_ts,
                "last_ts": last_ts,
                "first_query": first_query,
                "messages": messages,
            })
    except Exception as exc:
        _log.warning("conversations list failed: %s", exc)
    return JSONResponse({
        "sessions": sessions,
        "total_sessions": len(sessions),
        "total_messages": total_messages,
        "unique_users": len(users),
    })


async def get_feedback_list(request: Request):
    """Return all feedback records sorted newest-first, with aggregate counts."""
    path = Config.FEEDBACK_LOG_FILE
    if not os.path.exists(path):
        return JSONResponse({"records": [], "total": 0, "up": 0, "down": 0, "with_comment": 0})
    records = []
    up = down = commented = 0
    try:
        with open(path, encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                    if rec.get("record_type") == "structured":
                        continue
                    # Exclude the bulky fine_tune_record from the list response
                    rec.pop("fine_tune_record", None)
                    records.append(rec)
                    if rec.get("rating") == "up":
                        up += 1
                    elif rec.get("rating") == "down":
                        down += 1
                    if rec.get("comment"):
                        commented += 1
                except Exception:
                    pass
    except Exception as exc:
        _log.warning("feedback list read failed: %s", exc)
    records.reverse()  # newest first
    return JSONResponse({"records": records, "total": len(records), "up": up, "down": down, "with_comment": commented})


# ---------------------------------------------------------------------------
# App assembly
# ---------------------------------------------------------------------------

async def get_cache_stats(request: Request) -> JSONResponse:
    """Return semantic cache statistics.

    Response: {enabled, total_entries, similarity_threshold, max_age_hours,
               embed_model, chroma_dir, collection_name}
    """
    if not Config.ENABLE_RESPONSE_CACHE:
        return JSONResponse({
            "enabled": False,
            "total_entries": 0,
            "similarity_threshold": Config.CACHE_SIMILARITY_THRESHOLD,
            "max_age_hours": Config.CACHE_MAX_AGE_HOURS,
            "embed_model": Config.CACHE_EMBED_MODEL,
            "chroma_dir": Config.CACHE_CHROMA_DIR,
            "collection_name": Config.CACHE_COLLECTION_NAME,
            # Lifecycle counters (always present so dashboards don't break when disabled)
            "entity_gate_drops": 0,
            "reranker_invocations": 0,
            "hit_accepted": 0,
            "hit_rejected": 0,
            "intent_accepted": 0,
            "intent_rejected": 0,
            "reranker_enabled": Config.CACHE_RERANKER_ENABLED,
            "entity_gating_enabled": Config.CACHE_ENTITY_GATING_ENABLED,
        })
    try:
        from src.cache import get_cache_store
        stats = get_cache_store().stats()
        return JSONResponse(stats)
    except Exception as exc:
        _log.warning("cache stats error: %s", exc)
        return JSONResponse({"enabled": True, "error": str(exc)}, status_code=500)


@contextlib.asynccontextmanager
async def _lifespan(app):
    """Startup indexer + graceful shutdown OTel flush."""
    # Pre-populate the semantic cache from existing conversation JSONL files.
    # Runs in a background thread (asyncio.to_thread) so it never blocks startup.
    if Config.ENABLE_RESPONSE_CACHE:
        try:
            from src.cache.cache_indexer import index_conversations_async
            asyncio.create_task(index_conversations_async())
        except Exception as exc:
            _log.warning("cache indexer startup failed: %s", exc)
    # UC-5: Re-hydrate any HITL approvals that were pending when the server last shut down.
    # save_checkpoint() writes MeshState to data/checkpoints/{aid}.json before yielding;
    # this restore loop ensures those approvals survive restarts (no resubmission needed).
    if CHECKPOINT_DIR.exists():
        restored = 0
        for cp_file in CHECKPOINT_DIR.glob("*.json"):
            aid = cp_file.stem
            state = approval_store.load_checkpoint(aid)
            if state:
                approval_store.restore(aid, state)
                restored += 1
                _log.info("UC-5: Restored pending HITL approval %s from checkpoint", aid,
                          extra={"status": "HITL_RESTORED"})
        if restored:
            _log.info("UC-5: Restored %d pending HITL approval(s) from data/checkpoints/", restored)
    yield
    _log.info("api_server shutting down — flushing observability exporters.")
    flush_observability()


_API_SERVER_HOST = os.getenv("API_SERVER_HOST", "127.0.0.1")
_API_SERVER_PORT = int(os.getenv("API_SERVER_PORT", "8000"))

async def post_query_stream(request: Request) -> StreamingResponse:
    """SSE endpoint — streams one event per pipeline stage as it completes.

    Body: same as POST /api/query — {username, query, session_id?}

    SSE event types emitted in order:
      event: stage   data: {"stage":str, "status":"started"|"completed"|"blocked", "message":str}
      event: result  data: <full MeshResult JSON — same shape as POST /api/query response>
      event: done    data: {}
      event: error   data: {"message": str}
    """
    try:
        body = await request.json()
        username = str(body.get("username", "bob")).strip() or "bob"
        query = str(body.get("query", "")).strip()
        session_id = str(body.get("session_id", "")).strip() or None
        bypass_cache = bool(body.get("bypass_cache", False))
    except Exception:
        async def _err_body():
            yield 'event: error\ndata: {"message": "Invalid JSON body"}\n\n'
        return StreamingResponse(_err_body(), media_type="text/event-stream")

    if not query:
        async def _err_empty():
            yield 'event: error\ndata: {"message": "query must not be empty"}\n\n'
        return StreamingResponse(_err_empty(), media_type="text/event-stream")

    user = login(username)
    shared_request_id = uuid.uuid4().hex[:8].upper()
    event_queue: asyncio.Queue = asyncio.Queue(maxsize=64)

    async def _sse_generator():
        # Create tracer, set ContextVar, and fire the pipeline task all inside the
        # generator so the token is created and reset in the same async context.
        # ensure_future snapshots the context after set_active_tracer, so the
        # pipeline task still inherits _active_tracer correctly.
        tracer = ExecutionTracer(user=user.username, query=query, request_id=shared_request_id)
        token = set_active_tracer(tracer)
        pipeline_task = asyncio.ensure_future(
            handle_request_stream(user, query, session_id, request_id=shared_request_id, event_queue=event_queue, bypass_cache=bypass_cache)
        )
        try:
            while True:
                item = await event_queue.get()
                if item is None:
                    # Sentinel — pipeline finished. Await task for the MeshResult.
                    try:
                        result = await pipeline_task
                    except Exception as exc:
                        _log.exception("stream pipeline error: %s", exc)
                        yield f"event: error\ndata: {json.dumps({'message': str(exc)})}\n\n"
                        yield "event: done\ndata: {}\n\n"
                        return
                    summary = tracer.summary()
                    result_payload = {
                        "answer": result.answer,
                        "blocked": result.blocked,
                        "block_stage": result.block_stage,
                        "trail": result.trail,
                        "session_id": result.session_id,
                        "request_id": summary.request_id,
                        "domain": summary.domain,
                        "route": summary.route,
                        "execution_path": summary.execution_path,
                        "agents_invoked": summary.agents_invoked,
                        "tools_used": summary.tools_used,
                        "total_duration_ms": summary.total_duration_ms,
                        "confidence": summary.confidence,
                        "events": [dataclasses.asdict(e) for e in summary.events],
                        "llm_reasoning": summary.llm_reasoning or result.cache_reasoning,
                        "cache_hit": result.cache_hit,
                        "cache_age_hours": result.cache_age_hours,
                        "cache_similarity": result.cache_similarity,
                        "cache_reasoning": result.cache_reasoning,
                        "cache_judge_invoked": result.cache_judge_invoked,
                        "cache_judge_decision": result.cache_judge_decision,
                        "cache_judge_reason": result.cache_judge_reason,
                    }
                    yield f"event: result\ndata: {json.dumps(result_payload)}\n\n"
                    yield "event: done\ndata: {}\n\n"
                    break
                event_type = item.get("event_type", "stage")
                if event_type == "reasoning":
                    yield f"event: reasoning\ndata: {json.dumps({'entries': item['entries']})}\n\n"
                elif event_type == "hitl":
                    yield f"event: hitl\ndata: {json.dumps({'approval_id': item['approval_id'], 'details': item['details']})}\n\n"
                elif event_type == "intent_suggestion":
                    yield f"event: intent_suggestion\ndata: {json.dumps(item)}\n\n"
                elif event_type == "intent_suggestion_judge":
                    yield f"event: intent_suggestion_judge\ndata: {json.dumps(item)}\n\n"
                elif event_type == "cache_context":
                    yield f"event: cache_context\ndata: {json.dumps(item)}\n\n"
                else:
                    yield f"event: stage\ndata: {json.dumps(item)}\n\n"
        finally:
            clear_active_tracer(token)

    return StreamingResponse(
        _sse_generator(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


_CORS_ORIGINS = [
    "http://localhost:5173",   # Vite dev server
    "http://127.0.0.1:5173",
    "http://localhost:4173",   # Vite preview
    "http://127.0.0.1:4173",
]

async def get_approvals_list(request: Request) -> JSONResponse:
    """List all pending approvals. GET /api/approvals"""
    return JSONResponse(approval_store.get_pending())


async def get_approval(request: Request) -> JSONResponse:
    """Fetch approval details for the standalone approval page. GET /api/approvals/{id}"""
    aid = request.path_params.get("id", "").strip().upper()
    details = approval_store.get(aid)
    if details is None:
        return JSONResponse({"error": f"Approval '{aid}' not found or already resolved."}, status_code=404)
    return JSONResponse(details)


async def post_approve(request: Request) -> JSONResponse:
    """Approve a pending HITL request. POST /api/approvals/{id}/approve"""
    aid = request.path_params.get("id", "").strip().upper()
    ok = approval_store.approve(aid)   # signals asyncio.Event
    if not ok:
        return JSONResponse({"error": f"Approval '{aid}' not found or already resolved."}, status_code=404)
    _log.info("HITL approved id=%s", aid, extra={"status": "HITL_APPROVED"})
    # UC-5 cold-path: yield to event loop — if a live coroutine was waiting in
    # handle_request_stream it will consume the event now and clear _live_waiters.
    await asyncio.sleep(0)
    if not approval_store.is_live(aid):
        # No live coroutine consumed the event → server was restarted while this
        # approval was pending. Load the checkpoint and resume the pipeline directly.
        state = approval_store.load_checkpoint(aid)
        if state:
            _log.info("UC-5 cold-path resume for approval %s", aid,
                      extra={"status": "HITL_COLD_RESUME"})
            from src.mesh.workflow import build_hitl_resume_workflow
            from src.a2a.clients import ask_remote
            try:
                resume_wf = build_hitl_resume_workflow(ask=ask_remote)
                resume_events = await resume_wf.run(state)
                # Extract final MeshState (same pattern as orchestrator._final_state)
                result_state = None
                try:
                    for out in reversed(resume_events.get_outputs()):
                        if hasattr(out, "answer"):
                            result_state = out
                            break
                except Exception:
                    pass
                answer = result_state.answer if result_state else (
                    "Your request has been approved and processed."
                )
                # Store result in conversation history so user sees it on reconnect
                if state.session_id:
                    try:
                        store = ConversationStore()
                        store.append_turn(state.session_id, state.query, answer)
                    except Exception:
                        pass
            except Exception as exc:
                _log.warning("UC-5 cold-path resume failed for %s: %s", aid, exc)
                answer = "Your request was approved. Please re-open the conversation to see the result."
            approval_store.delete_checkpoint(aid)
            approval_store._pending.pop(aid, None)
            return JSONResponse({
                "success": True, "approval_id": aid, "decision": "approved",
                "resumed": True, "answer": answer,
            })
    return JSONResponse({"success": True, "approval_id": aid, "decision": "approved"})


async def post_reject(request: Request) -> JSONResponse:
    """Reject a pending HITL request. POST /api/approvals/{id}/reject"""
    aid = request.path_params.get("id", "").strip().upper()
    ok = approval_store.reject(aid)
    if not ok:
        return JSONResponse({"error": f"Approval '{aid}' not found or already resolved."}, status_code=404)
    _log.info("HITL rejected id=%s", aid, extra={"status": "HITL_REJECTED"})
    return JSONResponse({"success": True, "approval_id": aid, "decision": "rejected"})


async def post_cache_reload(request: Request) -> JSONResponse:
    """Reload ChromaDB in the running server after an external ingest. POST /api/cache/reload"""
    from src.cache.semantic_cache import get_cache_store
    try:
        store = get_cache_store()
        count = await asyncio.to_thread(store.reload)
        _log.info("cache: reload triggered via API — %d entries now visible", count)
        return JSONResponse({"status": "ok", "entries": count})
    except Exception as exc:
        _log.warning("cache reload error: %s", exc, exc_info=True)
        return JSONResponse({"status": "error", "detail": str(exc)}, status_code=500)


async def post_cache_ingest(request: Request) -> JSONResponse:
    """Trigger a background ingest job. POST /api/cache/ingest

    Body (all optional): {"source_dir"?: str, "dry_run"?: bool, "overwrite"?: bool, "role"?: str}
    Returns: {"job_id": str, "status": "running"}
    """
    try:
        body = await request.json()
    except Exception:
        body = {}
    source_dir = (body.get("source_dir") or "").strip()
    dry_run = bool(body.get("dry_run", False))
    overwrite = bool(body.get("overwrite", False))
    role_filter = (body.get("role") or "").strip() or None

    from src.cache.ingest_pipeline import IngestJob, run_ingest_job, _jobs
    job_id = uuid.uuid4().hex[:12].upper()
    _jobs[job_id] = IngestJob(job_id=job_id)

    asyncio.create_task(run_ingest_job(
        job_id,
        source_dir=source_dir,
        dry_run=dry_run,
        overwrite=overwrite,
        role_filter=role_filter,
    ))
    _log.info("cache ingest job started job_id=%s dry_run=%s", job_id, dry_run)
    return JSONResponse({"job_id": job_id, "status": "running"})


async def get_cache_ingest_job(request: Request) -> JSONResponse:
    """Poll ingest job status. GET /api/cache/ingest/{job_id}"""
    from src.cache.ingest_pipeline import get_job
    job_id = request.path_params.get("job_id", "").strip().upper()
    job = get_job(job_id)
    if job is None:
        return JSONResponse({"error": f"Job '{job_id}' not found."}, status_code=404)
    payload: dict = {"job_id": job.job_id, "status": job.status}
    if job.report is not None:
        payload["report"] = job.report.as_dict()
    if job.error:
        payload["error"] = job.error
    if job.finished_at:
        payload["elapsed_s"] = round(job.finished_at - job.started_at, 1)
    return JSONResponse(payload)


async def post_intent_decision(request: Request) -> JSONResponse:
    """Resolve a pending intent-match suggestion. POST /api/cache/intent-decision

    Body: {"entry_id": str, "accepted": bool, "chosen_entry_id": str (optional)}
    entry_id is the primary (top-1) candidate that keys the decision store.
    chosen_entry_id is which specific candidate the user clicked (may differ from entry_id).
    Called by the frontend when the user clicks "Use this answer" or "Run fresh".
    """
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": "Invalid JSON body."}, status_code=400)
    entry_id = (body.get("entry_id") or "").strip()
    accepted = bool(body.get("accepted", False))
    chosen_entry_id = (body.get("chosen_entry_id") or entry_id).strip() or entry_id
    if not entry_id:
        return JSONResponse({"error": "entry_id is required."}, status_code=400)
    from src.cache.intent_decision_store import intent_decision_store
    ok = intent_decision_store.resolve(entry_id, accepted, chosen_entry_id)
    decision_label = "accepted" if accepted else "rejected"
    _log.info(
        "Intent decision entry_id=%s chosen=%s decision=%s found=%s",
        entry_id, chosen_entry_id, decision_label, ok,
        extra={"status": "INTENT_DECISION"},
    )
    return JSONResponse({"ok": True, "entry_id": entry_id, "chosen_entry_id": chosen_entry_id, "accepted": accepted})


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
        Route("/health",               health,              methods=["GET"]),
        Route("/api/users",            get_users,           methods=["GET"]),
        Route("/api/login",            post_login,          methods=["POST"]),
        Route("/api/query",            post_query,          methods=["POST"]),
        Route("/api/query/stream",     post_query_stream,   methods=["POST"]),
        Route("/api/logs",                       get_logs_list,            methods=["GET"]),
        Route("/api/audit",                      get_audit_list,           methods=["GET"]),
        Route("/api/audit/{request_id}",         get_audit_detail,         methods=["GET"]),
        Route("/api/traces",                     get_trace_list,           methods=["GET"]),
        Route("/api/conversations/list",         get_conversations_list,   methods=["GET"]),
        Route("/api/feedback",                   post_feedback,              methods=["POST"]),
        Route("/api/feedback/structured",        post_structured_feedback,              methods=["POST"]),
        Route("/api/feedback/structured/list",   get_structured_feedback_list_handler,  methods=["GET"]),
        Route("/api/feedback/list",              get_feedback_list,                     methods=["GET"]),
        Route("/api/feedback/stats",             get_feedback_stats,                    methods=["GET"]),
        Route("/api/mesh/status",      get_mesh_status,     methods=["GET"]),
        Route("/api/conversations/{session_id}", get_conversation, methods=["GET"]),
        Route("/api/approvals",                  get_approvals_list, methods=["GET"]),
        Route("/api/approvals/{id}",             get_approval,     methods=["GET"]),
        Route("/api/approvals/{id}/approve",     post_approve,     methods=["POST"]),
        Route("/api/approvals/{id}/reject",      post_reject,      methods=["POST"]),
        Route("/api/cache/stats",                get_cache_stats,       methods=["GET"]),
        Route("/api/cache/intent-decision",      post_intent_decision,  methods=["POST"]),
        Route("/api/cache/reload",               post_cache_reload,     methods=["POST"]),
        Route("/api/cache/ingest",               post_cache_ingest,     methods=["POST"]),
        Route("/api/cache/ingest/{job_id}",      get_cache_ingest_job,  methods=["GET"]),
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
