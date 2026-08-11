from agent_framework import Content
from agent_framework.orchestrations import HandoffAgentUserRequest

# Maps tool name -> gate metadata shown to the human reviewer
APPROVAL_GATES = {
    "fraud_screen_transfer": {
        "gate": 1,
        "role": "Fraud Analyst",
        "label": "GATE 1 -- Fraud Screen Review",
    },
    "authorize_large_transfer": {
        "gate": 2,
        "role": "Branch Manager",
        "label": "GATE 2 -- Manager Authorization",
    },
    "freeze_account": {
        "gate": 1,
        "role": "Fraud Manager",
        "label": "GATE 1 -- Account Freeze Authorization",
    },
}


def handle_approval_request(event) -> object:
    func = event.data.function_call
    args = func.parse_arguments() or {}
    gate = APPROVAL_GATES.get(func.name, {"label": func.name, "role": "Approver", "gate": 1})

    print(f"\n{'=' * 55}")
    print(f"  {gate['label']}")
    print(f"  Requires approval from: {gate['role']}")
    print(f"  Operation: {func.name}")
    print("  Parameters:")
    for k, v in args.items():
        print(f"    {k}: {v}")
    print(f"{'=' * 55}")

    raw = input(f"[{gate['role']}] Approve? (y/n): ").strip().lower()
    approved = raw == "y"

    if not approved:
        reason = input("Rejection reason: ").strip()
        print(f"  REJECTED -- {reason}")
    else:
        print(f"  APPROVED")

    return event.data.to_function_approval_response(approved=approved)


def handle_user_input_request(event) -> object:
    print(f"\n[{event.executor_id}]:")
    for msg in event.data.agent_response.messages[-2:]:
        text = getattr(msg, "text", "") or ""
        if text:
            print(f"  {msg.author_name}: {text}")
    user_input = input("You: ").strip()
    return HandoffAgentUserRequest.create_response(user_input)
