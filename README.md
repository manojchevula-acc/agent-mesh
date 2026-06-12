# Policy-Aware Employee Action Request Assistant
### Microsoft Agent Framework Python SDK Reference Demo

A reference demonstration of a multi-agent cooperative mesh architecture using the **Microsoft Agent Framework Python SDK**.

---

## 1. What the Demo Does
The demo simulates an enterprise virtual assistant that handles employee requests regarding folder access permissions, policy lookups, and travel expense reimbursement submissions. It delegates tasks dynamically across five cooperative specialist agents, processes inputs against corporate policy records, runs compliance checks, prompts for human-in-the-loop approvals, executes simulated system actions, and writes scrubbed audit logs.

---

## 2. Why This Agent Architecture Was Chosen
This topology showcases standard multi-agent separation of concerns instead of a monolithic single-agent prompt:
* **Decoupled Roles**: It ensures policy retrieval, compliance checks, approvals, and system state modifications are performed by distinct agents with specialized instructions, making them easier to test, version, and control.
* **Cooperative Collaboration**: The `Coordinator` acts as the router and summarizer, while specialists evaluate parts of the lifecycle.
* **Deterministic Guardrails**: Orchestrating agents sequentially prevents LLMs from executing actions without passing safety checks.

---

## 3. Microsoft Agent Framework Capabilities Demonstrated
1. **Multi-Agent Systems**: Multiple distinct `Agent` instances executing specialist roles.
2. **Context Passing**: Transferring transcripts and task contexts across agents.
3. **Session Thread Management**: Retaining multi-turn conversation states.
4. **Agent Middleware Pipeline**: Utilizing custom `AgentMiddleware` subclass (`AuditMiddleware`) to intercept execution.
5. **Observability**: Structured trace logging at each agent transition.
6. **Human-in-the-Loop Approval**: Pausing execution to query a human administrator.
7. **PII Scrubbing Middleware**: Redacting emails and SSNs at the framework layer.

---

## 4. What is Real vs. Mocked
* **Real**:
  - Full Microsoft Agent Framework `Agent` loop executing prompts and instructions.
  - Local LLM reasoning via the official `OllamaChatClient` connector.
  - Middleware pipelining intercepting execution context.
  - Interactive console input prompts for human approvals.
  - File-based session persistence and JSONL structured log recording.
  - Automated tests checking agent responses and file outputs.
* **Mocked**:
  - **System Integrations**: Actions (folder permission provisioning, expense payouts) are simulated via printouts rather than real AD/ERP APIs.

---

## 5. Folder Structure
```
my_end_to_end_project/
├── .env.example
├── .gitignore
├── requirements.txt
├── README.md
├── architecture.md
├── run.py                 # Core CLI entry point
├── test_project.py        # Automated test suite (with self-contained offline tests)
├── src/
│   ├── __init__.py
│   ├── config.py          # Environment configuration loader
│   ├── agents/
│   │   ├── __init__.py
│   │   ├── base_demo_agent.py   # General agent wrapper (injects OllamaChatClient)
│   │   ├── coordinator.py       # Coordinator/Router Agent
│   │   ├── policy.py            # Policy Retrieval Agent
│   │   ├── compliance.py        # Compliance/Guardrail Agent
│   │   ├── approval.py          # Approval Gate Agent
│   │   └── action.py            # Action/Execution Agent
│   ├── middleware/
│   │   ├── __init__.py
│   │   └── audit_middleware.py  # Structured logging & PII redaction middleware
│   ├── memory/
│   │   ├── __init__.py
│   │   └── file_store.py        # Thread-based conversation history store
│   └── utils/
│       ├── __init__.py
│       └── logger.py            # Custom ANSI colored stdout logger
└── data/
    ├── policies.json            # Hardcoded policy knowledge base
    └── audit_trail.jsonl        # Observability output
```

---

## 6. How to Install
Ensure Python 3.10+ is installed. Run the following in your shell:

```bash
# Create virtual environment
python -m venv .venv

# Activate virtual environment
# On Windows (cmd):
.venv\Scripts\activate
# On Windows (PowerShell):
.venv\Scripts\Activate.ps1
# On macOS/Linux:
source .venv/bin/activate

# Install dependencies
pip install -r requirements.txt
```

---

## 7. How to Configure Environment Variables
Copy `.env.example` to `.env`:
```bash
cp .env.example .env
```
Open `.env` and set your local Ollama details:
* `OLLAMA_HOST=http://localhost:11434` (Host running your local Ollama server)
* `OLLAMA_MODEL=llama3.2` (Specified model pulled via `ollama pull`)

---

## 8. How to Run Locally
Run the interactive CLI application:
```bash
python run.py
```
Try entering queries like:
* `"What is the travel reimbursement policy?"` (Triggers Policy Lookup)
* `"Submit travel reimbursement for $200"` (Under pre-approval limit -> Auto-approved)
* `"Submit travel reimbursement for $600"` (Triggers Approval -> Prompts manager sign-off)
* `"Can I access the finance folder?"` (Restricted -> Prompts manager sign-off)
* `"Request access for admin@corp.com to finance folder"` (Fails Compliance guardrails due to PII email)

Type `clear` to reset memory or `exit` to quit.

---

## 9. How to Launch DevUI
The `agent-framework-devui` package is installed as part of the project dependencies. This package provides a local debugging dashboard and exposes an OpenAI-compatible endpoint.

### Understanding Individual Agents vs. Workflow in DevUI
When you run `devui ./src/agents`:
1. **Individual Agents (e.g. `action`, `compliance`, `coordinator`, etc.)**: DevUI discovers these as standalone agent entities. Interacting with them in the UI runs *only* that specific agent's instructions (e.g. chatting with the coordinator will run only the coordinator's standalone analysis prompt).
2. **Cooperative Mesh Workflow (`MultiAgentMeshWorkflow`)**: To execute the entire coordinated sequential agent loop (routing, compliance scan, policy retrieval, manager approval, action execution, and synthesis), select and run the **`MultiAgentMeshWorkflow`** in the DevUI dashboard. This triggers the complete multi-agent orchestration identically to the `run.py` CLI application.

To launch the DevUI dashboard:
1. Ensure your virtual environment is active.
2. Run the `devui` command, passing the directory containing your agent code:
   ```bash
   devui ./src/agents
   ```
   *Note: This starts the local server (default: `http://127.0.0.1:8080`) and automatically opens it in your default web browser.*
   
   If you run into path resolution issues manually, make sure `PYTHONPATH` includes the project root folder. For example:
   * **PowerShell**: `$env:PYTHONPATH="."; devui ./src/agents`
   * **CMD**: `set PYTHONPATH=. && devui ./src/agents`
   * **Bash/macOS/Linux**: `PYTHONPATH=. devui ./src/agents`
3. To enable OpenTelemetry distributed tracing and monitor execution flows, run with the `--instrumentation` flag:
   ```bash
   devui ./src/agents --instrumentation
   ```

---

## 10. How to Test
Run the automated test suite using `unittest`:
```bash
python -m unittest test_project.py
```

---

## 11. How Memory/Context Works
* **Conversational Thread Continuity**: Handled by `src/memory/file_store.py`. Every query is written under a specific session file `data/conversations/{session_id}.json`.
* **State Passing**: The `run_multi_agent_workflow` reads context from the session file and appends execution results, ensuring that follow-up queries (e.g. "approve it") are context-aware.

---

## 12. Where Approvals Happen
* **Approval Location**: Initiated in `src/agents/approval.py` inside the `ApprovalGateAgent`.
* **Human-in-the-Loop Gate**: If a request is flagged as restricted (from policy check), the client interrupts workflow processing and prompts:
  `>>> Approve this sensitive request? (yes/no): `
  If the operator type `yes`, a mock approval statement is passed downstream; otherwise, execution short-circuits.

---

## 13. How Guardrails Are Enforced
* **Compliance Checks**: The `ComplianceAgent` inspects messages for security limits and PII before any policy action is allowed to proceed.
* **PII Redaction**: The `AuditMiddleware` intercepts input/outputs and redacts SSNs and emails before writing log entries, ensuring log security.

---

## 14. How Observability Works
* **Log File**: Written to `data/audit_trail.jsonl` in JSONL format.
* **Schema**:
  ```json
  {"timestamp": "ISO8601", "session_id": "...", "agent_name": "...", "inputs": [...], "output": "...", "status": "SUCCESS", "latency_ms": 15}
  ```
* **Performance Recording**: Captures request execution time (in milliseconds) at the middleware layer.

---

## 15. Known Limitations
1. **Mock Handoffs**: While the SDK supports the `HandoffBuilder` workflow graph, this demo uses procedural async orchestrations to avoid API compilation errors when running without a real OpenAI API key.
2. **Local Memory Store**: The memory store is file-based and is not transactional. For production, swap it for a database state provider.

---

## 16. Suggested Next Steps
1. **Configure OpenAI / Azure OpenAI**: Set `USE_LLM=True` in `.env` and configure `OPENAI_API_KEY` to witness real semantic reasoning.
2. **Database Integration**: Replace `FileSessionStore` with a SQL/NoSQL memory adapter.
3. **Real API Integrations**: Modify the `ActionAgent` tools to hook into real active directory or ERP endpoints.
