https://learn.microsoft.com/en-us/agent-framework/workflows/orchestrations/handoff?pivots=programming-language-python#configure-handoff-rules-1


Perfect. Here's the full POC plan tailored specifically to Retail Banking with both customer-facing and internal ops flows, and multi-gate HITL approval.

---

## POC: Retail Banking Handoff Orchestration

### Use Case Overview
A customer contacts the bank. Depending on their issue, they get routed to the right specialist. Sensitive operations (large transfers, loan approvals) require multi-gate human approval — first fraud check, then manager sign-off.

```
Customer / Bank Staff
        ↓
   Triage Agent
   ↙    ↓    ↓    ↘
Account Card  Loan  Fraud
Agent   Agent Agent Investigation
        ↓         ↗
     Transfer   (escalation path)
     Agent ──→ [HITL Gate 1: Fraud Check]
                      ↓
              [HITL Gate 2: Manager Approval]
```

---

## Project Structure

```
retail_bank_handoff/
├── .env
├── requirements.txt
├── tools/
│   ├── account_tools.py        # balance, statement, account ops
│   ├── card_tools.py           # block card, limit change, dispute
│   ├── loan_tools.py           # eligibility, application, status
│   ├── transfer_tools.py       # fund transfer (HITL gate 1 + 2)
│   └── fraud_tools.py          # flag transaction, freeze account
├── agents/
│   └── agent_factory.py        # all agent definitions
├── workflows/
│   └── handoff_workflow.py     # HandoffBuilder + routing rules
├── approvals/
│   └── approval_handler.py     # multi-gate HITL logic
├── main.py                     # entry point (customer + staff mode)
└── checkpoints/                # durable workflow state
```

---

## Phase 1: Tools

### `tools/account_tools.py`
```python
from typing import Annotated
from agent_framework import tool

@tool
def get_account_balance(account_number: Annotated[str, "Customer account number"]) -> str:
    # Simulated — replace with real core banking API call
    return f"Account {account_number} balance: ₹ 1,24,500.00. Available: ₹ 1,20,000.00"

@tool
def get_mini_statement(account_number: Annotated[str, "Account number"]) -> str:
    return (
        f"Last 5 transactions for {account_number}:\n"
        "1. 2026-08-08  UPI/Swiggy          -₹450\n"
        "2. 2026-08-07  NEFT/Salary          +₹85,000\n"
        "3. 2026-08-06  ATM Withdrawal       -₹5,000\n"
        "4. 2026-08-05  UPI/Amazon           -₹1,299\n"
        "5. 2026-08-04  IMPS/Rent            -₹20,000"
    )

@tool
def update_contact_details(
    account_number: Annotated[str, "Account number"],
    mobile: Annotated[str, "New mobile number"],
    email: Annotated[str, "New email address"]
) -> str:
    return f"Contact details updated for account {account_number}. OTP sent to {mobile} for verification."
```

---

### `tools/card_tools.py`
```python
from agent_framework import tool
from typing import Annotated

@tool
def get_card_status(card_last4: Annotated[str, "Last 4 digits of card"]) -> str:
    return f"Card ending {card_last4}: Active. Credit limit ₹2,00,000. Available ₹1,45,000."

@tool
def block_card(card_last4: Annotated[str, "Last 4 digits of card"],
               reason: Annotated[str, "Reason for blocking"]) -> str:
    return f"Card ending {card_last4} has been blocked. Reason: {reason}. New card will arrive in 5-7 days."

@tool
def raise_transaction_dispute(
    card_last4: Annotated[str, "Card last 4 digits"],
    transaction_id: Annotated[str, "Transaction ID to dispute"],
    amount: Annotated[str, "Disputed amount"]
) -> str:
    return f"Dispute raised for ₹{amount} on card {card_last4}. Reference: DISP-{transaction_id[:6].upper()}. Resolution in 7 working days."
```

---

### `tools/loan_tools.py`
```python
from agent_framework import tool
from typing import Annotated

@tool
def check_loan_eligibility(
    account_number: Annotated[str, "Account number"],
    loan_type: Annotated[str, "Type: personal/home/auto"],
    amount: Annotated[str, "Requested loan amount"]
) -> str:
    return (
        f"Eligibility for {loan_type} loan of ₹{amount}:\n"
        "Credit Score: 762 (Good)\n"
        "Pre-approved limit: ₹5,00,000\n"
        "Indicative rate: 10.5% p.a.\n"
        "Status: ELIGIBLE — proceed to application"
    )

@tool
def get_loan_status(loan_id: Annotated[str, "Loan application ID"]) -> str:
    return f"Loan {loan_id}: Under credit review. Expected decision by 2026-08-14."
```

---

### `tools/transfer_tools.py` — Multi-gate HITL
```python
from agent_framework import tool
from typing import Annotated

# Gate 1: Fraud screening (always requires approval)
@tool(approval_mode="always_require")
def fraud_screen_transfer(
    from_account: Annotated[str, "Source account"],
    to_account: Annotated[str, "Destination account"],
    amount: Annotated[str, "Transfer amount in INR"],
    transfer_type: Annotated[str, "NEFT/RTGS/IMPS"]
) -> str:
    return (
        f"Fraud screening complete for ₹{amount} {transfer_type}.\n"
        f"From: {from_account} → To: {to_account}\n"
        "Risk Score: MEDIUM (first-time beneficiary, large amount)\n"
        "Status: PASSED FRAUD SCREEN — awaiting manager approval"
    )

# Gate 2: Manager approval (always requires approval)
@tool(approval_mode="always_require")
def authorize_large_transfer(
    from_account: Annotated[str, "Source account"],
    to_account: Annotated[str, "Destination account"],
    amount: Annotated[str, "Amount"],
    fraud_screen_ref: Annotated[str, "Fraud screen reference ID"]
) -> str:
    return (
        f"Transfer of ₹{amount} AUTHORIZED by manager.\n"
        f"Transaction ref: TXN-{from_account[-4:]}{amount[:3]}\n"
        "Funds will credit within 2 hours (RTGS) or next working day (NEFT)."
    )
```

---

### `tools/fraud_tools.py`
```python
from agent_framework import tool
from typing import Annotated

@tool
def flag_suspicious_transaction(
    account_number: Annotated[str, "Account number"],
    transaction_id: Annotated[str, "Transaction to flag"]
) -> str:
    return f"Transaction {transaction_id} flagged for investigation. Case ID: FRAUD-{transaction_id[:6].upper()} opened."

# Gate: Freezing an account requires human approval
@tool(approval_mode="always_require")
def freeze_account(
    account_number: Annotated[str, "Account to freeze"],
    reason: Annotated[str, "Reason for freeze"]
) -> str:
    return f"Account {account_number} FROZEN. Reason: {reason}. Customer notified via SMS and email."
```

---

## Phase 2: Agents (`agents/agent_factory.py`)

```python
from agent_framework.foundry import FoundryChatClient
from tools.account_tools import get_account_balance, get_mini_statement, update_contact_details
from tools.card_tools import get_card_status, block_card, raise_transaction_dispute
from tools.loan_tools import check_loan_eligibility, get_loan_status
from tools.transfer_tools import fraud_screen_transfer, authorize_large_transfer
from tools.fraud_tools import flag_suspicious_transaction, freeze_account

def create_agents(chat_client: FoundryChatClient):

    triage_agent = chat_client.as_agent(
        name="triage_agent",
        description="First point of contact. Identifies customer intent and routes to specialist.",
        instructions=(
            "You are the first point of contact at a retail bank. "
            "Greet the customer, understand their issue, and route to the correct specialist. "
            "For account queries → account_agent. "
            "For card issues → card_agent. "
            "For loans → loan_agent. "
            "For transfers above ₹50,000 → transfer_agent. "
            "For suspected fraud → fraud_agent. "
            "Never handle specialized requests yourself — always handoff."
        ),
    )

    account_agent = chat_client.as_agent(
        name="account_agent",
        description="Handles account balance, statements, and contact detail updates.",
        instructions=(
            "You are an account specialist. Help customers with balance enquiries, "
            "mini statements, and updating contact details. "
            "If the customer raises a fraud concern, handoff to fraud_agent. "
            "Once resolved, handoff back to triage_agent."
        ),
        tools=[get_account_balance, get_mini_statement, update_contact_details],
    )

    card_agent = chat_client.as_agent(
        name="card_agent",
        description="Handles card blocking, limit changes, and transaction disputes.",
        instructions=(
            "You are a card services specialist. Handle card status checks, blocking lost/stolen cards, "
            "and transaction disputes. "
            "If the customer suspects fraud, handoff to fraud_agent. "
            "Once resolved, handoff back to triage_agent."
        ),
        tools=[get_card_status, block_card, raise_transaction_dispute],
    )

    loan_agent = chat_client.as_agent(
        name="loan_agent",
        description="Handles loan eligibility checks and application status.",
        instructions=(
            "You are a loan specialist. Check eligibility for personal, home, and auto loans. "
            "Track existing loan application status. "
            "Once resolved, handoff back to triage_agent."
        ),
        tools=[check_loan_eligibility, get_loan_status],
    )

    transfer_agent = chat_client.as_agent(
        name="transfer_agent",
        description="Handles large fund transfers requiring fraud screening and manager approval.",
        instructions=(
            "You handle high-value fund transfers (above ₹50,000). "
            "Always run fraud_screen_transfer first. "
            "Only after fraud screen passes, run authorize_large_transfer. "
            "Both steps require human approval — wait for confirmation before proceeding. "
            "If fraud screen fails, handoff to fraud_agent immediately. "
            "Once transfer is complete, handoff back to triage_agent."
        ),
        tools=[fraud_screen_transfer, authorize_large_transfer],
    )

    fraud_agent = chat_client.as_agent(
        name="fraud_agent",
        description="Investigates suspicious transactions and can freeze accounts.",
        instructions=(
            "You are the fraud investigation specialist. "
            "Flag suspicious transactions and freeze accounts if necessary. "
            "Account freeze requires human approval — always wait for authorization. "
            "Coordinate with triage_agent once investigation steps are complete."
        ),
        tools=[flag_suspicious_transaction, freeze_account],
    )

    return triage_agent, account_agent, card_agent, loan_agent, transfer_agent, fraud_agent
```

---

## Phase 3: Handoff Rules (`workflows/handoff_workflow.py`)

```python
from agent_framework.orchestrations import HandoffBuilder
from agent_framework import FileCheckpointStorage

def build_workflow(triage, account, card, loan, transfer, fraud, use_checkpoints=False):

    kwargs = dict(
        name="retail_bank_handoff",
        participants=[triage, account, card, loan, transfer, fraud],
        termination_condition=lambda conv: (
            len(conv) > 0 and any(
                phrase in conv[-1].text.lower()
                for phrase in ["is there anything else", "thank you for banking", "have a great day"]
            )
        ),
    )

    if use_checkpoints:
        kwargs["checkpoint_storage"] = FileCheckpointStorage("./checkpoints")

    return (
        HandoffBuilder(**kwargs)
        .with_start_agent(triage)

        # Triage routing — cannot touch transfers or fraud directly
        .add_handoff(triage, [account, card, loan, transfer, fraud])

        # Account agent — can escalate to fraud, return to triage
        .add_handoff(account, [fraud, triage])

        # Card agent — can escalate to fraud, return to triage
        .add_handoff(card, [fraud, triage])

        # Loan agent — straightforward, returns to triage
        .add_handoff(loan, [triage])

        # Transfer agent — escalates to fraud if screen fails, else returns to triage
        .add_handoff(transfer, [fraud, triage])

        # Fraud agent — returns to triage after investigation
        .add_handoff(fraud, [triage])

        .build()
    )
```

---

## Phase 4: Multi-Gate HITL Handler (`approvals/approval_handler.py`)

```python
from agent_framework import Content
from agent_framework.orchestrations import HandoffAgentUserRequest

APPROVAL_GATES = {
    "fraud_screen_transfer": {
        "gate": 1,
        "role": "Fraud Analyst",
        "description": "GATE 1 — Fraud Screen Review",
    },
    "authorize_large_transfer": {
        "gate": 2,
        "role": "Branch Manager",
        "description": "GATE 2 — Manager Authorization",
    },
    "freeze_account": {
        "gate": 1,
        "role": "Fraud Manager",
        "description": "GATE 1 — Account Freeze Authorization",
    },
}

def handle_approval_request(req) -> object:
    func = req.data.function_call
    args = func.parse_arguments() or {}
    gate_info = APPROVAL_GATES.get(func.name, {"description": func.name, "role": "Approver", "gate": 1})

    print(f"\n{'='*50}")
    print(f"⚠️  {gate_info['description']}")
    print(f"   Requires: {gate_info['role']}")
    print(f"   Operation: {func.name}")
    print(f"   Parameters:")
    for k, v in args.items():
        print(f"     {k}: {v}")
    print(f"{'='*50}")

    decision = input(f"[{gate_info['role']}] Approve? (y/n): ").strip().lower()
    approved = decision == "y"

    if not approved:
        reason = input("Rejection reason: ").strip()
        print(f"❌ {gate_info['description']} REJECTED — {reason}")
    else:
        print(f"✅ {gate_info['description']} APPROVED")

    return req.data.to_function_approval_response(approved=approved)

def handle_user_input_request(req) -> object:
    print(f"\n[{req.executor_id}]:")
    for msg in req.data.agent_response.messages[-2:]:
        print(f"  {msg.author_name}: {msg.text}")
    user_input = input("You: ").strip()
    return HandoffAgentUserRequest.create_response(user_input)
```

---

## Phase 5: Main Entry Point (`main.py`)

```python
import asyncio, os
from dotenv import load_dotenv
from azure.identity import AzureCliCredential
from agent_framework.foundry import FoundryChatClient
from agent_framework import Content
from agent_framework.orchestrations import HandoffAgentUserRequest
from agents.agent_factory import create_agents
from workflows.handoff_workflow import build_workflow
from approvals.approval_handler import handle_approval_request, handle_user_input_request

load_dotenv()

async def main():
    mode = input("Mode? (customer/staff): ").strip().lower()
    use_checkpoints = mode == "staff"  # staff flows are long-running, need durability

    chat_client = FoundryChatClient(
        project_endpoint=os.environ["FOUNDRY_PROJECT_ENDPOINT"],
        model=os.environ["FOUNDRY_MODEL"],
        credential=AzureCliCredential(),
    )

    agents = create_agents(chat_client)
    triage, account, card, loan, transfer, fraud = agents
    workflow = build_workflow(*agents, use_checkpoints=use_checkpoints)

    print("\n--- Retail Bank Support ---")
    user_input = input("You: ").strip()

    pending_requests = []
    async for event in workflow.run_stream(user_input):
        if event.type == "request_info":
            pending_requests.append(event)
        elif event.type == "output":
            print("\n✅ Workflow complete.")

    while pending_requests:
        responses = {}
        for req in pending_requests:
            if isinstance(req.data, HandoffAgentUserRequest):
                responses[req.request_id] = handle_user_input_request(req)
            elif isinstance(req.data, Content) and req.data.type == "function_approval_request":
                responses[req.request_id] = handle_approval_request(req)

        pending_requests = []
        async for event in workflow.run(responses=responses):
            if event.type == "request_info":
                pending_requests.append(event)
            elif event.type == "output":
                print("\n✅ Workflow complete.")

asyncio.run(main())
```

---

## Validation Scenarios

| Scenario | Path | Gates |
|---|---|---|
| Balance enquiry | triage → account → triage | None |
| Block lost card | triage → card → triage | None |
| Card fraud dispute | triage → card → fraud → triage | Freeze approval |
| Personal loan check | triage → loan → triage | None |
| Large RTGS transfer | triage → transfer → triage | Gate 1 (fraud) + Gate 2 (manager) |
| Suspicious transaction | triage → account → fraud → triage | Flag + Freeze approval |
| Transfer fraud detected | triage → transfer → fraud → triage | Gate 1 fails → fraud escalation |

---

## Phased Delivery

| Phase | Deliverable | Effort |
|---|---|---|
| 1 | Tools (simulated) + agent factory | Day 1 |
| 2 | HandoffBuilder + routing rules | Day 1 |
| 3 | Basic interactive loop (no HITL) | Day 2 |
| 4 | Multi-gate HITL approval handler | Day 2-3 |
| 5 | Checkpointing for staff flows | Day 3 |
| 6 | All 7 validation scenarios tested | Day 4 |
| 7 | Swap simulated tools for real API calls | Post-POC |

---

The two things to nail for this POC are the **restricted routing** (transfer agent cannot be reached except through triage, fraud agent can be escalated to from multiple paths) and the **two-gate approval sequence** in `transfer_agent` — fraud screen must pass before manager authorization is even attempted. Everything else is straightforward once those two are solid.