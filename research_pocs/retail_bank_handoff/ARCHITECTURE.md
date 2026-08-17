# Retail Bank Handoff — Architecture & Flow Reference

## Table of Contents
1. [System Overview](#1-system-overview)
2. [Component Architecture](#2-component-architecture)
3. [Agent Mesh Topology](#3-agent-mesh-topology)
4. [End-to-End Conversation Flow](#4-end-to-end-conversation-flow)
5. [Multi-Intent Orchestration Protocol](#5-multi-intent-orchestration-protocol)
6. [HITL (Human-in-the-Loop) Gate Flow](#6-hitl-human-in-the-loop-gate-flow)
7. [Web Mode: SSE & State Management](#7-web-mode-sse--state-management)
8. [Checkpoint & Session Persistence](#8-checkpoint--session-persistence)
9. [Data Contracts](#9-data-contracts)
10. [Configuration Reference](#10-configuration-reference)

---

## 1. System Overview

Retail Bank Handoff is a **multi-agent banking assistant** built on Microsoft Agent Framework (MAF). A single customer request — which may span multiple banking domains — is automatically routed through a mesh of specialist agents, with each agent executing its domain tools, then handing off to the next. The result is a single consolidated response back to the customer, regardless of how many agents were involved.

**Key design principles:**
- **Open mesh topology** — every agent can hand off to any other agent; no hardcoded routing graph
- **Shared conversation context** — all agents see the same flat message history at all times
- **Autonomous multi-intent chaining** — specialists self-sequence using a stable priority order without central coordination
- **HITL gates** — high-risk operations (large transfers, account freezes) pause for human approval before executing
- **Three UI surfaces** — console, MAF DevUI, and a custom React + FastAPI web UI

---

## 2. Component Architecture

```
┌──────────────────────────────────────────────────────────────────────────┐
│                         Entry Points                                      │
│                                                                           │
│  ┌─────────────┐    ┌─────────────────┐    ┌──────────────────────────┐  │
│  │   main.py   │    │  devui_app.py   │    │     ui_server.py         │  │
│  │  (console)  │    │  (MAF DevUI)    │    │  (FastAPI + SSE, :8000)  │  │
│  │  port: TTY  │    │  port: 8080     │    │                          │  │
│  └──────┬──────┘    └────────┬────────┘    └───────────┬──────────────┘  │
│         │                    │                          │                  │
│         └────────────────────┴──────────────────────────┘                 │
│                              │                                             │
│                     ┌────────▼────────┐                                   │
│                     │  HandoffBuilder  │  (MAF orchestrator)               │
│                     │    Workflow      │                                   │
│                     └────────┬────────┘                                   │
│                              │                                             │
│         ┌────────────────────┼────────────────────────┐                   │
│         │                    │                         │                   │
│  ┌──────▼──────┐   ┌─────────▼──────┐    ┌───────────▼────────┐          │
│  │ agent_      │   │  workflows/    │    │  tools/             │          │
│  │ factory.py  │   │  handoff_      │    │  account_tools.py   │          │
│  │ (6 agents)  │   │  workflow.py   │    │  card_tools.py      │          │
│  └─────────────┘   └────────────────┘    │  loan_tools.py      │          │
│                                          │  transfer_tools.py  │ ◄─ HITL  │
│                                          │  fraud_tools.py     │ ◄─ HITL  │
│                                          └─────────────────────┘          │
│                                                                           │
│  ┌──────────────────────┐   ┌──────────────────────┐                     │
│  │  session_logger.py   │   │  approvals/           │                     │
│  │  (JSONL per session) │   │  approval_handler.py  │                     │
│  └──────────────────────┘   └──────────────────────┘                     │
│                                                                           │
│  ┌──────────────────────────────────────────────────────────────────┐    │
│  │  frontend/ (React 18 + Vite 6, dev port: 5173)                    │    │
│  │  App.jsx → ChatPanel | AgentFlowPanel | ApprovalsPanel |          │    │
│  │            ReasoningPanel | HistoryPanel                          │    │
│  └──────────────────────────────────────────────────────────────────┘    │
└──────────────────────────────────────────────────────────────────────────┘
                              │
                              ▼
                   ┌──────────────────────┐
                   │  Cerebras API         │
                   │  (OpenAI-compatible)  │
                   │  model: gpt-oss-120b  │
                   └──────────────────────┘
```

---

## 3. Agent Mesh Topology

All six agents are connected in a **fully open mesh** — every agent automatically receives a `handoff_to_<agent>` tool for every other participant. There are no hardcoded edges.

```
                         ┌─────────────────┐
                         │   triage_agent   │  ← entry point
                         │  (intent router  │  ← exit point (farewell phrase triggers termination)
                         │  + consolidator) │
                         └────────┬────────┘
                                  │
               ┌──────────────────┼──────────────────┐
               │       open mesh: all ↔ all           │
               │                                      │
    ┌──────────▼──────────┐              ┌────────────▼────────────┐
    │    account_agent     │              │      card_agent          │
    │  • get_account_      │              │  • get_card_status       │
    │    balance           │              │  • block_card            │
    │  • get_mini_         │              │  • raise_transaction_    │
    │    statement         │              │    dispute               │
    │  • update_contact_   │              └─────────────────────────┘
    │    details           │
    └──────────────────────┘
               │                                      │
    ┌──────────▼──────────┐              ┌────────────▼────────────┐
    │     loan_agent       │              │    transfer_agent        │
    │  • check_loan_       │              │  • fraud_screen_transfer │ ◄── Gate 1 (Fraud Analyst)
    │    eligibility       │              │  • authorize_large_      │ ◄── Gate 2 (Branch Manager)
    │  • get_loan_status   │              │    transfer              │
    └──────────────────────┘              └─────────────────────────┘
                                                       │
                                          ┌────────────▼────────────┐
                                          │      fraud_agent         │
                                          │  • flag_suspicious_      │
                                          │    transaction           │
                                          │  • freeze_account        │ ◄── Gate 1 (Fraud Manager)
                                          └─────────────────────────┘

  API Key Routing (rate-limit avoidance):
  K1 → triage_agent, card_agent, transfer_agent
  K2 → account_agent, loan_agent, fraud_agent
```

**Handoff tool injection** is automatic via `HandoffAgentExecutor`. Each agent's effective tool set is:

```
<specialist_agent tools>  +  handoff_to_triage_agent
                          +  handoff_to_account_agent
                          +  handoff_to_card_agent
                          +  handoff_to_loan_agent
                          +  handoff_to_transfer_agent
                          +  handoff_to_fraud_agent
                             (minus: handoff_to_self)
```

---

## 4. End-to-End Conversation Flow

### 4a. Single-Intent Example (Card Block Request)

```
Customer: "Please block my card ending 5821"
                    │
                    ▼
          ┌──────────────────┐
          │   triage_agent   │  Detects: card domain
          │                  │  Missing: account number
          │  → needs_input   │
          └────────┬─────────┘
                   │ HandoffAgentUserRequest
                   ▼
          Customer provides: "Account: 1234567"
                   │
          ┌────────┘
          │
          ▼
    ┌─────────────────┐
    │   card_agent    │  Calls: block_card(card_last4="5821", account_number="1234567")
    │                 │  Result: "Card ending 5821 has been blocked successfully."
    │  Turn 1: tool   │
    │  Turn 2: brief  │  "Card has been blocked. Confirming with triage."
    │  Turn 3: handoff│→ handoff_to_triage_agent
    └─────────────────┘
                   │
                   ▼
          ┌──────────────────┐
          │   triage_agent   │  Reads shared conversation, synthesises:
          │  (consolidator)  │  "Your card ending in 5821 has been successfully
          │                  │   blocked. Is there anything else I can help you with?"
          └──────────────────┘
                   │
       Customer: "No thanks, goodbye"
                   │
          ┌────────┘
          ▼
    ┌─────────────┐
    │ triage_agent│  "Thank you for banking with us. Have a great day!"
    └─────────────┘
           │
           ▼
    ◄ termination_condition matches "have a great day" → workflow ends ►
```

### 4b. Multi-Intent Example (Balance + Card Status + Loan Check)

```
Customer: "What's my balance, check my card status, and tell me if I'm eligible for a loan"
                    │
                    ▼
          ┌──────────────────────────────────────────────────┐
          │                  triage_agent                     │
          │  Detects 3 intents: account, card, loan           │
          │  Emits: multi_intent_queue = [account, card, loan]│
          │  Asks for: account number, card last 4 digits     │
          └──────────────────────┬───────────────────────────┘
                                 │ needs_input
                                 ▼
                Customer provides account + card details
                                 │
          ┌──────────────────────┘
          │
          ▼
    ┌─────────────────────┐
    │   account_agent     │  Priority 1 (account always first)
    │                     │  Calls: get_account_balance()
    │                     │  Reads shared history: card & loan NOT yet done
    │  Turn 1: tool call  │  → calls handoff_to_card_agent
    └─────────────────────┘
              │
              ▼
    ┌─────────────────────┐
    │    card_agent       │  Priority 2
    │                     │  Calls: get_card_status()
    │                     │  Reads shared history: loan NOT yet done
    │  Turn 1: tool call  │  → calls handoff_to_loan_agent
    └─────────────────────┘
              │
              ▼
    ┌─────────────────────┐
    │    loan_agent       │  Priority 3
    │                     │  Calls: check_loan_eligibility()
    │                     │  Reads shared history: all domains done
    │  Turn 1: tool call  │  → calls handoff_to_triage_agent
    └─────────────────────┘
              │
              ▼
    ┌──────────────────────────────────────────────────────────┐
    │                      triage_agent                         │
    │  Reads all 3 tool results from shared conversation        │
    │  Produces one consolidated reply:                         │
    │  "Your balance is £4,250. Your card ending 5821 is       │
    │   active. You are eligible for a personal loan up to     │
    │   £15,000. Have a wonderful day!"                        │
    └──────────────────────────────────────────────────────────┘
              │
              ▼
     ◄ termination_condition: "have a wonderful day" → done ►
```

### 4c. Detailed Sequence Diagram (Web Mode)

```
Browser          FastAPI           MAF Workflow        Agents          Cerebras API
   │                │                    │                │                  │
   │─POST /session──►                    │                │                  │
   │◄─{session_id}──│                    │                │                  │
   │                │                    │                │                  │
   │─GET /stream────►                    │                │                  │
   │  (SSE open)    │                    │                │                  │
   │                │                    │                │                  │
   │─POST /message──►                    │                │                  │
   │                │─run_workflow_loop──►                │                  │
   │                │                    │─dispatch to────►                  │
   │                │                    │  triage_agent  │                  │
   │                │                    │                │─LLM call─────────►
   │                │                    │                │◄─streaming tokens─│
   │◄─SSE:agent_active(triage)───────────│                │                  │
   │◄─SSE:token(...)────────────────────│                │                  │
   │◄─SSE:needs_input───────────────────│                │                  │
   │                │                    │◄─paused────────│                  │
   │─POST /message──►                    │                │                  │
   │  (account details)                  │─resume─────────►                  │
   │                │                    │─handoff to─────►                  │
   │                │                    │  account_agent │                  │
   │◄─SSE:handoff(triage→account)────────│                │                  │
   │◄─SSE:agent_active(account)──────────│                │                  │
   │◄─SSE:tool_call(get_account_balance)─│                │                  │
   │◄─SSE:token(account response)────────│                │                  │
   │                │                    │─handoff to─────►                  │
   │                │                    │  triage_agent  │                  │
   │◄─SSE:handoff(account→triage)────────│                │                  │
   │◄─SSE:reasoning(triage)──────────────│                │                  │
   │◄─SSE:token(final response)──────────│                │                  │
   │◄─SSE:session_done───────────────────│                │                  │
   │  (SSE closes)  │                    │                │                  │
```

---

## 5. Multi-Intent Orchestration Protocol

The multi-intent capability is entirely **emergent from agent prompting** — there is no hardcoded routing logic in the orchestrator for multiple intents.

### How It Works

Each specialist agent's system prompt contains a `_MULTI_INTENT_PROTOCOL` section that instructs it to:

1. **After completing its domain tool**, scan the **shared conversation history** for tool result entries.
2. **Identify which domains** have already been completed (by checking which domain tool names appear as `function_call_result` entries).
3. **Select the next unresolved domain** using the stable priority order:

```
account_agent → card_agent → loan_agent → transfer_agent → fraud_agent → triage_agent
```

4. **Emit exactly one handoff** call to the next unresolved domain, or back to `triage_agent` if all are done.

### Autonomous Turn Limits

Each specialist gets a fixed turn budget enforced by `with_autonomous_mode()`:

```
triage_agent:   2 turns   (ask for details + hand off OR consolidate + farewell)
account_agent:  4 turns   (reasoning → tool call → brief message → handoff)
card_agent:     4 turns
loan_agent:     4 turns
transfer_agent: 8 turns   (extra budget for 2 HITL approval gates)
fraud_agent:    5 turns
```

The per-agent autonomous prompt for `triage_agent` is:
> "Review shared conversation. Either wait for details or produce consolidated response."

### Specialist Turn Protocol (3-turn pattern)

```
Turn 1: Call exactly one domain tool (no text, just the function_call)
Turn 2: Brief factual confirmation message (≤ 2 sentences)
Turn 3: Call handoff_to_<next_agent> or handoff_to_triage_agent
```

This 3-turn structure ensures clean separation between tool execution and orchestration, and prevents specialists from attempting consolidation (that is triage_agent's job).

### Reasoning Block

Every agent is prompted to emit a structured reasoning block **before** its customer-facing text:

```
<<<REASONING>>>
called_because: [why control was routed here]
context:        [key facts extracted from shared conversation]
action:         [tool name being called / agent being handed to / 'direct answer']
decision:       [final decision and reasoning]
<<<END_REASONING>>>

[Customer-facing text here]
```

The `ui_server.py` strips these blocks out before forwarding tokens to the chat panel, and emits them as separate `SSE:reasoning` events for the Reasoning tab.

---

## 6. HITL (Human-in-the-Loop) Gate Flow

Three tools require explicit human approval before execution. They are decorated with `@tool(approval_mode="always_require")`.

| Tool | Agent | Gate | Approver Role |
|------|-------|------|---------------|
| `fraud_screen_transfer` | transfer_agent | Gate 1 | Fraud Analyst |
| `authorize_large_transfer` | transfer_agent | Gate 2 | Branch Manager |
| `freeze_account` | fraud_agent | Gate 1 | Fraud Manager |

### HITL Flow Diagram

```
  transfer_agent
       │
       │  calls: fraud_screen_transfer(amount=50000, ...)
       │
       ▼
  ┌─────────────────────────────────────────────────────┐
  │  MAF Framework intercepts: approval_mode=always_req │
  │  Blocks tool execution                              │
  │  Emits: function_approval_request event             │
  └─────────────────────────────────────────────────────┘
       │
       ├── Console Mode ──────────────────────────────────────────┐
       │                                                           │
       │  approval_handler.py:handle_approval_request()           │
       │  Prints: "APPROVAL REQUIRED — Gate 1 (Fraud Analyst)"    │
       │  Prompts: "Approve? [y/n]:"                              │
       │  Staff types: y                                           │
       │  → event.data.to_function_approval_response(approved=True)│
       │                                                           │
       ├── Web Mode ─────────────────────────────────────────────┐│
       │                                                          ││
       │  ui_server.py emits SSE: needs_approval                 ││
       │  ┌─────────────────────────────────────────────┐        ││
       │  │  React: ApprovalsPanel shows HitlGate card  │        ││
       │  │  Gate 1 — Fraud Screen Review               │        ││
       │  │  Required role: Fraud Analyst               │        ││
       │  │  Tool: fraud_screen_transfer                │        ││
       │  │  Args: {amount: 50000, ...}                 │        ││
       │  │  [Approve] [Reject]                         │        ││
       │  └─────────────────────────────────────────────┘        ││
       │  Staff clicks Approve → POST /api/approve/{id}          ││
       │  → response_queue.put({"approved": true, ...})          ││
       │  → workflow.run(responses={req_id: approval_response})  ││
       │                                                          ││
       └──────────────────────────────────────────────────────────┘│
                                                                    │
       ◄──────────────────────────────────────────────────────────┘│
       │                                                            │
       ▼                                                            │
  Tool executes (if approved) → result injected into shared conv   │
       │                                                            │
       ▼                                                            │
  transfer_agent: calls authorize_large_transfer (Gate 2) ─────────┘
  [Same approval cycle repeats with Branch Manager role]
       │
       ▼
  If Gate 1 rejected OR high-risk result:
       │
       └──► handoff_to_fraud_agent
               │
               ▼
         fraud_agent calls freeze_account (Gate 1: Fraud Manager approval)
```

---

## 7. Web Mode: SSE & State Management

### Backend Architecture (`ui_server.py`)

```
FastAPI app
│
├── POST /api/session
│     Creates: session dict {workflow, event_queue, response_queue, task, agents}
│     Returns: {session_id: <uuid>}
│
├── GET /api/stream/{session_id}    ← SSE endpoint
│     StreamingResponse (text/event-stream)
│     Drains event_queue → serialises to SSE JSON lines
│
├── POST /api/message/{session_id}  ← user chat messages + HITL text inputs
│     Puts text onto response_queue
│     Triggers _run_workflow_loop task if not already running
│
├── POST /api/approve/{session_id}  ← HITL approval decisions
│     Puts {request_id, approved, reason} onto response_queue
│
└── DELETE /api/session/{session_id}
      Cancels background task, cleans up session dict

_run_workflow_loop (asyncio background task)
│
├── await workflow.run(initial_message, stream=True)
│     iterate MAF events:
│     ├── executor_invoked  → queue: agent_active
│     ├── output (text)     → buffer token by agent
│     ├── output (fn_call)  → queue: tool_call
│     ├── handoff_sent      → flush buffer → parse reasoning → queue: reasoning, tokens
│     │                     → queue: handoff
│     ├── executor_completed→ flush buffer → parse reasoning → queue: reasoning, tokens
│     └── request_info      → branch:
│           HandoffAgentUserRequest → queue: needs_input → await response_queue
│           function_approval_request → queue: needs_approval → await response_queue
│
└── workflow ends → queue: session_done
```

### SSE Event Catalogue

| Event type | When | Key fields |
|------------|------|------------|
| `agent_active` | Agent executor starts | `agent` |
| `token` | Streaming text chunk | `agent`, `text` |
| `tool_call` | Agent calls a domain tool | `agent`, `tool`, `args` |
| `handoff` | Agent hands off to another | `from`, `to` |
| `reasoning` | Reasoning block extracted | `agent`, `called_because`, `context`, `action`, `decision` |
| `multi_intent_queue` | triage identifies multiple domains | `agent`, `pending: [...]` |
| `needs_input` | Agent requests customer info | `request_id`, `agent` |
| `needs_approval` | HITL gate triggered | `request_id`, `gate`, `role`, `label`, `tool`, `args` |
| `session_done` | Workflow terminated | — |
| `error` | Unhandled exception | `message` |

### React Frontend State

```javascript
// App.jsx state atoms
messages:     []          // chat thread items (user | agent | handoff | gate_notice)
reasoning:    []          // per-agent reasoning step cards
agentFlow:    []          // handoff path sequence + active agent
toolCalls:    []          // tool invocation log
approvals:    []          // HITL gate objects {id, gate, role, tool, args, status}
activeAgent:  null        // currently executing agent name
pendingGateId: null       // gates waiting for decision
done:         false       // session terminated flag
```

### React Panel Layout

```
┌────────────────────────────────────────────────────────────────────┐
│                                                                     │
│   ┌───────────────────────────┐   ┌────────────────────────────┐   │
│   │        Chat Panel          │   │   [Reasoning] [Approvals]  │   │
│   │                            │   │   [Flow]      [History]    │   │
│   │  User: "Block my card..."  │   │                            │   │
│   │                            │   │  ┌─────────────────────┐  │   │
│   │  ── triage_agent ──        │   │  │ AgentFlowPanel       │  │   │
│   │  "Please provide your..."  │   │  │                      │  │   │
│   │                            │   │  │ [triage] → [card]    │  │   │
│   │  You: "Account: 1234567"   │   │  │                      │  │   │
│   │                            │   │  │ Tools called:        │  │   │
│   │  ── card_agent ──          │   │  │  • block_card(...)   │  │   │
│   │  "Your card has been..."   │   │  └─────────────────────┘  │   │
│   │                            │   │                            │   │
│   │  ── triage_agent ──        │   │  [Approvals badge: 0]     │   │
│   │  "Card ending 5821 is      │   │  [Reasoning steps: 2]     │   │
│   │   blocked. Have a great    │   │                            │   │
│   │   day!"                    │   └────────────────────────────┘   │
│   │                            │                                     │
│   └───────────────────────────┘                                     │
│                                                                     │
└────────────────────────────────────────────────────────────────────┘
```

---

## 8. Checkpoint & Session Persistence

### Checkpoint Storage (Staff Mode)

When `use_checkpoints=True`, `FileCheckpointStorage("./checkpoints")` serialises the full workflow state after each superstep:

```
checkpoints/
└── <session-uuid>.json          ← main checkpoint
    <session-uuid>.json.tmp      ← atomic write staging file
```

**Checkpoint JSON structure:**

```json
{
  "workflow_name": "retail_bank_handoff",
  "graph_signature_hash": "<hash>",
  "checkpoint_id": "<uuid>",
  "previous_checkpoint_id": null,
  "timestamp": "2026-08-17T10:30:00Z",
  "messages": {
    "triage_agent": [{ "__pickled__": "<base64>", "__type__": "WorkflowMessage" }]
  },
  "state": {
    "_executor_state": {
      "account_agent": {
        "full_conversation": [...],
        "agent_session": {
          "type": "session",
          "session_id": "<uuid>",
          "state": { "in_memory": { "messages": [...] } }
        }
      }
    }
  }
}
```

Messages are Python-pickled and base64-encoded. The `agent_session.state.in_memory.messages` are plain JSON for the per-service local history.

### Session Logs (JSONL)

`session_logger.py` writes one JSONL file per session to `logs/<session_id>.log`. Each line is a structured event:

```jsonl
{"event": "SESSION_CREATED", "session_id": "...", "timestamp": "..."}
{"event": "USER_MESSAGE",     "session_id": "...", "text": "...", "timestamp": "..."}
{"event": "AGENT_ACTIVE",     "session_id": "...", "agent": "triage_agent", "timestamp": "..."}
{"event": "TOOL_CALL",        "session_id": "...", "agent": "...", "tool": "...", "args": {...}, "timestamp": "..."}
{"event": "HANDOFF",          "session_id": "...", "from": "...", "to": "...", "timestamp": "..."}
{"event": "REASONING",        "session_id": "...", "agent": "...", "called_because": "...", "timestamp": "..."}
{"event": "APPROVAL_REQUESTED","session_id": "...", "gate": 1, "tool": "...", "timestamp": "..."}
{"event": "APPROVAL_DECISION","session_id": "...", "approved": true, "timestamp": "..."}
{"event": "SESSION_DONE",     "session_id": "...", "timestamp": "..."}
```

### Browser History (localStorage)

Completed sessions are saved by React to `localStorage["rbh_session_<uuid>"]`. The `HistoryPanel` reads these for offline replay. Data includes the full message thread, agent path string, and session stats.

---

## 9. Data Contracts

### MAF Shared Conversation Message

```json
{
  "role": "user" | "assistant",
  "contents": [
    { "type": "text",                "text": "..." },
    { "type": "function_call",       "name": "get_account_balance", "arguments": "{\"account_number\": \"1234567\"}" },
    { "type": "function_call_result","name": "get_account_balance", "result": "Current balance: £4,250.00" }
  ]
}
```

### Reasoning Block (in-text format, stripped by ui_server.py)

```
<<<REASONING>>>
called_because: Customer requested account balance and card status. Handling account domain first (priority 1).
context: account_number=1234567, card_last4=5821. Card domain not yet resolved.
action: get_account_balance
decision: Call get_account_balance for account 1234567. After completion, hand off to card_agent.
<<<END_REASONING>>>
```

### HITL Approval Request (MAF internal)

```python
# Emitted by MAF on approval_mode="always_require"
event.data  # type: FunctionApprovalRequest
  .function_call.name       # tool name
  .function_call.arguments  # JSON string of args
  .to_function_approval_response(approved=True)  # builds response object
```

### API Endpoints

| Method | Path | Request Body | Response |
|--------|------|--------------|----------|
| `POST` | `/api/session` | `{mode?: "customer"\|"staff"}` | `{session_id: string}` |
| `GET` | `/api/stream/{id}` | — | SSE stream |
| `POST` | `/api/message/{id}` | `{message: string}` | `204 No Content` |
| `POST` | `/api/approve/{id}` | `{request_id, approved: bool, reason?: string}` | `204 No Content` |
| `DELETE` | `/api/session/{id}` | — | `204 No Content` |

---

## 10. Configuration Reference

All configuration lives in `.env` (local overrides parent `agent-mesh/.env`):

### LLM / API

| Variable | Default | Purpose |
|----------|---------|---------|
| `LLM_BASE_URL` | `https://api.cerebras.ai/v1` | Cerebras OpenAI-compatible endpoint |
| `TRIAGE_API_KEY` | K1 | triage_agent API key |
| `ACCOUNT_API_KEY` | K2 | account_agent API key |
| `CARD_API_KEY` | K1 | card_agent API key |
| `LOAN_API_KEY` | K2 | loan_agent API key |
| `TRANSFER_API_KEY` | K1 | transfer_agent API key |
| `FRAUD_API_KEY` | K2 | fraud_agent API key |
| `*_MODEL` | `gpt-oss-120b` | Model name per agent (same for all) |

### DevUI (MAF Built-in)

| Variable | Default | Purpose |
|----------|---------|---------|
| `DEVUI_HOST` | `127.0.0.1` | Bind address |
| `DEVUI_PORT` | `8080` | Listening port |
| `DEVUI_AUTO_OPEN` | `true` | Open browser on start |
| `DEVUI_NO_AUTH` | `true` | Disable authentication |

### Observability

| Variable | Default | Purpose |
|----------|---------|---------|
| `OBS_PROFILE` | `off` | Disables external OTLP exporters |
| `ENABLE_INSTRUMENTATION` | `true` | MAF in-process span collector |
| `ENABLE_SENSITIVE_DATA` | `true` | Include request/response data in traces |
| `ENABLE_CONSOLE_EXPORTERS` | `false` | No console OTel output |
| `OTEL_METRICS_EXPORTER` | `none` | Suppress metric export |
| `OTEL_EXPORTER_OTLP_ENDPOINT` | _(empty)_ | No OTLP collector |

### Running the Project

```bash
# Console mode
python main.py

# MAF DevUI (port 8080)
python devui_app.py

# React + FastAPI web UI
python ui_server.py          # start backend (port 8000)
cd frontend && npm run dev   # start frontend dev server (port 5173)

# Automated acceptance test
python validate_multi_intent.py
```
