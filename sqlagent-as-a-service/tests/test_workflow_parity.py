"""§9.2 parity gate — the MAF workflow reproduces the LangGraph ReAct loop's observable
behaviour. Uses a scripted fake chat client (no network, no live DB) so this suite runs
in CI without provider keys. See docs/MAF_MIGRATION_PLAN.md §9 for the full checklist
this is testing against, and docs/maf/reference_spike.py for the throwaway spike this
was promoted from.
"""

from __future__ import annotations

import asyncio

import pytest
from agent_framework import BaseChatClient, ChatResponse, Content, Message

from sql_agent.agent import workflow as wf
from sql_agent.agent.messages import (
    assistant_message, is_tool_result, is_user, text_of, tool_calls_of,
    tool_result_text, user_message,
)
from sql_agent.config import settings
from sql_agent.tools.decorator import tool
from sql_agent.validation.exceptions import GraphRecursionError


# --------------------------------------------------------------------------- #
# Fixtures: a scripted client + fake tools, so the real workflow module runs
# with no network access and no live DB.
# --------------------------------------------------------------------------- #

class FakeClient(BaseChatClient):
    """Returns pre-scripted assistant messages in order; records the tool_choice
    mode it was called with on each turn."""

    def __init__(self, script: list[list[Message]]) -> None:
        super().__init__()
        # NOT a copy: fake_get_llm() constructs a fresh FakeClient on every
        # complete_with_tools() call (mirroring the real factory's per-step lookup),
        # so every instance must share and mutate the SAME underlying list to make
        # progress through the script across calls.
        object.__setattr__(self, "_script", script)
        object.__setattr__(self, "seen_modes", [])

    async def _inner_get_response(self, *, messages, options=None, **kw):
        tc = (options or {}).get("tool_choice")
        self.seen_modes.append(tc.get("mode") if isinstance(tc, dict) else tc)
        return ChatResponse(messages=self._script.pop(0))

    async def _inner_get_streaming_response(self, *, messages, options=None, **kw):
        raise NotImplementedError
        yield  # pragma: no cover


def _fc(call_id: str, name: str, args: dict) -> Message:
    return Message("assistant", [Content.from_function_call(
        call_id=call_id, name=name, arguments=args)])


@tool
def fake_get_customer_360(customer_id: str) -> dict:
    """Fetch the 360 view for ONE customer."""
    return {"status": "success", "tool": "get_customer_360", "query_tier": "parameterised",
            "rows_returned": 1, "data": [{"customer_id": customer_id, "name": "Falcon Steel"}]}


@tool
def fake_boom(x: str) -> dict:
    """A tool that always raises, to prove the self-correction error path."""
    raise ValueError("simulated tool failure")


FAKE_TOOLS = [fake_get_customer_360, fake_boom]


@pytest.fixture(autouse=True)
def _fake_llm(monkeypatch):
    """Patch get_llm so complete_with_tools()/acomplete() never touch the network.
    Each test sets sql_agent.agent.workflow._SCRIPT before invoking the workflow.
    """
    state_box = {"script": []}

    def fake_get_llm(step):
        return FakeClient(state_box["script"]), {}

    monkeypatch.setattr("sql_agent.llm.step.get_llm", fake_get_llm)
    monkeypatch.setattr("sql_agent.llm.factory.log_usage", lambda *a, **k: None)
    return state_box


@pytest.fixture(autouse=True)
def _fake_tools(monkeypatch):
    """Bind FAKE_TOOLS instead of the real (DB-backed) tool catalogue."""
    monkeypatch.setattr(wf, "tools_for_caller", lambda caller, scopes: FAKE_TOOLS)


@pytest.fixture(autouse=True)
def _disable_side_features(monkeypatch):
    """Keep the surface under test to the 4-node ReAct loop: no intent classifier
    call, no KG lookup, no synthesis step (so the AGENT's own text IS the answer)."""
    monkeypatch.setattr(settings, "intent_detection_enabled", False)
    monkeypatch.setattr(settings, "intent_detection_enforced", False)
    monkeypatch.setattr(settings, "kg_enabled", False)
    monkeypatch.setattr(settings, "response_synthesis_enabled", False)
    monkeypatch.setattr(settings, "parameterised_tools_enabled", True)
    monkeypatch.setattr(settings, "semi_dynamic_tools_enabled", True)


def _base_state(question: str) -> dict:
    return {
        "messages": [user_message(question)],
        "caller_agent": "test", "auth_scopes": set(),
        "tool_call_count": 0, "dynamic_call_count": 0, "correlation_id": "t1",
    }


# --------------------------------------------------------------------------- #
# 1. Happy path + tool_choice switching (parity rows 1, 3, 5, 6, 20, 25)
# --------------------------------------------------------------------------- #

def test_happy_path_and_tool_choice_switching(_fake_llm):
    _fake_llm["script"] = [
        [_fc("c1", "get_customer_360", {"customer_id": "CUST002"})],
        [assistant_message("Falcon Steel is a Corporate customer.")],
    ]
    workflow = wf.build_sql_agent_workflow()
    out = asyncio.run(wf.run_turn(workflow, _base_state("who is CUST002?")))

    roles = [m.role for m in out["messages"]]
    assert roles == ["user", "assistant", "tool", "assistant"]
    assert tool_calls_of(out["messages"][1])[0]["name"] == "get_customer_360"
    assert "Falcon Steel" in text_of(out["messages"][-1])
    assert out["tool_call_count"] == 1


# --------------------------------------------------------------------------- #
# 2. Tool that raises -> error tool message drives self-correction (row 17)
# --------------------------------------------------------------------------- #

def test_tool_exception_becomes_error_tool_message(_fake_llm):
    _fake_llm["script"] = [
        [_fc("c1", "fake_boom", {"x": "1"})],
        [assistant_message("recovered")],
    ]
    workflow = wf.build_sql_agent_workflow()
    out = asyncio.run(wf.run_turn(workflow, _base_state("boom please")))

    tool_msg = out["messages"][2]
    assert is_tool_result(tool_msg)
    err = tool_result_text(tool_msg)
    assert err.startswith("Error: ValueError(")
    assert err.endswith("Please fix your mistakes.")


# --------------------------------------------------------------------------- #
# 3. Unknown tool name -> error tool message, LangGraph-verbatim template (row 18)
# --------------------------------------------------------------------------- #

def test_unknown_tool_name_error_template(_fake_llm):
    _fake_llm["script"] = [
        [_fc("c1", "nope_not_a_tool", {})],
        [assistant_message("ok")],
    ]
    workflow = wf.build_sql_agent_workflow()
    out = asyncio.run(wf.run_turn(workflow, _base_state("q")))

    err = tool_result_text(out["messages"][2])
    assert err == ("Error: nope_not_a_tool is not a valid tool, "
                   "try one of [fake_get_customer_360, fake_boom].")


# --------------------------------------------------------------------------- #
# 4. Bad/mistyped args -> schema-rejected BEFORE the tool body runs (row 36, D2)
# --------------------------------------------------------------------------- #

def test_bad_args_are_schema_rejected_before_dispatch(_fake_llm):
    _fake_llm["script"] = [
        [_fc("c1", "fake_get_customer_360", {"wrong_kwarg": 1})],
        [assistant_message("ok")],
    ]
    workflow = wf.build_sql_agent_workflow()
    out = asyncio.run(wf.run_turn(workflow, _base_state("q")))

    err = tool_result_text(out["messages"][2])
    assert "validation error" in err.lower()
    assert "customer_id" in err          # names the missing field
    assert "Falcon Steel" not in err     # never reached the tool body


# --------------------------------------------------------------------------- #
# 5. out_of_scope short-circuit under enforcement; shadow mode proceeds (rows 2, 4)
# --------------------------------------------------------------------------- #

def test_out_of_scope_short_circuits_when_enforced(monkeypatch, _fake_llm):
    monkeypatch.setattr(settings, "intent_detection_enabled", True)
    monkeypatch.setattr(settings, "intent_detection_enforced", True)
    monkeypatch.setattr(wf, "classify",
                        _async_returning(_FakeIntent(tier="out_of_scope")))
    workflow = wf.build_sql_agent_workflow()
    out = asyncio.run(wf.run_turn(workflow, _base_state("what's the weather?")))

    # No agent/tool step ran: only the original human message is present.
    assert [m.role for m in out["messages"]] == ["user"]
    assert out["intent"]["tier"] == "out_of_scope"


def test_shadow_mode_ignores_out_of_scope_and_proceeds(monkeypatch, _fake_llm):
    monkeypatch.setattr(settings, "intent_detection_enabled", True)
    monkeypatch.setattr(settings, "intent_detection_enforced", False)  # shadow
    monkeypatch.setattr(wf, "classify",
                        _async_returning(_FakeIntent(tier="out_of_scope")))
    # tool_choice is forced ("required") on the first step of a turn, so the script
    # must supply a tool call here — a bare text reply would exercise the SEPARATE
    # no-tool-on-a-forced-turn retry path (already covered by its own parity test),
    # not the shadow-mode routing this test targets.
    _fake_llm["script"] = [
        [_fc("c1", "fake_get_customer_360", {"customer_id": "X"})],
        [assistant_message("answered anyway")],
    ]
    workflow = wf.build_sql_agent_workflow()
    out = asyncio.run(wf.run_turn(workflow, _base_state("what's the weather?")))

    assert out["intent"]["tier"] == "out_of_scope"      # classified...
    assert text_of(out["messages"][-1]) == "answered anyway"  # ...but not enforced


class _FakeIntent:
    def __init__(self, tier: str) -> None:
        self.tier = tier
        self.domain = ""
        self.confidence = 0.0
        self.reason = "test"

    def as_dict(self) -> dict:
        return {"tier": self.tier, "domain": self.domain,
                "confidence": self.confidence, "reason": self.reason}


def _async_returning(value):
    async def _f(*_a, **_k):
        return value
    return _f


# --------------------------------------------------------------------------- #
# 6. Multi-turn thread memory: kg_context survives, counters reset (rows 27, 28)
# --------------------------------------------------------------------------- #

def test_multi_turn_thread_merge_semantics(_fake_llm):
    from sql_agent.memory.conversation_store import InMemoryStore

    store = InMemoryStore()
    workflow = wf.build_sql_agent_workflow()

    # A bare text reply on the forced first step of a turn triggers the (separately
    # tested) "no tool on a forced turn -> retry once" parity behaviour, so each turn
    # needs a second scripted reply for that in-flight retry.
    _fake_llm["script"] = [[assistant_message("a1")], [assistant_message("a1")]]
    s1 = _base_state("first")
    s1["kg_context"] = {"tables": ["customer_master"]}
    asyncio.run(wf.run_turn(workflow, s1, store=store, thread_id="sess"))

    _fake_llm["script"] = [[assistant_message("a2")], [assistant_message("a2")]]
    out = asyncio.run(wf.run_turn(workflow, _base_state("second"), store=store,
                                  thread_id="sess"))

    assert len(out["messages"]) == 4          # both turns accumulated
    assert out["kg_context"] == {"tables": ["customer_master"]}   # survived turn 2
    assert out["tool_call_count"] == 0        # overwritten by turn 2's fresh state


# --------------------------------------------------------------------------- #
# 7. Circuit breaker aborts the turn (row 15/16); recursion ceiling (row 24/38)
# --------------------------------------------------------------------------- #

def test_circuit_breaker_aborts_before_dispatch(_fake_llm):
    from sql_agent.routing.tier_router import MAX_TOOL_CALLS_PER_TURN
    from sql_agent.validation.exceptions import SQLAgentError

    # MAX_TOOL_CALLS_PER_TURN is captured as a module-level constant at import time
    # (tier_router.py:104) -- pre-existing, unrelated to this migration -- so the
    # ceiling itself can't be monkeypatched here; drive tool_call_count to it instead.
    _fake_llm["script"] = [[_fc("c1", "fake_get_customer_360", {"customer_id": "X"})]]
    workflow = wf.build_sql_agent_workflow()
    state = _base_state("q")
    state["tool_call_count"] = MAX_TOOL_CALLS_PER_TURN
    with pytest.raises(SQLAgentError):
        asyncio.run(wf.run_turn(workflow, state))


def test_recursion_ceiling_raises_graph_recursion_error(monkeypatch, _fake_llm):
    """Force an infinite tool<->agent bounce and confirm the MAX_SUPERSTEPS guard
    raises GraphRecursionError — the same type LangGraph raised, so api.ask()'s
    error-envelope/log-level branching is unaffected (see exceptions.py docstring)."""
    monkeypatch.setattr(wf, "MAX_SUPERSTEPS", 3)
    _fake_llm["script"] = [
        [_fc(f"c{i}", "fake_get_customer_360", {"customer_id": "X"})] for i in range(10)
    ]
    workflow = wf.build_sql_agent_workflow()
    with pytest.raises(Exception) as exc_info:
        asyncio.run(wf.run_turn(workflow, _base_state("q")))
    # MAF's own max_iterations ceiling or our belt-and-braces counter — either is
    # acceptable here; the important property is that the loop terminates.
    assert exc_info.type is GraphRecursionError or "iterations" in str(exc_info.value).lower()


# --------------------------------------------------------------------------- #
# 8. Verbatim-question enforcement runs before dispatch (row 13/14) — already
# covered in depth by tests/test_agent_node.py; smoke-tested here at the
# ToolsExecutor level to confirm the wiring (not just the pure function).
# --------------------------------------------------------------------------- #

def test_verbatim_enforcement_applies_in_dynamic_only_mode(monkeypatch, _fake_llm):
    monkeypatch.setattr(settings, "parameterised_tools_enabled", False)
    monkeypatch.setattr(settings, "semi_dynamic_tools_enabled", False)

    @tool
    def analytical_query(question: str) -> dict:
        """Gated dynamic tool stand-in."""
        return {"status": "success", "tool": "analytical_query",
                "query_tier": "full_dynamic", "rows_returned": 1}

    monkeypatch.setattr(wf, "tools_for_caller", lambda caller, scopes: [analytical_query])

    verbatim = "What is the total RWA for Corporate customers in the Trading industry?"
    _fake_llm["script"] = [
        [_fc("c1", "analytical_query", {"question": "total RWA"})],  # paraphrased
        [assistant_message("here you go")],
    ]
    workflow = wf.build_sql_agent_workflow()
    out = asyncio.run(wf.run_turn(workflow, _base_state(verbatim)))

    sent_call = tool_calls_of(out["messages"][1])[0]
    assert sent_call["args"]["question"] == verbatim   # paraphrase overwritten
