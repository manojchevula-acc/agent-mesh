"""
Native MAF Approval Mode - Browser-Based Demo
==============================================

Demonstrates MAF's built-in approval_mode="always_require" on a tool,
with a browser-based approval UI that includes a comment/feedback field.

Flow:
  1. Agent receives a "transfer funds" request.
  2. MAF intercepts the tool call and emits a function_approval_request.
  3. A mini Starlette server opens on port 8099 and the browser launches.
  4. Reviewer sees the tool details, writes an optional comment, and clicks
     Approve or Reject.
  5. The script resumes, sends a function_approval_response back to the agent.
  6. If approved: MAF executes the tool, agent returns a confirmation.
  7. If rejected: agent responds with a decline message.

Run from the agent-mesh/ directory with the project venv active:
    python demos/maf_approval_demo.py
"""

import asyncio
import io
import json
import sys
import pathlib
import webbrowser
from uuid import uuid4

# Force UTF-8 output on Windows consoles
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

# Ensure project root is on sys.path so we can import src.config
project_root = str(pathlib.Path(__file__).resolve().parents[1])
if project_root not in sys.path:
    sys.path.insert(0, project_root)

import uvicorn
from starlette.applications import Starlette
from starlette.requests import Request as StarletteRequest
from starlette.responses import HTMLResponse, JSONResponse as StarletteJSON
from starlette.routing import Route

from agent_framework import Agent, Message, Content, tool
from agent_framework.openai import OpenAIChatCompletionClient
from src.config import Config

APPROVAL_PORT = 8099


# ---------------------------------------------------------------------------
# Tool definition
# ---------------------------------------------------------------------------

@tool(approval_mode="always_require")
def transfer_funds(account_from: str, account_to: str, amount: float) -> str:
    """Transfer funds between two accounts."""
    ref = uuid4().hex[:8].upper()
    return (
        f"Transfer complete. {amount:.2f} GBP moved from {account_from} "
        f"to {account_to}. Reference: TXN-{ref}"
    )


# ---------------------------------------------------------------------------
# Browser approval gate
# ---------------------------------------------------------------------------

class BrowserApprovalGate:
    """Suspends the agent flow until a human submits a decision in the browser."""

    def __init__(self) -> None:
        self._event = asyncio.Event()
        self.approved: bool = False
        self.comment: str = ""

    def resolve(self, approved: bool, comment: str) -> None:
        self.approved = approved
        self.comment = comment
        self._event.set()

    async def wait(self) -> None:
        await self._event.wait()


def _build_approval_page(tool_name: str, arguments: object) -> str:
    args_json = json.dumps(arguments, indent=2) if isinstance(arguments, dict) else str(arguments)
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <title>MAF Tool Approval Request</title>
  <style>
    *, *::before, *::after {{ box-sizing: border-box; }}
    body {{
      margin: 0; padding: 2rem;
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
      background: #f4f5f7; color: #1e293b;
    }}
    .card {{
      max-width: 560px; margin: 0 auto;
      background: #fff; border-radius: 12px;
      box-shadow: 0 4px 24px rgba(0,0,0,.10);
      overflow: hidden;
    }}
    .banner {{
      background: #fffbeb; border-bottom: 1px solid #fde68a;
      padding: 1rem 1.5rem;
      display: flex; align-items: center; gap: .75rem;
    }}
    .banner-icon {{ font-size: 1.5rem; }}
    .banner-text {{ font-size: .875rem; color: #92400e; font-weight: 500; }}
    .body {{ padding: 1.5rem; }}
    .field {{ margin-bottom: 1.25rem; }}
    label {{ display: block; font-size: .75rem; font-weight: 600;
             color: #64748b; text-transform: uppercase; letter-spacing: .05em;
             margin-bottom: .35rem; }}
    .value {{ font-size: .9rem; color: #1e293b; }}
    pre {{
      background: #f8fafc; border: 1px solid #e2e8f0;
      border-radius: 6px; padding: .75rem 1rem;
      font-size: .8rem; white-space: pre-wrap; word-break: break-all;
      margin: 0;
    }}
    textarea {{
      width: 100%; padding: .65rem .875rem;
      border: 1px solid #cbd5e1; border-radius: 8px;
      font-size: .875rem; font-family: inherit;
      resize: vertical; min-height: 80px;
      outline: none; transition: border-color .15s;
    }}
    textarea:focus {{ border-color: #6366f1; }}
    .actions {{
      display: flex; gap: .75rem; justify-content: flex-end;
      padding: 1rem 1.5rem; background: #f8fafc;
      border-top: 1px solid #e2e8f0;
    }}
    button {{
      padding: .55rem 1.25rem; border-radius: 8px; border: none;
      font-size: .875rem; font-weight: 600; cursor: pointer;
      transition: opacity .15s;
    }}
    button:disabled {{ opacity: .5; cursor: default; }}
    .btn-reject {{
      background: #fff; color: #dc2626;
      border: 1.5px solid #fca5a5;
    }}
    .btn-reject:hover:not(:disabled) {{ background: #fef2f2; }}
    .btn-approve {{
      background: #16a34a; color: #fff;
    }}
    .btn-approve:hover:not(:disabled) {{ background: #15803d; }}
    .success {{
      display: none; padding: 1rem 1.5rem;
      background: #f0fdf4; border-top: 1px solid #bbf7d0;
      color: #166534; font-size: .875rem; font-weight: 500;
      text-align: center;
    }}
    .call-id {{ font-family: monospace; font-size: .75rem; color: #94a3b8; }}
  </style>
</head>
<body>
  <div class="card">
    <div class="banner">
      <span class="banner-icon">&#x26A0;&#xFE0F;</span>
      <span class="banner-text">
        A tool call requires your approval before the AI pipeline continues.
      </span>
    </div>

    <div class="body">
      <div class="field">
        <label>Tool</label>
        <div class="value"><strong>{tool_name}</strong></div>
      </div>

      <div class="field">
        <label>Arguments</label>
        <pre>{args_json}</pre>
      </div>

      <div class="field">
        <label>Comments / Feedback <span style="font-weight:400;text-transform:none;">(optional)</span></label>
        <textarea id="comment" placeholder="Add a note about your decision..."></textarea>
      </div>
    </div>

    <div class="actions" id="actions">
      <button class="btn-reject" onclick="decide(false)">Reject</button>
      <button class="btn-approve" onclick="decide(true)">Approve &amp; Continue</button>
    </div>

    <div class="success" id="success">
      Decision submitted &mdash; you may close this tab.
    </div>
  </div>

  <script>
    async function decide(approved) {{
      const comment = document.getElementById('comment').value.trim();
      document.querySelectorAll('button').forEach(b => b.disabled = true);
      await fetch('/decision', {{
        method: 'POST',
        headers: {{ 'Content-Type': 'application/json' }},
        body: JSON.stringify({{ approved, comment }}),
      }});
      document.getElementById('actions').style.display = 'none';
      document.getElementById('success').style.display = 'block';
    }}
  </script>
</body>
</html>"""


def build_starlette_app(gate: BrowserApprovalGate, tool_name: str, arguments: object) -> Starlette:
    page_html = _build_approval_page(tool_name, arguments)

    async def approval_page(_: StarletteRequest) -> HTMLResponse:
        return HTMLResponse(page_html)

    async def receive_decision(req: StarletteRequest) -> StarletteJSON:
        body = await req.json()
        gate.resolve(approved=bool(body.get("approved")), comment=str(body.get("comment", "")))
        return StarletteJSON({"ok": True})

    return Starlette(routes=[
        Route("/", approval_page, methods=["GET"]),
        Route("/decision", receive_decision, methods=["POST"]),
    ])


# ---------------------------------------------------------------------------
# Helper: pull all function_approval_request content items from a response
# ---------------------------------------------------------------------------

def extract_approval_requests(response) -> list[Content]:
    return [
        content
        for msg in response.messages
        for content in msg.contents
        if content.type == "function_approval_request"
    ]


# ---------------------------------------------------------------------------
# Main demo
# ---------------------------------------------------------------------------

async def main() -> None:
    print("=" * 58)
    print("  Native MAF Approval Demo - transfer_funds tool")
    print("=" * 58)

    client = OpenAIChatCompletionClient(
        model=Config.GROQ_MODEL,
        api_key=Config.GROQ_API_KEY,
        base_url=Config.LLM_BASE_URL,
    )

    agent = Agent(
        client=client,
        name="BankingDemoAgent",
        instructions=(
            "You are a banking assistant. "
            "When the user asks to transfer money, call the transfer_funds tool "
            "with the details they provide. "
            "If the transfer is declined, apologise and suggest contacting the bank."
        ),
        tools=[transfer_funds],
    )

    user_query = "Please transfer 500 GBP from account ACC-001 to account ACC-002."
    print(f"\nUser: {user_query}\n")
    print("Running agent (Turn 1)...")

    response = await agent.run(user_query)

    approval_requests = extract_approval_requests(response)
    if not approval_requests:
        print(f"Agent: {response.text}")
        print("\n(No approval request was generated — the model may not have called the tool.)")
        return

    approval_response_content: Content | None = None

    for req in approval_requests:
        fc = req.function_call
        gate = BrowserApprovalGate()
        app = build_starlette_app(gate, fc.name, fc.arguments)

        # Start uvicorn without blocking the event loop
        cfg = uvicorn.Config(app, host="127.0.0.1", port=APPROVAL_PORT, log_level="error")
        server = uvicorn.Server(cfg)
        server_task = asyncio.create_task(server.serve())

        # Give the server a moment to bind before opening the browser
        await asyncio.sleep(0.5)
        url = f"http://localhost:{APPROVAL_PORT}"
        print(f"\nApproval page opened in browser: {url}")
        print("Waiting for your decision...\n")
        webbrowser.open(url)

        # Suspend here until the human submits the form
        await gate.wait()
        server.should_exit = True
        await server_task

        label = "APPROVED" if gate.approved else "REJECTED"
        print(f"Decision: {label}")
        if gate.comment:
            print(f"Comment:  {gate.comment}")
        print()

        approval_response_content = req.to_function_approval_response(approved=gate.approved)

    if approval_response_content is None:
        return

    print("Running agent (Turn 2)...")
    followup = await agent.run(response.messages + [Message("user", [approval_response_content])])
    print(f"\nAgent: {followup.text}\n")


if __name__ == "__main__":
    asyncio.run(main())
