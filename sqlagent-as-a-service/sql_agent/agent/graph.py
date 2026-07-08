"""Section 10.2 — LangGraph ReAct definition.

Wires the tool catalogue (Sections 4-6), the router (Section 9), and the prompts
(Section 11) into a bounded ReAct loop. The self-correction loop reuses the same
edges: a validator exception surfaces as a tool error message; the agent node sees
it next iteration and (for retryable errors) re-emits a corrected tool call.
"""

from langchain_core.messages import HumanMessage, ToolMessage
from langgraph.graph import END, StateGraph
from langgraph.prebuilt import ToolNode

from sql_agent.agent.prompts import REACT_SYSTEM_PROMPT
from sql_agent.agent.state import AgentState
from sql_agent.config import settings
from sql_agent.formatting.audit_logger import log_invocation
from sql_agent.llm import Step, get_llm, log_usage
from sql_agent.logging_config import get_logger
from sql_agent.memory import approved_examples, render_examples_block
from sql_agent.routing.intent_classifier import classify
from sql_agent.routing.tier_router import guard_tool_call, tier_of, tools_for_caller
from sql_agent.validation.exceptions import SQLAgentError

log = get_logger("agent")

# Provider-side "you called a tool that wasn't offered / malformed tool call" signals.
# Groq/OpenAI surface these as a 400 BadRequestError with code tool_use_failed; left
# unhandled they abort the whole request. We catch them and feed a correction instead.
_TOOL_FAIL_MARKERS = ("tool_use_failed", "not in request.tools",
                      "failed to call a function", "invalid_request_error")


def _is_tool_call_failure(exc: Exception) -> bool:
    return any(marker in str(exc).lower() for marker in
               (m.lower() for m in _TOOL_FAIL_MARKERS))


def build_sql_agent_graph(llm=None, checkpointer=None):
    # Default to the model configured for the AGENT step; callers may still inject one.
    llm = llm or get_llm(Step.AGENT)

    def intent_node(state: AgentState):
        """Advisory pre-flight (Component A). Classifies the latest user turn and stores
        the result in state. Shadow-first: it never changes behaviour unless
        intent_detection_enabled is set; the out_of_scope short-circuit only engages when
        intent_detection_enforced is also set (handled by the conditional edge below)."""
        cid = state.get("correlation_id") or "-"
        if not settings.intent_detection_enabled:
            return {}
        last_human = next(
            (m for m in reversed(state["messages"]) if isinstance(m, HumanMessage)), None
        )
        if last_human is None:
            return {}
        intent = classify(last_human.content)
        log.info("[%s] INTENT tier=%s domain=%s conf=%.2f | %s", cid, intent.tier,
                 intent.domain, intent.confidence, intent.reason)
        return {"intent": intent.as_dict()}

    def agent_node(state: AgentState):
        tools = tools_for_caller(state["caller_agent"], state["auth_scopes"])
        tool_names = [t.name for t in tools]
        messages = state["messages"]
        last_human_idx = max(
            (i for i, m in enumerate(messages) if isinstance(m, HumanMessage)),
            default=-1,
        )
        current_turn_messages = messages[last_human_idx + 1:]
        has_tool_result = any(isinstance(m, ToolMessage) for m in current_turn_messages)
        tool_choice = "auto" if has_tool_result else "any"
        cid = state.get("correlation_id") or "-"
        llm_with_tools = llm.bind_tools(tools, tool_choice=tool_choice)

        # Ground the agent with approved, curated few-shot examples (from feedback).
        examples_block = render_examples_block(approved_examples())
        system_content = REACT_SYSTEM_PROMPT
        if examples_block:
            system_content = f"{REACT_SYSTEM_PROMPT}\n\n{examples_block}"

        # Trim to the most recent messages so cost/context stays bounded as the
        # conversation grows (architecture §2.3). The checkpointer keeps the full
        # history; the model only ever sees the recent window plus the system prompt.
        history = state["messages"][-settings.short_term_max_messages:]
        base_messages = [{"role": "system", "content": system_content}] + history

        def invoke(messages):
            """Invoke the LLM, recovering from a provider rejection of a bad tool
            call by retrying once with explicit guidance. If the retry also fails
            (e.g. the model genuinely has no context and keeps trying to clarify
            rather than call a tool), surface a clean SQLAgentError so the API
            layer returns a readable message instead of a raw 400."""
            try:
                response = llm_with_tools.invoke(messages)
                log_usage(Step.AGENT, response)
                return response
            except Exception as exc:
                if not _is_tool_call_failure(exc):
                    raise
                log.warning("[%s] AGENT bad tool call rejected | %s | retrying with "
                            "valid tool list", cid, str(exc)[:160])
                correction = HumanMessage(content=(
                    "Your previous tool call was invalid (you called a tool that does "
                    "not exist or passed malformed arguments). Call ONLY one of these "
                    f"tools, by exact name: {', '.join(tool_names)}. Resolve any "
                    "customer/product NAME to its id with the matching get_*_by_name "
                    "tool first; never pass a name where an *_id argument is expected."
                ))
                try:
                    response = llm_with_tools.invoke(messages + [correction])
                    log_usage(Step.AGENT, response)
                    return response
                except Exception as retry_exc:
                    if _is_tool_call_failure(retry_exc):
                        # Both attempts failed — the model cannot determine what to
                        # call (e.g. ambiguous reference like "they" with no context).
                        # Extract the model's clarification text from the error so we
                        # can surface it as a readable response instead of a 500.
                        err_str = str(retry_exc)
                        clarification = ""
                        if "failed_generation" in err_str:
                            import re
                            m = re.search(r"'failed_generation':\s*'([^']*)'", err_str)
                            if m:
                                clarification = m.group(1)
                        raise SQLAgentError(
                            clarification or
                            "I need more context to answer this. Could you clarify "
                            "who or what you are referring to?"
                        ) from retry_exc
                    raise

        log.info("[%s] AGENT thinking | tool_choice=%s | tools=%d", cid, tool_choice, len(tools))
        response = invoke(base_messages)

        # Some models silently ignore tool_choice="any" and return a plain-text answer.
        # If this is a forced (first) step of the turn and no tool was called, inject a
        # correction and retry once — the retry messages are not persisted to state,
        # just used in-flight.
        if not has_tool_result and not getattr(response, "tool_calls", None):
            log.warning("[%s] AGENT no tool on forced turn | retrying once", cid)
            retry_messages = base_messages + [
                response,
                HumanMessage(content=(
                    "You MUST call one of your available tools before responding. "
                    "Do not answer from memory. Select the most specific tool "
                    "for this request and call it now."
                )),
            ]
            response = invoke(retry_messages)

        chosen = [c["name"] for c in getattr(response, "tool_calls", None) or []]
        if chosen:
            log.info("[%s] AGENT selected | %s", cid,
                     ", ".join(f"{n}[{tier_of(n)}]" for n in chosen))
        else:
            log.info("[%s] AGENT final answer | no further tools", cid)

        return {"messages": [response]}

    def tool_node(state: AgentState):
        last = state["messages"][-1]
        tool_calls = last.tool_calls
        dynamic_in_batch = 0
        for call in tool_calls:
            guard_tool_call(state, call["name"])  # circuit breaker, Section 9.3
            if tier_of(call["name"]) == "full_dynamic":
                dynamic_in_batch += 1
        tools = tools_for_caller(state["caller_agent"], state["auth_scopes"])
        result = ToolNode(tools).invoke(state)
        log_invocation(state, tool_calls, result)  # Section 12.2
        # Advance the fan-out counters so the circuit breaker bounds the whole turn.
        result["tool_call_count"] = state["tool_call_count"] + len(tool_calls)
        result["dynamic_call_count"] = state["dynamic_call_count"] + dynamic_in_batch
        return result

    def _after_intent(state: AgentState):
        """Route out_of_scope straight to END, but only when enforcement is on. In
        shadow mode (default) every turn proceeds to the agent exactly as before."""
        if (settings.intent_detection_enforced
                and state.get("intent", {}).get("tier") == "out_of_scope"):
            return END
        return "agent"

    graph = StateGraph(AgentState)
    graph.add_node("intent", intent_node)
    graph.add_node("agent", agent_node)
    graph.add_node("tools", tool_node)
    graph.set_entry_point("intent")
    graph.add_conditional_edges("intent", _after_intent, {"agent": "agent", END: END})
    graph.add_conditional_edges(
        "agent",
        lambda s: "tools" if s["messages"][-1].tool_calls else END,
        {"tools": "tools", END: END},
    )
    graph.add_edge("tools", "agent")
    return graph.compile(checkpointer=checkpointer)
