from agent_framework.orchestrations import HandoffBuilder
from agent_framework import FileCheckpointStorage


def _termination_condition(conversation) -> bool:
    if not conversation:
        return False
    last_msg = conversation[-1]
    # Only triage_agent should close the session — specialists must never trigger termination
    if getattr(last_msg, "author_name", "") != "triage_agent":
        return False
    last_text = getattr(last_msg, "text", "") or ""
    farewell_phrases = [
        "have a great day",
        "have a wonderful day",
        "thank you for banking",
        "goodbye",
        "take care",
    ]
    return any(phrase in last_text.lower() for phrase in farewell_phrases)


def build_workflow(triage, account, card, loan, transfer, fraud, use_checkpoints: bool = False):
    """
    Pure HandoffBuilder mesh -- no orchestrator, no WorkflowBuilder.

     Fully open MAF handoff mesh. No add_handoff() calls means every participant
     receives native handoff_to_<agent> tools for the other participants.

    Triage identifies all requested domains from the shared MAF conversation.
    Each specialist completes its own item, inspects the original request and
    completed tool results to choose the next unresolved specialist, and uses a
    native handoff. Native autonomous mode gives each specialist bounded turns to
    publish its tool result to the shared conversation before handing off. The
    final specialist returns to triage for one consolidated response. Routing and
    context remain entirely in HandoffBuilder; this module adds no custom
    orchestration.

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
        .with_autonomous_mode(
            agents=[triage, account, card, loan, transfer, fraud],
            prompts={
                triage.name: (
                    "Review the shared conversation. If required customer details are still missing, restate the "
                    "single concise request and wait for the customer. If every requested domain has a completed "
                    "tool result, produce the consolidated customer response."
                ),
            },
            turn_limits={
                triage.name: 2,
                account.name: 4,
                card.name: 4,
                loan.name: 4,
                transfer.name: 8,
                fraud.name: 5,
            },
        )
        .build()
    )
