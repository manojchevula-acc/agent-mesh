"""Meta-tool: let the agent ASK the user for a missing required input.

The agent is forced to call a tool on every turn (it must never answer a data
question from memory or from the conversation history — see agent/graph.py). But
sometimes a tool genuinely needs an input the user hasn't given (e.g. a tenor for
compute_recommended_price, or WHICH of several matching entities they mean). Without
an escape hatch the model would either guess (bad) or refuse to call a tool (which the
forced-tool guard turns into an error). ask_clarification IS that escape hatch: asking
the user is itself a tool call, so the grounding rule holds and the turn ends with a
clean question instead of a rejection. It touches no data.
"""

from sql_agent.tools.decorator import tool


@tool
def ask_clarification(question: str) -> dict:
    """Ask the USER ONE concise clarifying question when a REQUIRED input for the
    tool you need is missing or ambiguous — e.g. a missing tenor / amount / currency,
    or which of several matching customers/products/deals they mean. Use this INSTEAD
    of guessing a value, assuming a default, or answering in prose without a tool.
    `question` is the single question to show the user.

    IMPORTANT: this tool resolves NOTHING — it records no id. Before you call it, first
    resolve every entity the user already named. If they named a customer/product/deal
    but a DIFFERENT input is missing (e.g. the tenor), call the matching get_*/get_*_by_name
    tool to establish that entity's id FIRST, then ask only about the input still missing —
    so the follow-up reply can continue without re-asking who/what they meant."""
    return {
        "status": "clarification",
        "tool": "ask_clarification",
        "question": question,
    }
