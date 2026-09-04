"""Section 10.2 — MAF Workflow ReAct definition.

Wires the tool catalogue (Sections 4-6), the router (Section 9), and the prompts
(Section 11) into a bounded ReAct loop. The self-correction loop reuses the same
edges: a validator exception surfaces as a tool error message; the agent executor sees
it next iteration and (for retryable errors) re-emits a corrected tool call.

Ported from the LangGraph StateGraph 1:1 — same four nodes, same edges, same guards.
The state travelling between executors IS the AgentState dict, exactly as before, so
guard_tool_call(state, ...), log_invocation(state, ...) and kg_lookup_node(state) are
unchanged.
"""

import asyncio
import json

from agent_framework import Executor, WorkflowBuilder, WorkflowContext, handler

from sql_agent.agent.messages import (
    apply_tool_call_args, assistant_message, is_tool_result, is_user,
    system_message, text_of, tool_calls_of, tool_message, tool_name_of,
    tool_result_text, user_message,
)
from sql_agent.agent.prompts import (
    REACT_SYSTEM_PROMPT,
    REACT_SYSTEM_PROMPT_DYNAMIC_ONLY,
    RESPONSE_SYNTHESIS_PROMPT,
)
from sql_agent.agent.state import AgentState, add_messages
from sql_agent.config import settings
from sql_agent.formatting.audit_logger import log_invocation
from sql_agent.kg.node import kg_lookup_node
from sql_agent.llm import Step, acomplete, complete_with_tools
from sql_agent.logging_config import get_logger
from sql_agent.memory import approved_examples, render_examples_block
from sql_agent.routing.intent_classifier import classify
from sql_agent.routing.tier_router import (
    fixed_tiers_disabled, guard_tool_call, tier_of, tools_for_caller,
)
from sql_agent.validation.exceptions import GraphRecursionError, SQLAgentError

log = get_logger("agent")

MAX_SUPERSTEPS = 25      # was config={"recursion_limit": 25} on graph.invoke().
                         # Passed to WorkflowBuilder(max_iterations=...) -- NATIVE, so
                         # no hand-rolled counter is needed (see _guard_supersteps note).

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
        (m for m in reversed(messages) if is_user(m)), None
    )
    if last_human is None or not text_of(last_human):
        return
    for call in tool_calls or []:
        if (call.get("name") == "analytical_query"
                and isinstance(call.get("args"), dict)
                and call["args"].get("question") != text_of(last_human)):
            log.info("[%s] TOOL arg override | analytical_query.question restored to verbatim "
                     "user turn (agent had paraphrased it) | was=%r",
                     cid, str(call["args"].get("question"))[:120])
            call["args"]["question"] = text_of(last_human)


def _parse_tool_content(content) -> dict | None:
    if isinstance(content, dict):
        return content
    if isinstance(content, str):
        try:
            return json.loads(content)
        except Exception:
            return None
    return None


def _all_tool_results_empty(tool_messages: list) -> bool:
    """True only when EVERY tool result this turn is a parseable envelope with zero
    rows and no computed value — i.e. there is nothing for synthesis to write about.
    An unparseable message is left to the LLM rather than guessed at."""
    if not tool_messages:
        return False
    for m in tool_messages:
        parsed = _parse_tool_content(tool_result_text(m))
        if not isinstance(parsed, dict):
            return False
        if parsed.get("status") == "error":
            continue
        if parsed.get("rows_returned") not in (0, None) or parsed.get("calculated"):
            return False
    return True


async def _synthesize_final_answer(question: str, tool_messages: list, cid: str):
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
        return assistant_message(_NO_DATA_ANSWER)
    data_block = "\n\n".join(
        f"[{tool_name_of(m) or 'tool_result'}]\n{tool_result_text(m)}"
        for m in tool_messages
    ) or "(no data retrieved)"
    prompt = RESPONSE_SYNTHESIS_PROMPT.format(question=question, tool_results=data_block)
    text = await acomplete(Step.SYNTHESIS, prompt)   # log_usage happens inside
    log.info("[%s] SYNTHESIS final answer | %d chars", cid, len(text or ""))
    return assistant_message(text)


def _guard_supersteps(state: AgentState) -> None:
    """Belt-and-braces counter on top of WorkflowBuilder(max_iterations=MAX_SUPERSTEPS).

    The builder ceiling is the real enforcement and is native to MAF. This counter is
    kept for two reasons: it raises GraphRecursionError, the exact type service/api.py
    puts into the error envelope (MAF's own ceiling raises a WorkflowException), and it
    keeps step_count in state where the audit trail and the parity tests can see it.
    Drop it only if you also map MAF's exception onto the same envelope."""
    state["step_count"] = state.get("step_count", 0) + 1
    if state["step_count"] > MAX_SUPERSTEPS:
        raise GraphRecursionError(
            f"Recursion limit of {MAX_SUPERSTEPS} reached without hitting a stop "
            "condition."
        )


class IntentExecutor(Executor):
    """Advisory pre-flight (Component A). Body unchanged from intent_node."""

    @handler
    async def run(self, state: dict, ctx: WorkflowContext[dict]) -> None:
        _guard_supersteps(state)
        cid = state.get("correlation_id") or "-"
        if settings.intent_detection_enabled:
            last_human = next(
                (m for m in reversed(state["messages"]) if is_user(m)), None
            )
            if last_human is not None:
                intent = await classify(text_of(last_human))
                log.info("[%s] INTENT tier=%s domain=%s conf=%.2f | %s", cid,
                         intent.tier, intent.domain, intent.confidence, intent.reason)
                state["intent"] = intent.as_dict()
        await ctx.send_message(state)


class KgLookupExecutor(Executor):
    """kg_lookup — sits between intent and agent so it runs ONCE per turn. The node
    body itself still lives in sql_agent/kg/node.py."""

    @handler
    async def run(self, state: dict, ctx: WorkflowContext[dict]) -> None:
        _guard_supersteps(state)
        state.update(kg_lookup_node(state))
        await ctx.send_message(state)


class AgentExecutor(Executor):
    """agent_node — tool selection, the two recovery retries, and the synthesis hand-off."""

    @handler
    async def run(self, state: dict, ctx: WorkflowContext[dict]) -> None:
        _guard_supersteps(state)
        tools = tools_for_caller(state["caller_agent"], state["auth_scopes"])
        tool_names = [t.name for t in tools]
        messages = state["messages"]
        last_human_idx = max(
            (i for i, m in enumerate(messages) if is_user(m)), default=-1,
        )
        current_turn_messages = messages[last_human_idx + 1:]
        has_tool_result = any(is_tool_result(m) for m in current_turn_messages)
        # LangChain "any" == MAF "required": the model MUST call a tool this step.
        # complete_with_tools wraps this in ToolMode's {"mode": ...} TypedDict.
        tool_choice = "auto" if has_tool_result else "required"
        cid = state.get("correlation_id") or "-"

        examples_block = render_examples_block(approved_examples())
        base_system = (
            REACT_SYSTEM_PROMPT_DYNAMIC_ONLY if fixed_tiers_disabled() else REACT_SYSTEM_PROMPT
        )
        system_content = f"{base_system}\n\n{examples_block}" if examples_block else base_system

        # Trim to the most recent messages so cost/context stays bounded as the
        # conversation grows (architecture §2.3). The store keeps the full
        # history; the model only ever sees the recent window plus the system prompt.
        history = state["messages"][-settings.short_term_max_messages:]
        base_messages = [system_message(system_content)] + history

        async def invoke(msgs):
            """Invoke the LLM, recovering from a provider rejection of a bad tool
            call by retrying once with explicit guidance. If the retry also fails
            (e.g. the model genuinely has no context and keeps trying to clarify
            rather than call a tool), surface a clean SQLAgentError so the API
            layer returns a readable message instead of a raw 400."""
            try:
                return await complete_with_tools(Step.AGENT, msgs, tools, tool_choice)
            except Exception as exc:
                if not _is_tool_call_failure(exc):
                    raise
                log.warning("[%s] AGENT bad tool call rejected | %s | retrying with "
                            "valid tool list", cid, str(exc)[:160])
                if fixed_tiers_disabled():
                    # Only analytical_query is bound; steer the retry to it, not the
                    # removed get_*/find_* tools (which is what triggered this failure).
                    correction = user_message(
                        "Your previous tool call was invalid — you called a tool that does "
                        "not exist in this configuration. The ONLY tool available is "
                        "analytical_query(question). Call it now and pass the user's "
                        "natural-language question as `question` (no SQL, no table/column "
                        "names). Do not call any get_* or find_* tool."
                    )
                else:
                    correction = user_message(
                        "Your previous tool call was invalid (you called a tool that does "
                        "not exist or passed malformed arguments — it may have been removed "
                        "from your tool list). Call ONLY one of these tools, by exact name: "
                        f"{', '.join(tool_names)}. Do NOT default to analytical_query just "
                        "because your first choice failed — if the question is about ONE "
                        "named customer/deal/product's own data, use that entity's specific "
                        "get_* view tool (e.g. get_customer_pricing_recommendations for a "
                        "customer's pricing); analytical_query is only for cross-row "
                        "aggregates no fixed tool covers."
                    )
                try:
                    return await complete_with_tools(
                        Step.AGENT, msgs + [correction], tools, tool_choice)
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
        response = await invoke(base_messages)
        message = response.messages[-1]

        # Some models silently ignore tool_choice="required" and return plain text.
        # If this is a forced (first) step of the turn and no tool was called, inject a
        # correction and retry once — the retry messages are not persisted to state,
        # just used in-flight.
        if not has_tool_result and not tool_calls_of(message):
            log.warning("[%s] AGENT no tool on forced turn | retrying once", cid)
            retry_messages = base_messages + [
                message,
                user_message(
                    "You MUST call one of your available tools before responding. "
                    "Do not answer from memory. Select the most specific tool "
                    "for this request and call it now."
                ),
            ]
            response = await invoke(retry_messages)
            message = response.messages[-1]

        chosen = [c["name"] for c in tool_calls_of(message)]
        if chosen:
            log.info("[%s] AGENT selected | %s", cid,
                     ", ".join(f"{n}[{tier_of(n)}]" for n in chosen))
        elif settings.response_synthesis_enabled and has_tool_result:
            log.info("[%s] AGENT final answer | no further tools -> synthesis step", cid)
            tool_msgs = [m for m in current_turn_messages if is_tool_result(m)]
            question = text_of(messages[last_human_idx]) if last_human_idx >= 0 else ""
            message = await _synthesize_final_answer(question, tool_msgs, cid)
        else:
            log.info("[%s] AGENT final answer | no further tools", cid)

        state["messages"] = add_messages(state["messages"], [message])
        await ctx.send_message(state)


class ToolsExecutor(Executor):
    """tool_node — replaces langgraph.prebuilt.ToolNode.

    ToolNode's contract that this reproduces exactly:
      * dispatch each call in the batch IN PARALLEL (langgraph's ToolNode._func fans a
        batch out through an executor, not one call after another — see docs/maf/
        reference_spike.py and MAF_MIGRATION_PLAN.md §9.1 D1);
      * validate args against the tool's schema BEFORE calling (ToolNode did this via
        Pydantic; a hallucinated/mistyped argument becomes a correctable error message
        rather than reaching the SQL layer — see §9.1 D2);
      * an unknown tool name -> an error TOOL message, not an exception;
      * a tool that RAISES -> an error TOOL message ("Error: <repr>\\n Please fix your
        mistakes."), which is what drives the self-correction loop;
      * a non-str return -> json.dumps(), falling back to str() when unserialisable
        (service/api._parse_tool_content's ast.literal_eval branch depends on that
        fallback still existing).

    guard_tool_call stays OUTSIDE that try/except, as it is today: a circuit-breaker
    trip must abort the turn, not be handed back to the model as a correctable error.
    """

    @handler
    async def run(self, state: dict, ctx: WorkflowContext[dict]) -> None:
        _guard_supersteps(state)
        last = state["messages"][-1]
        tool_calls = tool_calls_of(last)
        cid = state.get("correlation_id") or "-"

        # Enforce that analytical_query receives the user's question VERBATIM (dynamic-only
        # mode). See _enforce_verbatim_question for the why. Mutates tool_calls in place
        # BEFORE dispatch, so the router/generator and the audit log all see the faithful
        # question, not the model's paraphrase.
        _enforce_verbatim_question(state["messages"], tool_calls, cid)
        apply_tool_call_args(last, tool_calls)   # keep the persisted message in sync

        dynamic_in_batch = 0
        for call in tool_calls:
            guard_tool_call(state, call["name"])  # circuit breaker, Section 9.3
            if tier_of(call["name"]) == "full_dynamic":
                dynamic_in_batch += 1

        tools = {t.name: t for t in
                 tools_for_caller(state["caller_agent"], state["auth_scopes"])}
        # PARALLEL, in call order — ToolNode dispatches a multi-call batch through an
        # executor, not one after another. A sequential list comprehension here would
        # silently change turn latency (and DB connection interleaving) whenever the
        # model emits more than one call in a batch. gather preserves result order.
        results = await asyncio.gather(*(_dispatch(tools, c) for c in tool_calls))
        results = list(results)

        log_invocation(state, tool_calls, {"messages": results})  # Section 12.2
        state["messages"] = add_messages(state["messages"], results)
        # Advance the fan-out counters so the circuit breaker bounds the whole turn.
        state["tool_call_count"] = state["tool_call_count"] + len(tool_calls)
        state["dynamic_call_count"] = state["dynamic_call_count"] + dynamic_in_batch
        await ctx.send_message(state)


async def _dispatch(tools: dict, call: dict):
    """Run ONE tool call on a worker thread and envelope the outcome as a TOOL message.

    asyncio.to_thread keeps the tool bodies sync and blocking-safe: they do SQLAlchemy
    I/O and (for analytical_query) the whole generation pipeline. Under the old sync
    FastAPI endpoints they ran in the threadpool; this preserves that exactly.
    """
    name, args, call_id = call["name"], call["args"], call["id"]
    fn = tools.get(name)
    if fn is None:
        # Verbatim INVALID_TOOL_NAME_ERROR_TEMPLATE (LangGraph tool_node.py:108-110): the
        # names are ", "-joined INSIDE literal brackets. f"{list(tools)}" would emit repr
        # quotes ('a', 'b') and change the string the model self-corrects against.
        return tool_message(
            call_id, name,
            f"Error: {name} is not a valid tool, "
            f"try one of [{', '.join(tools)}].")
    try:
        # VALIDATE FIRST. ToolNode ran the args through the tool's Pydantic schema
        # before calling it, so a hallucinated/mistyped argument became a correctable
        # error message rather than reaching the SQL layer. Calling fn.func(**args)
        # directly would skip that.
        validated = fn.input_model(**args).model_dump()
        result = await asyncio.to_thread(fn.func, **validated)
    except Exception as exc:                       # noqa: BLE001 — ToolNode parity
        return tool_message(call_id, name,
                            f"Error: {exc!r}\n Please fix your mistakes.")
    if isinstance(result, str):
        content = result
    else:
        try:
            # ensure_ascii=False matches langchain_core.tools.base._stringify. With the
            # default True, non-ASCII tool data (the eval's non-English variants) would
            # reach the model as \uXXXX escapes — readable, but different text and
            # different token counts than today.
            content = json.dumps(result, ensure_ascii=False)
        except Exception:                          # noqa: BLE001
            content = str(result)
    return tool_message(call_id, name, content)


class FinishExecutor(Executor):
    """The END node, made explicit. Yields the final state as the workflow output."""

    @handler
    async def run(self, state: dict, ctx: WorkflowContext[dict]) -> None:
        await ctx.yield_output(state)


def _after_intent_continues(state: AgentState) -> bool:
    """Route out_of_scope straight to END, but only when enforcement is on. In shadow
    mode (default) every turn proceeds to the agent exactly as before."""
    return not (settings.intent_detection_enforced
                and state.get("intent", {}).get("tier") == "out_of_scope")


def build_sql_agent_workflow():
    intent, kg_lookup = IntentExecutor(id="intent"), KgLookupExecutor(id="kg_lookup")
    agent, tools = AgentExecutor(id="agent"), ToolsExecutor(id="tools")
    finish = FinishExecutor(id="finish")

    # start_executor is a CONSTRUCTOR kwarg -- there is no set_start_executor().
    # max_iterations is MAF's native recursion ceiling (LangGraph's recursion_limit).
    builder = WorkflowBuilder(max_iterations=MAX_SUPERSTEPS, start_executor=intent)
    # intent -> kg_lookup | END        (was add_conditional_edges("intent", _after_intent))
    builder.add_edge(intent, kg_lookup, condition=_after_intent_continues)
    builder.add_edge(intent, finish,
                     condition=lambda s: not _after_intent_continues(s))
    # kg_lookup -> agent
    builder.add_edge(kg_lookup, agent)
    # agent -> tools | END
    builder.add_edge(agent, tools,
                     condition=lambda s: bool(tool_calls_of(s["messages"][-1])))
    builder.add_edge(agent, finish,
                     condition=lambda s: not tool_calls_of(s["messages"][-1]))
    # tools -> agent  (the ReAct back-edge)
    builder.add_edge(tools, agent)
    return builder.build()


# Back-compat alias so eval/ and scripts/ can migrate independently.
build_sql_agent_graph = build_sql_agent_workflow


async def run_turn(workflow, state: AgentState, *, store=None,
                   thread_id: str | None = None) -> AgentState:
    """Run one turn. With a store + thread_id this is the exact equivalent of
    graph.invoke(partial_state, config={"configurable": {"thread_id": ...}}):
    the prior thread state is merged in under LangGraph's channel-update semantics
    (see state.merge_state), the turn runs, and the result is persisted."""
    from sql_agent.agent.state import merge_state

    prior = store.load(thread_id) if (store and thread_id) else None
    merged = merge_state(prior, state)
    merged.setdefault("step_count", 0)

    # Workflow.run(message) -> WorkflowRunResult; get_outputs() returns whatever the
    # terminal executor yielded via ctx.yield_output().
    result = await workflow.run(merged)
    outputs = result.get_outputs()
    final: AgentState = outputs[-1] if outputs else merged

    if store and thread_id:
        store.save(thread_id, final)
    return final
