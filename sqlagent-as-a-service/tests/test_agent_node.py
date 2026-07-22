"""Agent-node tests — the verbatim-question enforcement for dynamic-only mode.

Guards the fix for reasoning models (gpt-oss) paraphrasing/decomposing the user's question
in the analytical_query tool arg, which silently dropped most of the ask before the
router/generator saw it (see graph._enforce_verbatim_question).
"""

import pytest
from langchain_core.messages import AIMessage, HumanMessage

from sql_agent.agent.graph import _enforce_verbatim_question
from sql_agent.config import settings

USER_Q = (
    "I have a new customer, Mira Electronics Trading, whose profile is very similar to our "
    "existing customer Prime Tech Solutions. Can I offer Mira the same price we gave Prime Tech?"
)


def _tool_calls(question: str):
    """A single analytical_query tool call carrying `question` (shape LangChain emits)."""
    return [{"name": "analytical_query", "args": {"question": question}, "id": "call_1"}]


@pytest.fixture
def _dynamic_only(monkeypatch):
    monkeypatch.setattr(settings, "parameterised_tools_enabled", False)
    monkeypatch.setattr(settings, "semi_dynamic_tools_enabled", False)


def test_paraphrased_question_restored_to_verbatim(_dynamic_only):
    messages = [HumanMessage(content=USER_Q)]
    calls = _tool_calls("What price was offered to the customer Prime Tech Solutions?")
    _enforce_verbatim_question(messages, calls)
    assert calls[0]["args"]["question"] == USER_Q  # paraphrase overwritten


def test_faithful_question_left_unchanged(_dynamic_only):
    messages = [HumanMessage(content=USER_Q)]
    calls = _tool_calls(USER_Q)  # agent already passed it verbatim
    _enforce_verbatim_question(messages, calls)
    assert calls[0]["args"]["question"] == USER_Q


def test_no_override_when_fixed_tiers_on(monkeypatch):
    # A fixed tier is on -> analytical_query is a legitimate last-resort; do not touch its arg.
    monkeypatch.setattr(settings, "parameterised_tools_enabled", True)
    monkeypatch.setattr(settings, "semi_dynamic_tools_enabled", False)
    messages = [HumanMessage(content=USER_Q)]
    calls = _tool_calls("total rwa by product type")
    _enforce_verbatim_question(messages, calls)
    assert calls[0]["args"]["question"] == "total rwa by product type"  # untouched


def test_only_analytical_query_calls_affected(_dynamic_only):
    messages = [HumanMessage(content=USER_Q)]
    calls = [
        {"name": "get_customer_360", "args": {"customer_id": "CUST001"}, "id": "c1"},
        {"name": "analytical_query", "args": {"question": "paraphrased"}, "id": "c2"},
    ]
    _enforce_verbatim_question(messages, calls)
    assert calls[0]["args"] == {"customer_id": "CUST001"}   # other tools untouched
    assert calls[1]["args"]["question"] == USER_Q


def test_uses_latest_human_turn(_dynamic_only):
    # A multi-message history: the MOST RECENT human turn is the one enforced.
    messages = [
        HumanMessage(content="earlier unrelated question"),
        AIMessage(content="some answer"),
        HumanMessage(content=USER_Q),
    ]
    calls = _tool_calls("dropped-most-of-the-ask")
    _enforce_verbatim_question(messages, calls)
    assert calls[0]["args"]["question"] == USER_Q


def test_no_human_message_is_noop(_dynamic_only):
    calls = _tool_calls("whatever the model produced")
    _enforce_verbatim_question([AIMessage(content="no human turn")], calls)
    assert calls[0]["args"]["question"] == "whatever the model produced"
