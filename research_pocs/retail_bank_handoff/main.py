"""
Retail Banking Handoff POC — Console Mode
==========================================
Interactive console session with full transparency:
  [AGENT ACTIVE]  — which agent is running
  [HANDOFF]       — agent-to-agent transitions
  [TOOL CALL]     — tool invocations (and approval gates)
  [HITL GATE]     — human-in-the-loop approval prompts
  [SESSION DONE]  — summary of agent path + tools used

Run:
    cd research_pocs/retail_bank_handoff
    python main.py

For the browser-based DevUI with OTel trace panel run:
    python devui_app.py
"""

import asyncio
import sys
import os
import pathlib

from dotenv import load_dotenv

_here = pathlib.Path(__file__).resolve().parent
_env_parent = _here / ".." / ".." / "agent-mesh" / ".env"
_env_local = _here / ".env"

if _env_parent.exists():
    load_dotenv(str(_env_parent), override=False)
if _env_local.exists():
    load_dotenv(str(_env_local), override=True)

sys.path.insert(0, str(_here))

from agent_framework import Content
from agent_framework.orchestrations import HandoffAgentUserRequest
from agents.agent_factory import create_chat_client, create_agents
from workflows.handoff_workflow import build_workflow
from approvals.approval_handler import handle_approval_request, handle_user_input_request


# ── ANSI helpers ──────────────────────────────────────────────────────────────
def _c(text: str, code: str) -> str:
    return f"\033[{code}m{text}\033[0m"

def _bold(t): return _c(t, "1")
def _cyan(t): return _c(t, "36")
def _yellow(t): return _c(t, "33")
def _green(t): return _c(t, "32")
def _red(t): return _c(t, "31")
def _magenta(t): return _c(t, "35")
def _dim(t): return _c(t, "2")

SEP = "─" * 55
SEP2 = "═" * 55

# ── Event handler ─────────────────────────────────────────────────────────────

class SessionTrace:
    """Accumulates transparency info across the streaming event loop."""

    def __init__(self):
        self.agent_path: list[str] = []
        self.tool_calls: list[dict] = []
        self._current_agent: str | None = None
        self._token_buf: str = ""
        self._last_agent_printed: str | None = None

    def on_executor_invoked(self, event) -> None:
        name = event.executor_id or "unknown"
        # Dedupe — the framework may emit executor_invoked multiple times per turn
        if name != self._current_agent:
            self._current_agent = name
            if name not in self.agent_path:
                self.agent_path.append(name)
            print(f"\n{_cyan(_bold(f'[AGENT ACTIVE: {name}]'))}")

    def on_handoff(self, event) -> None:
        src = getattr(event.data, "source", "?")
        tgt = getattr(event.data, "target", "?")
        print(f"\n{_magenta(_bold(f'[HANDOFF: {src}  →  {tgt}]'))}")

    def on_token(self, event) -> None:
        update = event.data
        # AgentResponseUpdate carries .contents (list of Content items)
        for item in getattr(update, "contents", []):
            text = getattr(item, "text", None)
            if text:
                self._token_buf += text
                print(text, end="", flush=True)

    def on_agent_response(self, event) -> None:
        # Non-streaming full AgentResponse
        response = event.data
        text = getattr(response, "text", "") or ""
        agent = event.executor_id or self._current_agent or "agent"
        if text:
            if self._last_agent_printed != agent:
                print(f"\n{_bold(agent)}: ", end="")
                self._last_agent_printed = agent
            print(text)

    def flush_tokens(self, event) -> None:
        if self._token_buf:
            # Token buffer was already printed char-by-char; just add newline
            print()
            self._token_buf = ""

    def on_tool_call_in_message(self, event) -> None:
        # Tool calls surface inside output events as Content items with type="function_call"
        update = event.data
        for item in getattr(update, "contents", []):
            if getattr(item, "type", None) == "function_call":
                name = getattr(item, "name", "?")
                args = getattr(item, "parse_arguments", lambda: {})() or {}
                self.tool_calls.append({"tool": name, "agent": self._current_agent, "args": args})
                print(f"\n  {_yellow(f'[TOOL CALL: {name}({args})]')}")


def _handle_event(event, trace: SessionTrace) -> None:
    etype = event.type

    if etype == "executor_invoked":
        trace.on_executor_invoked(event)

    elif etype == "executor_completed":
        trace.flush_tokens(event)

    elif etype == "handoff_sent":
        trace.on_handoff(event)

    elif etype == "output":
        data = event.data
        # Distinguish streaming chunks (AgentResponseUpdate) from full response (AgentResponse)
        from agent_framework._types import AgentResponseUpdate
        if isinstance(data, AgentResponseUpdate):
            trace.on_token(event)
            trace.on_tool_call_in_message(event)
        else:
            trace.on_agent_response(event)

    elif etype == "status":
        state = getattr(event.state, "name", str(event.state)) if event.state else ""
        if state in ("IDLE", "FAILED"):
            print(f"\n{_dim(f'[status: {state}]')}")

    elif etype in ("started", "superstep_started", "superstep_completed",
                   "executor_bypassed", "intermediate"):
        pass  # low-level framework noise, suppress

    elif etype in ("failed", "executor_failed"):
        details = event.details
        msg = getattr(details, "message", str(details)) if details else "unknown error"
        print(f"\n{_red(f'[ERROR: {msg}]')}")

    # request_info is NOT handled here — it goes to pending_requests


# ── Approval / input handlers ─────────────────────────────────────────────────

APPROVAL_GATES = {
    "fraud_screen_transfer": {"gate": 1, "role": "Fraud Analyst",  "label": "GATE 1 — Fraud Screen Review"},
    "authorize_large_transfer": {"gate": 2, "role": "Branch Manager", "label": "GATE 2 — Manager Authorization"},
    "freeze_account": {"gate": 1, "role": "Fraud Manager",  "label": "GATE 1 — Account Freeze Authorization"},
}


def _handle_approval(event) -> object:
    func = event.data.function_call
    args = func.parse_arguments() or {}
    gate = APPROVAL_GATES.get(func.name, {"label": func.name, "role": "Approver", "gate": "?"})

    print(f"\n{SEP}")
    print(_bold(f"  {gate['label']}"))
    print(f"  Requires : {gate['role']}")
    print(f"  Tool     : {func.name}")
    print("  Args:")
    for k, v in args.items():
        print(f"    {k}: {v}")
    print(SEP)

    raw = input(f"  [{gate['role']}] Approve? (y/n): ").strip().lower()
    approved = raw == "y"
    if not approved:
        reason = input("  Rejection reason: ").strip()
        print(_red(f"  ✗ REJECTED — {reason}"))
    else:
        print(_green("  ✓ APPROVED"))

    return event.data.to_function_approval_response(approved=approved)


def _handle_user_input(event) -> object:
    agent = event.executor_id or "agent"
    print(f"\n{SEP}")
    for msg in event.data.agent_response.messages[-3:]:
        text = getattr(msg, "text", "") or ""
        name = getattr(msg, "author_name", agent) or agent
        if text:
            print(f"  {_bold(name)}: {text}")
    print(SEP)
    user_input = input("  You: ").strip()
    return HandoffAgentUserRequest.create_response(user_input)


# ── Session runner ─────────────────────────────────────────────────────────────

async def run_session(workflow, opening_message: str) -> None:
    trace = SessionTrace()
    pending_requests: list = []

    async for event in workflow.run(opening_message, stream=True):
        if event.type == "request_info":
            pending_requests.append(event)
        else:
            _handle_event(event, trace)

    while pending_requests:
        responses: dict[str, object] = {}
        for req in pending_requests:
            if isinstance(req.data, HandoffAgentUserRequest):
                responses[req.request_id] = _handle_user_input(req)
            elif isinstance(req.data, Content) and req.data.type == "function_approval_request":
                responses[req.request_id] = _handle_approval(req)

        pending_requests = []
        async for event in workflow.run(responses=responses, stream=True):
            if event.type == "request_info":
                pending_requests.append(event)
            else:
                _handle_event(event, trace)

    # Session summary
    print(f"\n{SEP2}")
    print(_bold("  Session Summary"))
    print(SEP2)
    path_str = "  →  ".join(trace.agent_path) if trace.agent_path else "(no agents recorded)"
    print(f"  Agent path : {_cyan(path_str)}")
    if trace.tool_calls:
        print("  Tool calls :")
        for tc in trace.tool_calls:
            print(f"    • {_yellow(tc['tool'])} (by {tc['agent']})")
    else:
        print("  Tool calls : none")
    print(SEP2)


# ── Entry point ────────────────────────────────────────────────────────────────

async def main() -> None:
    print(SEP2)
    print(_bold("  Retail Banking Support — HandoffBuilder POC (console)"))
    print(_dim("  For the browser DevUI with trace panel run: python devui_app.py"))
    print(SEP2)

    mode = input("Mode? (customer/staff) [customer]: ").strip().lower() or "customer"
    use_checkpoints = mode == "staff"
    print(_dim(f"  {'Staff mode — checkpointing enabled' if use_checkpoints else 'Customer mode'}"))
    print(SEP)

    chat_client = create_chat_client()
    agents = create_agents(chat_client)
    workflow = build_workflow(*agents, use_checkpoints=use_checkpoints)

    print("\nHow can we help you today?")
    user_input = input("You: ").strip()
    if not user_input:
        print("No input — exiting.")
        return

    await run_session(workflow, user_input)


if __name__ == "__main__":
    asyncio.run(main())
