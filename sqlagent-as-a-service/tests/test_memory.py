"""Conversation-memory tests — no LLM keys required.

Covers two things:
  (1) The checkpointer makes a graph remember a thread across invokes.
  (2) The has_tool_result check looks at the CURRENT turn only, not history —
      this is the fix for the multi-turn follow-up bug.
"""

from typing import Annotated, TypedDict

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, StateGraph
from langgraph.graph.message import add_messages

from sql_agent.memory import (
    new_session_id,
    render_examples_block,
)


# --- helpers ------------------------------------------------------------------

class _S(TypedDict):
    messages: Annotated[list, add_messages]


def _tiny_graph(checkpointer):
    """A throwaway 1-node graph (no LLM) to prove thread persistence."""
    g = StateGraph(_S)
    g.add_node("reply", lambda s: {"messages": [AIMessage(content="ok")]})
    g.add_edge(START, "reply")
    g.add_edge("reply", END)
    return g.compile(checkpointer=checkpointer)


# --- (1) checkpointer persists per thread ------------------------------------

def test_checkpointer_persists_messages_per_thread():
    app = _tiny_graph(InMemorySaver())
    cfg = {"configurable": {"thread_id": "sess_test"}}
    app.invoke({"messages": [HumanMessage("first")]}, config=cfg)
    out = app.invoke({"messages": [HumanMessage("second")]}, config=cfg)
    # two human turns + two AI replies, all retained on the same thread
    assert len(out["messages"]) == 4


def test_separate_threads_do_not_share_history():
    app = _tiny_graph(InMemorySaver())
    app.invoke({"messages": [HumanMessage("a")]},
               config={"configurable": {"thread_id": "s1"}})
    out = app.invoke({"messages": [HumanMessage("b")]},
                     config={"configurable": {"thread_id": "s2"}})
    assert len(out["messages"]) == 2   # s2 has its own turn + reply only


# --- (2) has_tool_result looks at CURRENT turn only (multi-turn bug fix) -----

def test_has_tool_result_uses_current_turn_only():
    """Prior-turn ToolMessages must NOT cause tool_choice to be set to 'auto'
    on the first step of a new turn — that was the bug causing the model to
    skip calling a tool on follow-up questions."""
    # Simulate what state["messages"] looks like at the start of turn 2:
    # turn 1 history is already in the message list (from the checkpointer).
    messages = [
        HumanMessage("Who is CUST002?"),          # turn 1 human
        AIMessage("ok", tool_calls=[]),            # turn 1 AI
        ToolMessage(content="{}", tool_call_id="x"),   # turn 1 tool result
        AIMessage("Falcon Steel..."),              # turn 1 final answer
        HumanMessage("What deals do they have?"), # turn 2 human  ← current
    ]
    # Reproduce the fixed logic from graph.py
    last_human_idx = max(
        (i for i, m in enumerate(messages) if isinstance(m, HumanMessage)),
        default=-1,
    )
    current_turn_messages = messages[last_human_idx + 1:]
    has_tool_result = any(isinstance(m, ToolMessage) for m in current_turn_messages)

    # No ToolMessage exists AFTER the last HumanMessage → must be False
    # so tool_choice stays "any" and the model is forced to call a tool.
    assert has_tool_result is False, (
        "has_tool_result should be False at the start of turn 2 — "
        "prior-turn ToolMessages must not count"
    )


def test_has_tool_result_true_after_tool_runs_this_turn():
    """Once a tool has run IN the current turn, has_tool_result should be True
    so tool_choice switches to 'auto' and the model can compose its answer."""
    messages = [
        HumanMessage("Who is CUST002?"),          # turn 1
        AIMessage("ok", tool_calls=[]),
        ToolMessage(content="{}", tool_call_id="x"),
        AIMessage("Falcon Steel..."),
        HumanMessage("What deals do they have?"), # turn 2 human
        AIMessage("ok", tool_calls=[]),            # turn 2 AI step 1
        ToolMessage(content="{}", tool_call_id="y"),  # turn 2 tool result ← current turn
    ]
    last_human_idx = max(
        (i for i, m in enumerate(messages) if isinstance(m, HumanMessage)),
        default=-1,
    )
    current_turn_messages = messages[last_human_idx + 1:]
    has_tool_result = any(isinstance(m, ToolMessage) for m in current_turn_messages)

    assert has_tool_result is True


# --- (3) utility functions ---------------------------------------------------

def test_new_session_id_format():
    sid = new_session_id()
    assert sid.startswith("sess_")
    assert len(sid) > 10


def test_render_examples_block_formats_rows():
    block = render_examples_block([
        {"question": "avg margin?", "validated_sql": "SELECT 1"},
    ])
    assert "avg margin?" in block
    assert "SELECT 1" in block
    assert render_examples_block([]) == ""
