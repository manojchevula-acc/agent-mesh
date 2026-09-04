"""Message adapter — the one place that knows MAF's Message/Content model.

Everything downstream (service/api.py, kg/node.py, eval/, scripts/) speaks in these
helpers instead of isinstance checks against framework classes. Tool calls are
surfaced in the {"name", "args", "id"} shape LangChain used, because the agent's
guards (_enforce_verbatim_question) and the audit logger (log_invocation) are written
against that shape and their logic is not in scope for this migration.
"""

from __future__ import annotations

import json
from typing import Any

from agent_framework import Content, Message

# --- constructors -------------------------------------------------------------

def user_message(text: str) -> Message:
    return Message("user", [Content.from_text(text)])


def assistant_message(text: str) -> Message:
    return Message("assistant", [Content.from_text(text)])


def system_message(text: str) -> Message:
    return Message("system", [Content.from_text(text)])


def tool_message(call_id: str, name: str, content: str) -> Message:
    """A tool RESULT message. `author_name` carries the tool name so the service
    layer and the synthesis prompt can label the block (LangChain put it on
    ToolMessage.name)."""
    return Message("tool",
                   [Content.from_function_result(call_id=call_id, result=content)],
                   author_name=name)


# --- predicates (replace isinstance(m, HumanMessage) etc.) --------------------

# MAF roles are plain strings, and Content is ONE tagged class discriminated by
# `.type` -- there are no separate TextContent/FunctionCallContent classes.

def is_user(m: Message) -> bool:
    return m.role == "user"


def is_assistant(m: Message) -> bool:
    return m.role == "assistant"


def is_tool_result(m: Message) -> bool:
    return m.role == "tool"


# --- accessors ----------------------------------------------------------------

def text_of(m: Message) -> str:
    """The plain-text part of a message ('' when there is none). Message.text
    already concatenates the text contents, so this is just a None guard."""
    return m.text or ""


def tool_calls_of(m: Message) -> list[dict[str, Any]]:
    """Tool calls in the LangChain dict shape: [{"name", "args", "id"}].

    NOTE: the returned dicts are NOT views onto the function_call Content. Callers
    that mutate args (see graph guard _enforce_verbatim_question) must write the
    result back with apply_tool_call_args().
    """
    calls: list[dict[str, Any]] = []
    for c in m.contents:
        if c.type == "function_call":
            args = c.arguments
            if isinstance(args, str):
                try:
                    args = json.loads(args) if args.strip() else {}
                except Exception:          # malformed args -> let dispatch fail loudly
                    args = {}
            calls.append({"name": c.name, "args": dict(args or {}), "id": c.call_id})
    return calls


def apply_tool_call_args(m: Message, calls: list[dict[str, Any]]) -> None:
    """Push (possibly mutated) args back onto the assistant message, so the message
    the checkpointer/store persists matches what was actually executed. This is the
    MAF analogue of LangChain's in-place mutation of AIMessage.tool_calls."""
    by_id = {c["id"]: c for c in calls}
    for content in m.contents:
        if content.type == "function_call" and content.call_id in by_id:
            content.arguments = by_id[content.call_id]["args"]


def tool_name_of(m: Message) -> str | None:
    return m.author_name


def tool_call_id_of(m: Message) -> str | None:
    for c in m.contents:
        if c.type == "function_result":
            return c.call_id
    return None


def tool_result_text(m: Message) -> str:
    """The raw tool-result payload as a string (JSON, in this codebase)."""
    for c in m.contents:
        if c.type == "function_result":
            r = c.result
            return r if isinstance(r, str) else json.dumps(r, default=str)
    return ""
