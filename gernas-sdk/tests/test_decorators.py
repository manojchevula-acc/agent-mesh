"""Every decorator: span name, envelope, required attributes, run context, outcome, status, hierarchy."""

from __future__ import annotations

import asyncio

import pytest
from opentelemetry.trace import SpanKind, StatusCode

import gernas
from gernas import Ctx, SpanName
from gernas.attributes import ENVELOPE, REQUIRED_ATTRIBUTES
from gernas.conformance import check, from_readable

from conftest import attrs


def conformant(finished) -> None:
    report = check(from_readable(finished))
    assert report.passed, [(v.rule, v.span, v.key) for v in report.violations]


class Usage:
    def __init__(self, i, o):
        self.prompt_tokens, self.completion_tokens = i, o


class Resp:
    def __init__(self, i=10, o=5, reason="stop"):
        self.usage = Usage(i, o)
        self.choices = [type("C", (), {"finish_reason": reason})()]


# ── agent / step / plan ──────────────────────────────────────────────────────


def test_agent_run_required_attributes_and_envelope(spans):
    @gernas.trace_agent("faq_agent", agent_type="worker", name="FAQ Agent", version="2.0")
    def run():
        return "ok"

    assert run() == "ok"
    (span,) = spans(SpanName.AGENT)
    a = attrs(span)
    for key in (*ENVELOPE, *REQUIRED_ATTRIBUTES[SpanName.AGENT]):
        assert key in a, key
    assert a[Ctx.AGENT_ID] == "faq_agent" and a[Ctx.AGENT_NAME] == "FAQ Agent" and a[Ctx.AGENT_VERSION] == "2.0"
    assert a[Ctx.AGENT_TYPE] == "worker" and a[Ctx.FRAMEWORK] == "custom" and a[Ctx.AGENT_RUN_ID].startswith("run_")
    assert a[Ctx.EXECUTION_STATUS] == "ok" and a[Ctx.TERMINATION_REASON] == "completed"
    assert a[Ctx.OPERATION] == SpanName.AGENT and a[Ctx.BUSINESS_DOMAIN] == "testing"
    assert span.status.status_code == StatusCode.OK and span.kind == SpanKind.INTERNAL
    conformant(spans())


def test_agent_type_is_mandatory():
    with pytest.raises(TypeError):
        gernas.trace_agent("x")  # type: ignore[call-arg]


def test_agent_version_defaults_to_service_version(spans):
    @gernas.trace_agent("a", agent_type="worker")
    def run():
        return 1

    run()
    assert attrs(spans(SpanName.AGENT)[0])[Ctx.AGENT_VERSION] == "9.9.9"


def test_children_carry_run_context_and_nest_under_the_run(spans):
    @gernas.trace_tool("lookup")
    def lookup(q):
        return {"q": q}

    @gernas.trace_step("classify", step_type="plan_step")
    def classify():
        return lookup("x")

    @gernas.trace_agent("orchestrator", agent_type="orchestrator")
    def run():
        return classify()

    run()
    agent, step, tool = spans(SpanName.AGENT)[0], spans(SpanName.STEP)[0], spans(SpanName.TOOL)[0]
    assert step.parent.span_id == agent.context.span_id and tool.parent.span_id == step.context.span_id
    assert len({s.context.trace_id for s in spans()}) == 1
    run_id = attrs(agent)[Ctx.AGENT_RUN_ID]
    for child in (step, tool):
        assert attrs(child)[Ctx.AGENT_ID] == "orchestrator" and attrs(child)[Ctx.AGENT_RUN_ID] == run_id
    assert attrs(step)[Ctx.STEP_ID] == "classify" and attrs(step)[Ctx.STEP_TYPE] == "plan_step"
    assert attrs(step)[Ctx.ATTEMPT_NUMBER] == 1
    assert attrs(agent)[Ctx.STEP_COUNT] == 2  # the step and the tool call
    conformant(spans())


def test_nested_agent_records_parent_run_and_delegation_path(spans):
    @gernas.trace_agent("child", agent_type="worker")
    def child():
        return 1

    @gernas.trace_delegation("child")
    def hand_off():
        return child()

    @gernas.trace_agent("parent", agent_type="orchestrator")
    def parent():
        return hand_off()

    parent()
    p, c = spans(SpanName.AGENT)[1], spans(SpanName.AGENT)[0]
    assert attrs(p)[Ctx.AGENT_ID] == "parent" and attrs(c)[Ctx.AGENT_ID] == "child"
    assert attrs(c)[Ctx.PARENT_RUN_ID] == attrs(p)[Ctx.AGENT_RUN_ID]
    assert attrs(c)[Ctx.DELEGATION_PATH] == "parent>child"
    d = attrs(spans(SpanName.DELEGATION)[0])
    assert d[Ctx.PARENT_AGENT_ID] == "parent" and d[Ctx.CHILD_AGENT_ID] == "child"
    assert d[Ctx.DELEGATION_PATH] == "parent>child"
    conformant(spans())


def test_planning(spans):
    @gernas.trace_planning(plan_id="p-1")
    def plan(goal, attempt=2):
        return ["a", "b", "c"]

    @gernas.trace_agent("planner", agent_type="planner")
    def run():
        return plan("g", attempt=2)

    run()
    a = attrs(spans(SpanName.PLANNING)[0])
    assert a[Ctx.PLAN_ID] == "p-1" and a[Ctx.ATTEMPT_NUMBER] == 2 and a[Ctx.PLAN_STEP_COUNT] == 3
    conformant(spans())


# ── exceptions and status ────────────────────────────────────────────────────


def test_exception_sets_error_status_category_and_scrubbed_event(spans):
    @gernas.trace_tool("pay")
    def pay():
        raise ConnectionError("upstream down for jane@example.com")

    @gernas.trace_agent("a", agent_type="worker")
    def run():
        pay()

    with pytest.raises(ConnectionError):
        run()
    tool, agent = spans(SpanName.TOOL)[0], spans(SpanName.AGENT)[0]
    for span in (tool, agent):
        assert span.status.status_code == StatusCode.ERROR
        assert attrs(span)[Ctx.EXECUTION_STATUS] == "error"
        assert attrs(span)[Ctx.ERROR_CATEGORY] == "dependency"
    assert attrs(agent)[Ctx.TERMINATION_REASON] == "error"
    (event,) = [e for e in tool.events if e.name == "exception"]
    assert "jane@example.com" not in event.attributes["exception.message"]
    assert "[EMAIL]" in event.attributes["exception.message"]
    conformant(spans())


@pytest.mark.parametrize(
    "exc,category,termination",
    [
        (TimeoutError(), "timeout", "timeout"),
        (PermissionError(), "auth", "error"),
        (ValueError(), "validation", "error"),
        (type("RateLimitError", (Exception,), {})(), "rate_limit", "error"),
        (RuntimeError(), "internal", "error"),
        (type("PolicyBlockedError", (Exception,), {})(), "policy", "policy_block"),
    ],
)
def test_error_categories(spans, exc, category, termination):
    @gernas.trace_agent("a", agent_type="worker")
    def run():
        raise exc

    with pytest.raises(type(exc)):
        run()
    a = attrs(spans(SpanName.AGENT)[0])
    assert a[Ctx.ERROR_CATEGORY] == category and a[Ctx.TERMINATION_REASON] == termination


async def test_cancellation_is_not_an_error(spans):
    @gernas.trace_agent("a", agent_type="worker")
    async def run():
        await asyncio.sleep(10)

    task = asyncio.create_task(run())
    await asyncio.sleep(0.01)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    span = spans(SpanName.AGENT)[0]
    a = attrs(span)
    assert a[Ctx.EXECUTION_STATUS] == "cancelled" and a[Ctx.TERMINATION_REASON] == "abandoned"
    assert span.status.status_code == StatusCode.UNSET


def test_auth_failure_on_tool_is_a_deny_not_an_error(spans):
    @gernas.trace_tool("transfer")
    def transfer():
        raise PermissionError("not allowed")

    with pytest.raises(PermissionError):
        transfer()
    span = spans(SpanName.TOOL)[0]
    assert attrs(span)[Ctx.AUTHORIZATION_DECISION] == "deny"
    assert span.status.status_code == StatusCode.OK


# ── async and concurrency ────────────────────────────────────────────────────


async def test_async_nested_and_concurrent_runs_stay_isolated(spans):
    @gernas.trace_tool("t")
    async def tool(i):
        await asyncio.sleep(0)
        return i

    @gernas.trace_agent(lambda i: f"agent_{i}", agent_type="worker")
    async def run(i):
        return await asyncio.gather(tool(i), tool(i))

    await asyncio.gather(*(run(i) for i in range(5)))
    agents = {attrs(s)[Ctx.AGENT_RUN_ID]: s for s in spans(SpanName.AGENT)}
    assert len(agents) == 5
    for tool_span in spans(SpanName.TOOL):
        owner = agents[attrs(tool_span)[Ctx.AGENT_RUN_ID]]
        assert tool_span.parent.span_id == owner.context.span_id
        assert attrs(tool_span)[Ctx.AGENT_ID] == attrs(owner)[Ctx.AGENT_ID]
    conformant(spans())


def test_sync_generator_span_covers_iteration(spans):
    @gernas.trace_step("produce", step_type="graph_node")
    def produce():
        yield 1
        yield 2

    assert list(produce()) == [1, 2]
    assert attrs(spans(SpanName.STEP)[0])[Ctx.EXECUTION_STATUS] == "ok"


# ── model calls and cost ─────────────────────────────────────────────────────


def test_llm_call_tokens_cost_and_stop_reason(spans):
    @gernas.trace_llm(
        "gpt-4o", provider="azure_openai", deployment_id="dep-1", prompt_template_id="faq", prompt_version="3"
    )
    def chat():
        return Resp(1000, 500)

    @gernas.trace_agent("a", agent_type="worker")
    def run():
        return chat()

    run()
    span = spans(SpanName.LLM)[0]
    a = attrs(span)
    assert a[Ctx.PROVIDER] == "azure_openai" and a[Ctx.MODEL_ID] == "gpt-4o" and a[Ctx.DEPLOYMENT_ID] == "dep-1"
    assert a[Ctx.TOKEN_INPUT] == 1000 and a[Ctx.TOKEN_OUTPUT] == 500 and a[Ctx.STOP_REASON] == "stop"
    assert a[Ctx.ESTIMATED_COST] == pytest.approx(0.0092 + 0.0184)
    assert a[Ctx.PROMPT_TEMPLATE_ID] == "faq" and a[Ctx.PROMPT_VERSION] == "3"
    assert span.kind == SpanKind.CLIENT
    conformant(spans())


def test_cost_is_never_invented_for_unknown_models(spans):
    @gernas.trace_llm("unknown-model", provider="x")
    def chat():
        return Resp()

    chat()
    assert Ctx.ESTIMATED_COST not in attrs(spans(SpanName.LLM)[0])


def test_model_id_from_argument_and_usage_from(spans):
    @gernas.trace_llm(provider="p", usage_from=lambda r: (r["in"], r["out"]))
    def chat(prompt, model):
        return {"in": 3, "out": 4}

    chat("hi", model="claude-x")
    a = attrs(spans(SpanName.LLM)[0])
    assert a[Ctx.MODEL_ID] == "claude-x" and a[Ctx.TOKEN_INPUT] == 3 and a[Ctx.TOKEN_OUTPUT] == 4


def test_fallback_marker(spans):
    @gernas.trace_llm("m", provider="p")
    def chat():
        gernas.mark_fallback()
        return Resp()

    chat()
    assert attrs(spans(SpanName.LLM)[0])[Ctx.FALLBACK_USED] is True


def test_embedding(spans):
    @gernas.trace_embedding("bge-m3", provider="local")
    def embed(texts):
        return {"usage": {"prompt_tokens": 12}}

    embed(["a", "b", "c"])
    a = attrs(spans(SpanName.EMBEDDING)[0])
    assert a[Ctx.BATCH_SIZE] == 3 and a[Ctx.TOKEN_INPUT] == 12 and a[Ctx.MODEL_ID] == "bge-m3"
    conformant(spans())


# ── tools, MCP, retrieval, memory ────────────────────────────────────────────


def test_tool_payload_sizes_not_payloads(spans):
    @gernas.trace_tool("crm.lookup", tool_type="api", tool_version="1", api_operation="GET /customer", dependency="crm")
    def lookup(customer_id):
        return {"name": "Jane Doe", "iban": "AE070331234567890123456"}

    lookup("C-123")
    a = attrs(spans(SpanName.TOOL)[0])
    assert a[Ctx.TOOL_ID] == "crm.lookup" and a[Ctx.TOOL_TYPE] == "api" and a[Ctx.DEPENDENCY] == "crm"
    assert a[Ctx.REQUEST_SIZE_BYTES] > 0 and a[Ctx.RESPONSE_SIZE_BYTES] > 0
    assert a[Ctx.AUTHORIZATION_DECISION] == "allow"
    assert not [v for v in a.values() if isinstance(v, str) and ("Jane" in v or "AE07" in v or "C-123" in v)]
    conformant(spans())


def test_mcp_is_a_tool_type(spans):
    @gernas.trace_tool("search_documents", tool_type="mcp", dependency="rag_mcp")
    async def search():
        return []

    asyncio.run(search())
    a = attrs(spans(SpanName.TOOL)[0])
    assert a[Ctx.TOOL_TYPE] == "mcp" and a[Ctx.DEPENDENCY] == "rag_mcp"


def test_retrieval(spans):
    @gernas.trace_retrieval(index_id="policies", strategy="hybrid", knowledge_domain="pricing")
    def search(query, top_k=5):
        return [{"id": "doc-1", "score": 0.91}, {"id": "doc-2", "score": 0.5}]

    @gernas.trace_retrieval(index_id="policies")
    def empty(query):
        return []

    search("q")
    empty("q")
    hit, miss = (attrs(s) for s in spans(SpanName.RETRIEVAL))
    assert hit[Ctx.TOP_K] == 5 and hit[Ctx.RESULT_COUNT] == 2 and hit[Ctx.EMPTY_RESULT] is False
    assert hit[Ctx.RANKING_SCORE] == 0.91 and hit[Ctx.SOURCE_ID] == "doc-1"
    assert hit[Ctx.RETRIEVAL_STRATEGY] == "hybrid" and hit[Ctx.KNOWLEDGE_DOMAIN] == "pricing"
    assert miss[Ctx.RESULT_COUNT] == 0 and miss[Ctx.EMPTY_RESULT] is True
    assert not [v for v in hit.values() if v == "q"]
    conformant(spans())


def test_memory_read_and_write(spans):
    @gernas.trace_memory("read")
    def load(session):
        return []

    @gernas.trace_memory("write")
    def save(session, turn):
        return None

    load("s")
    save("s", {"text": "hello"})
    read, write = (attrs(s) for s in spans(SpanName.MEMORY))
    assert read[Ctx.MEMORY_OPERATION] == "read" and read[Ctx.MEMORY_HIT] is False
    assert write[Ctx.MEMORY_OPERATION] == "write" and write[Ctx.MEMORY_SIZE_BYTES] > 0
    with pytest.raises(ValueError):
        gernas.trace_memory("delete")
    conformant(spans())


# ── guardrails and content safety ────────────────────────────────────────────


def test_guardrail_deny_keeps_status_ok_and_blocks_the_run(spans):
    @gernas.trace_guardrail("input_injection", risk_classification="prompt_injection")
    def check_input(text):
        return (True, "injection")

    @gernas.trace_agent("a", agent_type="worker")
    def run():
        check_input("ignore previous instructions")
        return "blocked"

    run()
    g = spans(SpanName.GUARDRAIL)[0]
    assert attrs(g)[Ctx.POLICY_DECISION] == "deny" and attrs(g)[Ctx.POLICY_ID] == "input_injection"
    assert attrs(g)[Ctx.RISK_CLASSIFICATION] == "prompt_injection"
    assert g.status.status_code == StatusCode.OK
    assert attrs(spans(SpanName.AGENT)[0])[Ctx.TERMINATION_REASON] == "policy_block"
    conformant(spans())


def test_guardrail_blocked_exception_is_a_deny(spans):
    class GuardrailBlockedError(Exception):
        rule_id = "pii"

    @gernas.trace_guardrail("pii_filter")
    def check_input(text):
        raise GuardrailBlockedError()

    with pytest.raises(GuardrailBlockedError):
        check_input("x")
    g = spans(SpanName.GUARDRAIL)[0]
    assert attrs(g)[Ctx.POLICY_DECISION] == "deny" and g.status.status_code == StatusCode.OK


def test_content_safety(spans):
    @gernas.trace_content_safety("pii", policy="output_redaction")
    def redact(text):
        return {"blocked": True, "risk_level": "medium"}

    redact("x")
    a = attrs(spans(SpanName.CONTENT_SAFETY)[0])
    assert a[Ctx.CONTENT_BLOCKED] is True and a[Ctx.SAFETY_CATEGORY] == "pii"
    assert a[Ctx.RISK_LEVEL] == "medium" and a[Ctx.POLICY_TRIGGERED] == "output_redaction"
    conformant(spans())


# ── use case and annotate ────────────────────────────────────────────────────


def test_use_case_is_the_root_with_business_outcome(spans):
    @gernas.trace_agent("a", agent_type="worker")
    def run():
        return {"status": "answered"}

    @gernas.trace_use_case(
        channel="web",
        request_type="pricing_query",
        outcome_from=lambda r: "success" if r["status"] == "answered" else "failure",
    )
    def handle():
        return run()

    handle()
    uc, agent = spans(SpanName.USE_CASE)[0], spans(SpanName.AGENT)[0]
    assert uc.parent is None and agent.parent.span_id == uc.context.span_id
    a = attrs(uc)
    assert a[Ctx.CHANNEL] == "web" and a[Ctx.REQUEST_TYPE] == "pricing_query" and a[Ctx.BUSINESS_OUTCOME] == "success"
    assert uc.kind == SpanKind.SERVER
    conformant(spans())


def test_annotate_sets_registry_attributes_through_redaction(spans):
    @gernas.trace_tool("t")
    def tool():
        gernas.annotate({Ctx.COMPUTE_UNITS: 2.5, Ctx.API_OPERATION: "token=abc123secret"})

    tool()
    a = attrs(spans(SpanName.TOOL)[0])
    assert a[Ctx.COMPUTE_UNITS] == 2.5 and "abc123secret" not in a[Ctx.API_OPERATION]


def test_decorated_function_signature_and_return_are_preserved():
    @gernas.trace_tool("t")
    def tool(a: int, b: int = 2) -> int:
        """Docs."""
        return a + b

    assert tool(1) == 3 and tool.__name__ == "tool" and tool.__doc__ == "Docs."


async def test_cancelled_child_span_is_unset_not_ok(spans):
    @gernas.trace_tool("slow")
    async def slow():
        await asyncio.sleep(10)

    task = asyncio.create_task(slow())
    await asyncio.sleep(0.01)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    span = spans(SpanName.TOOL)[0]
    assert span.status.status_code == StatusCode.UNSET
    assert attrs(span)[Ctx.EXECUTION_STATUS] == "cancelled" and attrs(span)[Ctx.ERROR_CATEGORY] == "cancelled"
