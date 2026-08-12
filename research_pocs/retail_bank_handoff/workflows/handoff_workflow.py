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

    TWO MODES — swap by commenting/uncommenting the return block at the bottom:

    1. RESTRICTED GRAPH (commented out):
       add_handoff() narrows which targets each agent can reach.
         triage   -> all specialists
         account  -> fraud (escalation) | triage (done / OOS re-route)
         card     -> fraud (escalation) | triage (done / OOS re-route)
         loan     -> triage (done / OOS re-route)
         transfer -> fraud (screen failed) | triage (done / OOS re-route)
         fraud    -> triage (done / OOS re-route)
       Pro: enforced routing, audit-friendly, wrong routes raise ValueError.
       Con: OOS topic needs 2 hops — specialist -> triage -> target specialist.

    2. FULLY OPEN MESH (active):
       No add_handoff() calls — every agent gets handoff_to_<X> tools for all
       other participants. Specialists can route directly to each other in 1 hop.
       Pro: fewer hops, simpler graph.
       Con: LLM decides routing freely — can make mistakes; no enforced guardrails.

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

    # -------------------------------------------------------------------------
    # MODE 1: RESTRICTED GRAPH (commented out)
    # Each agent is limited to a specific set of handoff targets.
    # -------------------------------------------------------------------------
    # return (
    #     builder
    #     .with_start_agent(triage)
    #     .add_handoff(triage, [account, card, loan, transfer, fraud])
    #     .add_handoff(account, [fraud, triage])
    #     .add_handoff(card, [fraud, triage])
    #     .add_handoff(loan, [triage])
    #     .add_handoff(transfer, [fraud, triage])
    #     .add_handoff(fraud, [triage])
    #     .build()
    # )

    # -------------------------------------------------------------------------
    # MODE 2: FULLY OPEN MESH (active)
    # No add_handoff() calls: every agent can hand off to every other agent.
    # Each specialist gets handoff_to_<X> tools for all 5 peers automatically.
    # OOS topics resolve in 1 hop (e.g. account_agent -> loan_agent directly).
    # NOTE: agent instructions guide routing intent, but there are no hard graph
    # constraints — the LLM picks the target freely.
    # -------------------------------------------------------------------------
    return (
        builder
        .with_start_agent(triage)
        .build()
    )
