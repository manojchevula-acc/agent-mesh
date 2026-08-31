"""Section 10.2 — LangGraph ReAct definition.

Wires the tool catalogue (Sections 4-6), the router (Section 9), and the prompts
(Section 11) into a bounded ReAct loop. The self-correction loop reuses the same
edges: a validator exception surfaces as a tool error message; the agent node sees
it next iteration and (for retryable errors) re-emits a corrected tool call.
"""

import json

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langgraph.graph import END, StateGraph
from langgraph.prebuilt import ToolNode

from sql_agent.agent.prompts import (
    REACT_SYSTEM_PROMPT,
    REACT_SYSTEM_PROMPT_DYNAMIC_ONLY,
    RESPONSE_SYNTHESIS_PROMPT,
)
from sql_agent.agent.state import AgentState
from sql_agent.config import settings
from sql_agent.formatting.audit_logger import log_invocation
from sql_agent.kg.node import kg_lookup_node
from sql_agent.llm import Step, get_llm, log_usage
from sql_agent.logging_config import get_logger
from sql_agent.memory import approved_examples, render_examples_block
from sql_agent.routing.intent_classifier import classify
from sql_agent.routing.tier_router import (
    fixed_tiers_disabled,
    guard_tool_call,
    tier_of,
    tools_for_caller,
)
from sql_agent.validation.exceptions import SQLAgentError

log = get_logger("agent")

# Provider-side "you called a tool that wasn't offered / malformed tool call" signals.
# Groq/OpenAI surface these as a 400 BadRequestError with code tool_use_failed; left
# unhandled they abort the whole request. We catch them and feed a correction instead.
_TOOL_FAIL_MARKERS = ("tool_use_failed", "not in request.tools",
                      "failed to call a function", "invalid_request_error")

_NO_DATA_ANSWER = (
    "I couldn't find any data for that — the lookup returned no matching records. "
    "Double-check the customer/deal name or id and I can try again."
)


def _is_tool_call_failure(exc: Exception) -> bool:
    return any(marker in str(exc).lower() for marker in
               (m.lower() for m in _TOOL_FAIL_MARKERS))


def _enforce_verbatim_question(messages, tool_calls, cid: str = "-") -> None:
    """In dynamic-only mode, overwrite analytical_query's ``question`` arg with the VERBATIM
    latest user turn. Mutates ``tool_calls`` in place; no-op unless the fixed tiers are off.

    Why: analytical_query is then the SOLE tool and its whole job is to take the user's
    natural-language question. Reasoning models (gpt-oss) paraphrase or DECOMPOSE it in the
    tool arg — e.g. reducing "new customer like X, can I offer the same price?" to "what price
    for X?" — silently dropping most of the ask before the router/generator ever see it, so
    the pipeline then answers a different question. The system prompt forbids this but the
    model does it anyway, so it is enforced deterministically here.

    Trade-off: this drops the agent's ability to fold a prior-turn pronoun into the question
    ("match THAT price"); single-turn faithfulness is worth far more than that, and the
    entity resolver / memory cover most reference cases. Scoped to dynamic-only mode — in
    full-tier mode analytical_query is a last resort and a focused analytical question is
    legitimate, so the arg is left untouched.
    """
    if not fixed_tiers_disabled():
        return
    last_human = next(
        (m for m in reversed(messages) if isinstance(m, HumanMessage)), None
    )
    if last_human is None or not isinstance(last_human.content, str):
        return
    for call in tool_calls or []:
        if (call.get("name") == "analytical_query"
                and isinstance(call.get("args"), dict)
                and call["args"].get("question") != last_human.content):
            log.info("[%s] TOOL arg override | analytical_query.question restored to verbatim "
                     "user turn (agent had paraphrased it) | was=%r",
                     cid, str(call["args"].get("question"))[:120])
            call["args"]["question"] = last_human.content


def _parse_tool_content(content) -> dict | None:
    if isinstance(content, dict):
        return content
    if isinstance(content, str):
        try:
            return json.loads(content)
        except Exception:
            return None
    return None


def _all_tool_results_empty(tool_messages: list[ToolMessage]) -> bool:
    """True only when EVERY tool result this turn is a parseable envelope with zero
    rows and no computed value — i.e. there is nothing for synthesis to write about.
    An unparseable message is left to the LLM rather than guessed at."""
    if not tool_messages:
        return False
    for m in tool_messages:
        parsed = _parse_tool_content(m.content)
        if not isinstance(parsed, dict):
            return False
        if parsed.get("status") == "error":
            continue
        if parsed.get("rows_returned") not in (0, None) or parsed.get("calculated"):
            return False
    return True


def _synthesize_final_answer(question: str, tool_messages: list[ToolMessage], cid: str) -> AIMessage:
    """Dedicated final-answer step (Step.SYNTHESIS), gated by
    settings.response_synthesis_enabled. Runs on a separate, smaller model with ONLY the
    question and this turn's retrieved data — no tool schemas, no tool-choice rules
    competing for its attention — so it can focus entirely on writing a complete answer.
    Added after gpt-oss-120b, doing double duty as both tool-selector and answer-writer,
    was observed to under-synthesize (e.g. reporting one field when a "full profile" was
    asked for and every field was already sitting in the tool result).

    When every tool result this turn is empty (zero rows, no computed value), the
    "no data found" call is hard-coded here rather than left to the synthesis LLM: a
    smaller model asked to write a full answer from an envelope that still reads
    "status": "success" (just with an empty data array) will happily pad one out
    instead of reporting the miss."""
    if _all_tool_results_empty(tool_messages):
        log.info("[%s] SYNTHESIS skipped | all tool results empty", cid)
        return AIMessage(content=_NO_DATA_ANSWER)
    data_block = "\n\n".join(
        f"[{m.name or 'tool_result'}]\n{m.content}" for m in tool_messages
    ) or "(no data retrieved)"
    prompt = RESPONSE_SYNTHESIS_PROMPT.format(question=question, tool_results=data_block)
    synthesis_llm = get_llm(Step.SYNTHESIS)
    response = synthesis_llm.invoke(prompt)
    log_usage(Step.SYNTHESIS, response)
    log.info("[%s] SYNTHESIS final answer | %d chars", cid, len(response.content or ""))
    return AIMessage(content=response.content)


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
        # In dynamic-only mode (fixed tiers off) swap in the single-tool system prompt so
        # the model isn't steered toward get_*/find_* tools that are no longer bound.
        examples_block = render_examples_block(approved_examples())
        base_system = (
            REACT_SYSTEM_PROMPT_DYNAMIC_ONLY if fixed_tiers_disabled() else REACT_SYSTEM_PROMPT
        )
        system_content = f"{base_system}\n\n{examples_block}" if examples_block else base_system

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
                if fixed_tiers_disabled():
                    # Only analytical_query is bound; steer the retry to it, not the
                    # removed get_*/find_* tools (which is what triggered this failure).
                    correction = HumanMessage(content=(
                        "Your previous tool call was invalid — you called a tool that does "
                        "not exist in this configuration. The ONLY tool available is "
                        "analytical_query(question). Call it now and pass the user's "
                        "natural-language question as `question` (no SQL, no table/column "
                        "names). Do not call any get_* or find_* tool."
                    ))
                else:
                    correction = HumanMessage(content=(
                        "Your previous tool call was invalid (you called a tool that does "
                        "not exist or passed malformed arguments — it may have been removed "
                        "from your tool list). Call ONLY one of these tools, by exact name: "
                        f"{', '.join(tool_names)}. Do NOT default to analytical_query just "
                        "because your first choice failed — if the question is about ONE "
                        "named customer/deal/product's own data, use that entity's specific "
                        "get_* view tool (e.g. get_customer_pricing_recommendations for a "
                        "customer's pricing); analytical_query is only for cross-row "
                        "aggregates no fixed tool covers."
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
        elif settings.response_synthesis_enabled and has_tool_result:
            log.info("[%s] AGENT final answer | no further tools -> synthesis step", cid)
            tool_msgs = [m for m in current_turn_messages if isinstance(m, ToolMessage)]
            question = messages[last_human_idx].content if last_human_idx >= 0 else ""
            response = _synthesize_final_answer(question, tool_msgs, cid)
        else:
            log.info("[%s] AGENT final answer | no further tools", cid)

        return {"messages": [response]}

    def tool_node(state: AgentState):
        last = state["messages"][-1]
        tool_calls = last.tool_calls
        cid = state.get("correlation_id") or "-"
        # Enforce that analytical_query receives the user's question VERBATIM (dynamic-only
        # mode). See _enforce_verbatim_question for the why. Mutates tool_calls in place
        # BEFORE dispatch, so the router/generator and the audit log all see the faithful
        # question, not the model's paraphrase.
        _enforce_verbatim_question(state["messages"], tool_calls, cid)

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
        return "kg_lookup"

    graph = StateGraph(AgentState)
    graph.add_node("intent", intent_node)
    # KG lookup sits between intent and agent so it runs ONCE per turn (not once per
    # dynamic call) and its result is checkpointed before tool selection. It is a no-op
    # returning {} when kg_enabled is false, so the graph shape is unchanged in effect.
    graph.add_node("kg_lookup", kg_lookup_node)
    graph.add_node("agent", agent_node)
    graph.add_node("tools", tool_node)
    graph.set_entry_point("intent")
    graph.add_conditional_edges("intent", _after_intent,
                                {"kg_lookup": "kg_lookup", END: END})
    graph.add_edge("kg_lookup", "agent")
    graph.add_conditional_edges(
        "agent",
        lambda s: "tools" if s["messages"][-1].tool_calls else END,
        {"tools": "tools", END: END},
    )
    graph.add_edge("tools", "agent")
    return graph.compile(checkpointer=checkpointer)
