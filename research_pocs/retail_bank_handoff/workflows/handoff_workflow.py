from agent_framework.orchestrations import HandoffBuilder
from agent_framework import FileCheckpointStorage


def _termination_condition(conversation) -> bool:
    if not conversation:
        return False
    last_text = getattr(conversation[-1], "text", "") or ""
    farewell_phrases = [
        "have a great day",
        "thank you for banking",
        "is there anything else",
        "goodbye",
        "take care",
    ]
    return any(phrase in last_text.lower() for phrase in farewell_phrases)


def build_workflow(triage, account, card, loan, transfer, fraud, use_checkpoints: bool = False):
    """
    Pure HandoffBuilder mesh -- no orchestrator, no WorkflowBuilder.

    Routing rules (add_handoff restricts which agents each peer can hand off to):
      triage   -> all specialists
      account  -> fraud (escalation) | triage (done)
      card     -> fraud (escalation) | triage (done)
      loan     -> triage (done)
      transfer -> fraud (screen failed) | triage (done)
      fraud    -> triage (done)

    By default all agents can hand off to each other; add_handoff() narrows that.
    HandoffAgentExecutor injects the handoff tools automatically -- we do not
    define them manually.
    """
    builder = HandoffBuilder(
        name="retail_bank_handoff",
        participants=[triage, account, card, loan, transfer, fraud],
        termination_condition=_termination_condition,
    )

    if use_checkpoints:
        builder = HandoffBuilder(
            name="retail_bank_handoff",
            participants=[triage, account, card, loan, transfer, fraud],
            termination_condition=_termination_condition,
            checkpoint_storage=FileCheckpointStorage("./checkpoints"),
        )

    return (
        builder
        .with_start_agent(triage)
        .add_handoff(triage, [account, card, loan, transfer, fraud])
        .add_handoff(account, [fraud, triage])
        .add_handoff(card, [fraud, triage])
        .add_handoff(loan, [triage])
        .add_handoff(transfer, [fraud, triage])
        .add_handoff(fraud, [triage])
        .build()
    )
