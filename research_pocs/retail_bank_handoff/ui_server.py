"""
Retail Banking Handoff POC — FastAPI Backend
=============================================
Runs the HandoffBuilder workflow and streams structured events to the React
frontend via Server-Sent Events (SSE).

Endpoints:
  POST   /api/session                  Create session → {session_id}
  GET    /api/stream/{session_id}      SSE event stream
  POST   /api/message/{session_id}     Send user message
  POST   /api/approve/{session_id}     HITL approve / reject
  DELETE /api/session/{session_id}     Clean up session

Run:
    cd research_pocs/retail_bank_handoff
    python ui_server.py
    → API at http://localhost:8000

Then in a second terminal:
    cd research_pocs/retail_bank_handoff/frontend
    npm run dev
    → UI at http://localhost:5173
"""

import asyncio
import json
import os
import pathlib
import sys
import uuid

from dotenv import load_dotenv

_here = pathlib.Path(__file__).resolve().parent
_env_parent = _here / ".." / ".." / "agent-mesh" / ".env"
_env_local = _here / ".env"

if _env_parent.exists():
    load_dotenv(str(_env_parent), override=False)
if _env_local.exists():
    load_dotenv(str(_env_local), override=True)

sys.path.insert(0, str(_here))

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from agent_framework import Content
from agent_framework._types import AgentResponseUpdate
from agent_framework.orchestrations import HandoffAgentUserRequest
from agents.agent_factory import create_chat_client, create_agents
from workflows.handoff_workflow import build_workflow

app = FastAPI(title="Retail Bank Handoff API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── Session registry ────────────────────────────────────────────────────────────
sessions: dict[str, dict] = {}

APPROVAL_GATES = {
    "fraud_screen_transfer":  {"gate": 1, "role": "Fraud Analyst",   "label": "Gate 1 — Fraud Screen Review"},
    "authorize_large_transfer": {"gate": 2, "role": "Branch Manager", "label": "Gate 2 — Manager Authorization"},
    "freeze_account":          {"gate": 1, "role": "Fraud Manager",   "label": "Gate 1 — Account Freeze Authorization"},
}


# ── Reasoning parser ─────────────────────────────────────────────────────────────

def _parse_reasoning(text: str) -> tuple[dict | None, str]:
    """Split <<<REASONING>>>...<<<END_REASONING>>> out of agent text.

    Returns (reasoning_dict, customer_text). If no block found returns (None, text).
    """
    START = "<<<REASONING>>>"
    END = "<<<END_REASONING>>>"
    if START not in text or END not in text:
        return None, text

    r_start = text.index(START) + len(START)
    r_end = text.index(END)
    block = text[r_start:r_end].strip()
    rest = text[r_end + len(END):].strip()

    reasoning: dict[str, str] = {}
    for line in block.splitlines():
        if ":" in line:
            k, _, v = line.partition(":")
            reasoning[k.strip()] = v.strip()

    return reasoning, rest


def _sse(payload: dict) -> str:
    return f"data: {json.dumps(payload)}\n\n"


# ── Workflow runner ───────────────────────────────────────────────────────────────

async def _run_workflow_loop(session_id: str) -> None:
    """Background task: drives the MAF event loop and feeds the SSE queue."""
    sess = sessions[session_id]
    workflow = sess["workflow"]
    eq: asyncio.Queue = sess["event_queue"]
    rq: asyncio.Queue = sess["response_queue"]

    async def drain_stream(stream):
        """Consume one MAF run() stream, collect request_info events, parse all others.

        Tokens are buffered per-agent and flushed on executor_completed so the full
        <<<REASONING>>>...<<<END_REASONING>>> block can be parsed before emitting.
        """
        pending: list = []
        agent_text_buf: dict[str, str] = {}  # agent → accumulated text for current turn
        current_agent: str | None = None

        async def flush_buf(agent_name: str) -> None:
            buf = agent_text_buf.pop(agent_name, "")
            if not buf:
                return
            reasoning, customer_text = _parse_reasoning(buf)
            if reasoning:
                await eq.put({"type": "reasoning", "agent": agent_name, **reasoning})
            if customer_text:
                await eq.put({"type": "token", "agent": agent_name, "text": customer_text})

        async for event in stream:
            etype = event.type

            if etype == "executor_invoked":
                name = event.executor_id or "unknown"
                if name != current_agent:
                    if current_agent:
                        await flush_buf(current_agent)
                    current_agent = name
                    await eq.put({"type": "agent_active", "agent": name})

            elif etype == "executor_completed":
                # Flush buffered text now that the agent turn is fully done
                if current_agent:
                    await flush_buf(current_agent)

            elif etype == "handoff_sent":
                src = getattr(event.data, "source", "?")
                tgt = getattr(event.data, "target", "?")
                await eq.put({"type": "handoff", "from": src, "to": tgt})

            elif etype == "output":
                data = event.data
                agent = event.executor_id or current_agent or "agent"

                if isinstance(data, AgentResponseUpdate):
                    for item in getattr(data, "contents", []):
                        itype = getattr(item, "type", None)
                        text = getattr(item, "text", None)

                        if itype == "function_call":
                            fname = getattr(item, "name", "?")
                            args = getattr(item, "parse_arguments", lambda: {})() or {}
                            await eq.put({"type": "tool_call", "agent": agent, "tool": fname, "args": args})
                        elif text:
                            # Buffer ALL tokens — parse after turn completes
                            agent_text_buf[agent] = agent_text_buf.get(agent, "") + text

                else:
                    # Non-streaming AgentResponse — flush any partial buffer first
                    await flush_buf(agent)
                    text = getattr(data, "text", "") or ""
                    if text:
                        reasoning, customer_text = _parse_reasoning(text)
                        if reasoning:
                            await eq.put({"type": "reasoning", "agent": agent, **reasoning})
                        if customer_text:
                            await eq.put({"type": "token", "agent": agent, "text": customer_text})

            elif etype == "request_info":
                # Flush before pausing for user/approval input
                for name in list(agent_text_buf.keys()):
                    await flush_buf(name)
                pending.append(event)

            elif etype in ("failed", "executor_failed"):
                details = event.details
                msg = getattr(details, "message", str(details)) if details else "unknown error"
                await eq.put({"type": "error", "message": msg})

        # Flush any remaining buffers at end of stream
        for name in list(agent_text_buf.keys()):
            await flush_buf(name)

        return pending

    # Initial message is in the response_queue placed by /api/message before task starts
    initial_msg: str = await rq.get()

    stream = workflow.run(initial_msg, stream=True)
    pending = await drain_stream(stream)

    # Multi-turn loop: handle user input and HITL approvals
    while pending:
        responses: dict[str, object] = {}
        for req in pending:
            if isinstance(req.data, HandoffAgentUserRequest):
                await eq.put({
                    "type": "needs_input",
                    "request_id": req.request_id,
                    "agent": req.executor_id or "agent",
                })
                # Wait for user to provide the next message
                user_reply: str = await rq.get()
                responses[req.request_id] = HandoffAgentUserRequest.create_response(user_reply)

            elif isinstance(req.data, Content) and req.data.type == "function_approval_request":
                func = req.data.function_call
                args = func.parse_arguments() or {}
                gate = APPROVAL_GATES.get(func.name, {"gate": "?", "role": "Approver", "label": func.name})
                await eq.put({
                    "type": "needs_approval",
                    "request_id": req.request_id,
                    "gate": gate["gate"],
                    "role": gate["role"],
                    "label": gate["label"],
                    "tool": func.name,
                    "args": args,
                })
                # Wait for approval decision from frontend
                decision: dict = await rq.get()
                approved = decision.get("approved", False)
                responses[req.request_id] = req.data.to_function_approval_response(approved=approved)

        stream = workflow.run(responses=responses, stream=True)
        pending = await drain_stream(stream)

    await eq.put({"type": "session_done"})


# ── API models ────────────────────────────────────────────────────────────────────

class MessageBody(BaseModel):
    text: str

class ApproveBody(BaseModel):
    request_id: str
    approved: bool


# ── API routes ────────────────────────────────────────────────────────────────────

@app.post("/api/session")
async def create_session():
    session_id = str(uuid.uuid4())
    chat_client = create_chat_client()
    agents = create_agents(chat_client)
    workflow = build_workflow(*agents, use_checkpoints=False)

    sessions[session_id] = {
        "workflow": workflow,
        "event_queue": asyncio.Queue(),
        "response_queue": asyncio.Queue(),
        "task": None,
        "agents": [a.name for a in agents],
    }
    return {"session_id": session_id, "agents": sessions[session_id]["agents"]}


@app.get("/api/stream/{session_id}")
async def stream_events(session_id: str):
    if session_id not in sessions:
        raise HTTPException(status_code=404, detail="Session not found")

    sess = sessions[session_id]
    eq: asyncio.Queue = sess["event_queue"]

    async def generate():
        while True:
            event = await eq.get()
            yield _sse(event)
            if event.get("type") in ("session_done", "error"):
                break

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


@app.post("/api/message/{session_id}")
async def send_message(session_id: str, body: MessageBody):
    if session_id not in sessions:
        raise HTTPException(status_code=404, detail="Session not found")

    sess = sessions[session_id]
    await sess["response_queue"].put(body.text)

    # Start the background runner on first message
    if sess["task"] is None:
        sess["task"] = asyncio.create_task(_run_workflow_loop(session_id))

    return {"ok": True}


@app.post("/api/approve/{session_id}")
async def send_approval(session_id: str, body: ApproveBody):
    if session_id not in sessions:
        raise HTTPException(status_code=404, detail="Session not found")

    sess = sessions[session_id]
    await sess["response_queue"].put({"approved": body.approved})
    return {"ok": True}


@app.delete("/api/session/{session_id}")
async def delete_session(session_id: str):
    if session_id not in sessions:
        raise HTTPException(status_code=404, detail="Session not found")

    sess = sessions.pop(session_id)
    if sess["task"] and not sess["task"].done():
        sess["task"].cancel()
    return {"ok": True}


# ── Entry point ───────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import uvicorn
    print("=" * 60)
    print("  Retail Banking — FastAPI Backend")
    print(f"  Model : {os.environ.get('GROQ_MODEL', '(not set)')}")
    print(f"  URL   : http://localhost:8000")
    print("  Docs  : http://localhost:8000/docs")
    print("=" * 60)
    uvicorn.run(app, host="0.0.0.0", port=8000, log_level="info")
