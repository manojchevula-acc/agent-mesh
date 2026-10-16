"""WP1-CORE-02a..d: behaviour of each of the sixteen decorators."""
from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest
from opentelemetry.trace import StatusCode

import gernas
from gernas import Ctx, SpanName
from gernas.compliance.decorators import GuardrailBlockedError


# ── @trace_agent ─────────────────────────────────────────────────────────────

def test_trace_agent_happy_path(spans):
    @gernas.trace_agent("triage", name="Triage", version="1.0", framework="custom", agent_type="worker")
    def run(query):
        return {"answer": 42}

    assert run("q") == {"answer": 42}
    span = spans(SpanName.AGENT)[0]
    a = span.attributes
    assert a[Ctx.AGENT_ID] == "triage" and a[Ctx.AGENT_NAME] == "Triage" and a[Ctx.AGENT_VERSION] == "1.0"
    assert a[Ctx.FRAMEWORK] == "custom" and a[Ctx.TERMINATION_REASON] == "completed"
    assert a[Ctx.AGENT_RUN_ID].startswith("run_") and len(a[Ctx.INPUT_HASH]) == 64
    assert a[Ctx.USE_CASE_ID] == "UC-TEST" and a[Ctx.BUSINESS_DOMAIN] == "testing"
    assert a[Ctx.APPLICATION_ID] == "gernas-tests-app" and a[Ctx.ENVIRONMENT] == "dev"
    assert a[Ctx.TRACE_ID] == format(span.context.trace_id, "032x")
    assert span.status.status_code == StatusCode.OK


async def test_trace_agent_async_exception_path(spans):
    @gernas.trace_agent("failing")
    async def run():
        raise ValueError("account 12345678901234 missing")

    with pytest.raises(ValueError):
        await run()
    span = spans(SpanName.AGENT)[0]
    assert span.status.status_code == StatusCode.ERROR
    assert span.attributes[Ctx.TERMINATION_REASON] == "error"
    assert span.attributes[Ctx.ERROR_TYPE] == "ValueError"
    event = span.events[0]
    assert event.name == "exception"
    assert "12345678901234" not in event.attributes["exception.message"]  # PII scrubbed


def test_trace_agent_each_run_gets_a_fresh_run_id(spans):
    @gernas.trace_agent("a")
    def run():
        return 1

    run(), run()
    ids = {s.attributes[Ctx.AGENT_RUN_ID] for s in spans(SpanName.AGENT)}
    assert len(ids) == 2


def test_trace_agent_resolves_dynamic_agent_id(spans):
    class Executor:
        name = "compliance"

        @gernas.trace_agent(lambda self, *a, **k: self.name)
        def run(self, msg):
            return msg

    Executor().run("x")
    assert spans(SpanName.AGENT)[0].attributes[Ctx.AGENT_ID] == "compliance"


def test_nested_agents_link_parent_run(spans):
    @gernas.trace_agent("child")
    def child():
        return gernas.current_run().agent_run_id

    @gernas.trace_agent("parent")
    def parent():
        return gernas.current_run().agent_run_id, child()

    parent_id, child_id = parent()
    by_agent = {s.attributes[Ctx.AGENT_ID]: s for s in spans(SpanName.AGENT)}
    assert by_agent["child"].attributes[Ctx.PARENT_RUN_ID] == parent_id
    assert by_agent["child"].attributes[Ctx.AGENT_RUN_ID] == child_id != parent_id
    assert by_agent["child"].attributes[Ctx.DELEGATION_DEPTH] == 1


# ── @trace_llm ───────────────────────────────────────────────────────────────

def test_trace_llm_azure_openai_shape_with_cost(spans):
    @gernas.trace_llm("gpt-4o", provider="azure_openai", deployment_id="paygo-uae")
    def call(prompt):
        return SimpleNamespace(usage=SimpleNamespace(prompt_tokens=1840, completion_tokens=260),
                               choices=[SimpleNamespace(finish_reason="stop")], model="gpt-4o-2024")

    call("hello")
    a = spans(SpanName.LLM)[0].attributes
    assert a[Ctx.MODEL_ID] == "gpt-4o" and a[Ctx.PROVIDER] == "azure_openai" and a[Ctx.DEPLOYMENT_ID] == "paygo-uae"
    assert a[Ctx.INPUT_TOKENS] == 1840 and a[Ctx.OUTPUT_TOKENS] == 260
    assert tuple(a[Ctx.FINISH_REASON]) == ("stop",) and a[Ctx.RESPONSE_MODEL] == "gpt-4o-2024"
    assert a[Ctx.EST_COST_AED] == pytest.approx(1.84 * 0.0092 + 0.26 * 0.0368)
    assert a[Ctx.RATE_CARD_VERSION] == "2026-04-01"
    assert Ctx.PROMPT not in a and Ctx.COMPLETION not in a


async def test_trace_llm_anthropic_shape(spans):
    @gernas.trace_llm("claude-x", provider="anthropic")
    async def call():
        return SimpleNamespace(usage=SimpleNamespace(input_tokens=100, output_tokens=50), stop_reason="end_turn")

    await call()
    a = spans(SpanName.LLM)[0].attributes
    assert (a[Ctx.INPUT_TOKENS], a[Ctx.OUTPUT_TOKENS]) == (100, 50)
    assert tuple(a[Ctx.FINISH_REASON]) == ("end_turn",)
    assert a[Ctx.EST_COST_AED] > 0  # wildcard provider rate


def test_trace_llm_agent_framework_and_dict_shapes(spans):
    @gernas.trace_llm(provider="groq")
    def maf(model):
        return SimpleNamespace(usage_details={"input_token_count": 7, "output_token_count": 3}, finish_reason="stop")

    @gernas.trace_llm("m", provider="groq")
    def raw():
        return {"usage": {"prompt_tokens": 5, "completion_tokens": 2}, "choices": [{"finish_reason": "length"}]}

    maf(model="openai/gpt-oss-20b")
    raw()
    first, second = spans(SpanName.LLM)
    assert first.attributes[Ctx.MODEL_ID] == "openai/gpt-oss-20b"
    assert (first.attributes[Ctx.INPUT_TOKENS], first.attributes[Ctx.OUTPUT_TOKENS]) == (7, 3)
    assert (second.attributes[Ctx.INPUT_TOKENS], tuple(second.attributes[Ctx.FINISH_REASON])) == (5, ("length",))


def test_trace_llm_missing_usage_and_unknown_model_do_not_raise(spans):
    @gernas.trace_llm("not-in-rate-card", provider="x")
    def call():
        return "plain text"

    assert call() == "plain text"
    a = spans(SpanName.LLM)[0].attributes
    assert Ctx.INPUT_TOKENS not in a and Ctx.EST_COST_AED not in a
    assert a[Ctx.GENERATION_LATENCY_MS] >= 0


def test_trace_llm_prompt_provenance(spans):
    @gernas.trace_llm("gpt-4o", provider="azure_openai", prompt_template_id="greeting", prompt_version="2",
                      prompt_arg="prompt")
    def call(prompt):
        return "ok"

    call("Hello v1")
    call("Hello v1 edited in prod")
    first, second = spans(SpanName.LLM)
    for s in (first, second):
        assert s.attributes[Ctx.PROMPT_TEMPLATE_ID] == "greeting" and s.attributes[Ctx.PROMPT_VERSION] == "2"
    assert first.attributes[Ctx.PROMPT_SHA256] != second.attributes[Ctx.PROMPT_SHA256]
    assert "Hello" not in str(first.attributes)


# ── @trace_tool / @trace_mcp ─────────────────────────────────────────────────

def test_trace_tool_happy_path_hashes_io(spans):
    @gernas.trace_tool("lookup", tool_version="3")
    def lookup(customer_id):
        return {"name": "Alice"}

    lookup("C-1")
    span = spans("tool.lookup")[0]
    a = span.attributes
    assert a[Ctx.TOOL_ID] == "lookup" and a[Ctx.TOOL_TYPE] == "function" and a[Ctx.TOOL_VERSION] == "3"
    assert a[Ctx.AUTH_DECISION] == "allow" and len(a[Ctx.INPUT_HASH]) == 64 and len(a[Ctx.OUTPUT_HASH]) == 64
    assert "Alice" not in str(a) and "C-1" not in str(a)


def test_trace_tool_auth_denial_is_an_attribute_not_an_error(spans):
    @gernas.trace_tool("write")
    def write():
        raise PermissionError("not allowed")

    with pytest.raises(PermissionError):
        write()
    span = spans("tool.write")[0]
    assert span.attributes[Ctx.AUTH_DECISION] == "deny"
    assert span.status.status_code != StatusCode.ERROR


def test_trace_tool_exception_path(spans):
    @gernas.trace_tool("flaky")
    async def flaky():
        raise TimeoutError

    with pytest.raises(TimeoutError):
        asyncio.run(flaky())
    span = spans("tool.flaky")[0]
    assert span.status.status_code == StatusCode.ERROR
    assert span.attributes[Ctx.TOOL_FAILURE_REASON] == "TimeoutError"


@pytest.mark.parametrize("operation", ["tool", "resource", "prompt"])
def test_trace_mcp_operations(spans, operation):
    @gernas.trace_mcp("datalayer", operation=operation, transport="streamable_http")
    def call(name, arguments=None):
        return {"content": [{"text": "secret payload"}]}

    call("customer_360", arguments={"id": 1})
    span = spans(SpanName.MCP)[0]
    a = span.attributes
    assert span.name == "mcp.call"  # transport is never in the name
    assert a[Ctx.MCP_SERVER_ID] == "datalayer" and a[Ctx.MCP_OPERATION] == operation
    assert a[Ctx.MCP_TOOL_NAME] == "customer_360" and a[Ctx.MCP_TRANSPORT] == "streamable_http"
    assert "secret payload" not in str(a)


def test_trace_mcp_rejects_unknown_operation():
    with pytest.raises(ValueError):
        gernas.trace_mcp("x", operation="sampling")


# ── retrieval family ─────────────────────────────────────────────────────────

def test_trace_rag_empty_vs_non_empty(spans):
    @gernas.trace_rag(index_id="docs", strategy="hybrid")
    def search(query, top_k=5):
        return [] if query == "nothing" else [{"id": "d1", "score": 0.83}, {"id": "d2", "score": 0.5}]

    search("pricing policy", top_k=2)
    search("nothing")
    full, empty = spans(SpanName.RAG)
    assert full.attributes[Ctx.RESULT_COUNT] == 2 and full.attributes[Ctx.EMPTY_RESULT] is False
    assert full.attributes[Ctx.RANKING_SCORE] == 0.83 and full.attributes[Ctx.SOURCE_ID] == "d1"
    assert full.attributes[Ctx.TOP_K] == 2 and full.attributes[Ctx.RETRIEVAL_STRATEGY] == "hybrid"
    assert empty.attributes[Ctx.EMPTY_RESULT] is True and empty.attributes[Ctx.RESULT_COUNT] == 0
    assert "pricing policy" not in str(full.attributes)
    assert full.attributes[Ctx.QUERY_HASH] != empty.attributes[Ctx.QUERY_HASH]


def test_trace_retrieval_handles_missing_scores_and_tuples(spans):
    @gernas.trace_retrieval(index_id="kb")
    def search(query):
        return [SimpleNamespace(page_content="x")]

    @gernas.trace_retrieval()
    def scored(query):
        return [("doc", 0.91)]

    search("a")
    scored("b")
    plain, tup = spans(SpanName.RETRIEVAL)
    assert plain.attributes[Ctx.RESULT_COUNT] == 1 and Ctx.RANKING_SCORE not in plain.attributes
    assert tup.attributes[Ctx.RANKING_SCORE] == 0.91


def test_trace_embedding_batch_size(spans):
    @gernas.trace_embedding("bge-m3")
    def embed(texts):
        return SimpleNamespace(usage=SimpleNamespace(prompt_tokens=120, total_tokens=120))

    embed([f"t{i}" for i in range(10)])
    a = spans(SpanName.EMBEDDING)[0].attributes
    assert a[Ctx.EMBED_BATCH_SIZE] == 10 and a[Ctx.EMBED_MODEL] == "bge-m3"
    assert a[Ctx.EMBED_TOKEN_COUNT] == 120 and a[Ctx.EMBED_LATENCY_MS] >= 0


# ── @trace_memory / @trace_planning ──────────────────────────────────────────

def test_trace_memory_read_hit_vs_miss_and_write(spans):
    store = {"s1": [{"q": "a"}]}

    @gernas.trace_memory("read", store="dict", stale_from=lambda r: False)
    def load(session):
        return store.get(session)

    @gernas.trace_memory("write", store="dict")
    def save(session, turn):
        store.setdefault(session, []).append(turn)

    load("s1")
    load("missing")
    save("s2", {"q": "b"})
    hit, miss = spans("memory.read")
    assert hit.attributes[Ctx.MEMORY_HIT] is True and miss.attributes[Ctx.MEMORY_HIT] is False
    assert hit.attributes[Ctx.MEMORY_ITEM_COUNT] == 1 and hit.attributes[Ctx.MEMORY_STALE] is False
    write = spans("memory.write")[0]
    assert write.attributes[Ctx.MEMORY_OPERATION] == "write" and write.attributes[Ctx.MEMORY_SIZE_BYTES] > 0


def test_trace_memory_custom_hit_test(spans):
    @gernas.trace_memory("read", hit_from=lambda r: bool(r[0] or r[1]))
    def load(session):
        return ("", [])  # always a 2-tuple: the default "non-empty" test would call this a hit

    load("s")
    assert spans("memory.read")[0].attributes[Ctx.MEMORY_HIT] is False


def test_trace_planning_step_count_and_steps(spans):
    @gernas.trace_planning(plan_id="plan-1")
    def plan(goal, attempt=1):
        return {"steps": ["fetch", "rank", "answer"]}

    result = plan("price a loan", attempt=2)
    for i, step in enumerate(result["steps"]):
        with gernas.planning_step("plan-1", f"s{i}", step_type=step):
            pass
    p = spans(SpanName.PLANNING)[0]
    assert p.attributes[Ctx.PLAN_STEP_COUNT] == 3 and p.attributes[Ctx.ATTEMPT_NUMBER] == 2
    assert p.attributes[Ctx.PLAN_ID] == "plan-1" and p.attributes[Ctx.PLANNING_LATENCY_MS] >= 0
    steps = spans(SpanName.PLAN_STEP)
    assert [s.attributes[Ctx.STEP_TYPE] for s in steps] == ["fetch", "rank", "answer"]


# ── @trace_use_case / @trace_delegation ──────────────────────────────────────

def test_trace_use_case_is_the_root_of_the_trace(spans):
    @gernas.trace_agent("worker")
    def worker():
        return "ok"

    @gernas.trace_use_case("UC-PRICING", channel="api", outcome_from=lambda r: "success")
    def handle():
        return worker()

    handle()
    root = spans(SpanName.USE_CASE)[0]
    child = spans(SpanName.AGENT)[0]
    assert root.parent is None
    assert child.context.trace_id == root.context.trace_id and child.parent.span_id == root.context.span_id
    assert root.attributes[Ctx.USE_CASE_ID] == "UC-PRICING" and root.attributes[Ctx.CHANNEL] == "api"
    assert root.attributes[Ctx.BUSINESS_OUTCOME] == "success" and root.attributes[Ctx.EXECUTION_STATUS] == "ok"


def test_trace_use_case_new_trace_ignores_ambient_parent(spans):
    @gernas.trace_use_case(new_trace=True)
    def handle():
        return 1

    with gernas.get_tracer().start_as_current_span("ambient") as ambient:
        handle()
    assert spans(SpanName.USE_CASE)[0].context.trace_id != ambient.get_span_context().trace_id


async def test_trace_delegation_child_linkage(spans):
    @gernas.trace_agent("data_agent")
    async def child(headers):
        with gernas.attach_headers(headers):
            return await _remote_child()

    @gernas.trace_agent("data_agent_remote")
    async def _remote_child():
        return gernas.current_run().agent_run_id

    @gernas.trace_delegation("data_agent")
    async def delegate(prompt, headers):
        return await child(headers)

    @gernas.trace_agent("price_assist")
    async def parent():
        return gernas.current_run().agent_run_id, await delegate("q", headers={})

    parent_id, _ = await parent()
    d = spans(SpanName.DELEGATION)[0]
    p = next(s for s in spans(SpanName.AGENT) if s.attributes[Ctx.AGENT_ID] == "price_assist")
    assert d.attributes[Ctx.PARENT_AGENT_ID] == "price_assist" and d.attributes[Ctx.CHILD_AGENT_ID] == "data_agent"
    assert d.attributes[Ctx.DELEGATION_LATENCY_MS] >= 0 and d.attributes[Ctx.DELEGATION_PROTOCOL] == "a2a"
    assert d.context.trace_id == p.context.trace_id and d.parent.span_id == p.context.span_id


# ── @trace_guardrail / @trace_content_safety ─────────────────────────────────

def test_guardrail_deny_is_not_an_error(spans):
    @gernas.trace_guardrail("input_screen", risk_classification="prompt_injection")
    def screen(text):
        return (True, "injection")

    screen("ignore previous instructions")
    span = spans(SpanName.GUARDRAIL)[0]
    assert span.attributes[Ctx.POLICY_DECISION] == "deny" and span.attributes[Ctx.POLICY_ID] == "input_screen"
    assert span.attributes[Ctx.RISK_CLASSIFICATION] == "prompt_injection"
    assert span.status.status_code == StatusCode.OK


@pytest.mark.parametrize("result,expected", [
    ((False, ""), "allow"), (False, "allow"), (True, "deny"), ({"decision": "warn"}, "warn"),
    ({"allowed": False}, "deny"), (SimpleNamespace(blocked=True), "deny"), ("unknown shape", "allow"),
    (("deny", "pii"), "deny"), (("escalate", None), "escalate"),
])
def test_guardrail_decision_inference(spans, result, expected):
    @gernas.trace_guardrail("p")
    def check():
        return result

    check()
    assert spans(SpanName.GUARDRAIL)[0].attributes[Ctx.POLICY_DECISION] == expected


def test_guardrail_blocking_exception_marks_policy_block(spans):
    @gernas.trace_guardrail("hard_block")
    def check():
        raise GuardrailBlockedError("hard_block", "nope")

    @gernas.trace_agent("gate")
    def run():
        check()

    with pytest.raises(GuardrailBlockedError):
        run()
    g = spans(SpanName.GUARDRAIL)[0]
    a = spans(SpanName.AGENT)[0]
    assert g.attributes[Ctx.POLICY_DECISION] == "deny" and g.status.status_code != StatusCode.ERROR
    assert a.attributes[Ctx.TERMINATION_REASON] == "policy_block" and a.status.status_code != StatusCode.ERROR


def test_content_safety_block(spans):
    @gernas.trace_content_safety("pii", policy="output_redaction")
    def redact(text):
        return {"blocked": True, "category": "pii", "risk_level": "medium"}

    @gernas.trace_content_safety("toxicity")
    def clean(text):
        return (False, "toxicity")

    redact("x")
    clean("y")
    blocked, ok = spans(SpanName.CONTENT_SAFETY)
    assert blocked.attributes[Ctx.CONTENT_BLOCKED] is True and blocked.attributes[Ctx.SAFETY_CATEGORY] == "pii"
    assert blocked.attributes[Ctx.RISK_LEVEL] == "medium"
    assert blocked.attributes[Ctx.POLICY_TRIGGERED] == "output_redaction"
    assert ok.attributes[Ctx.CONTENT_BLOCKED] is False and ok.status.status_code == StatusCode.OK


# ── @trace_chain ─────────────────────────────────────────────────────────────

def test_trace_chain_nested_hierarchy(spans):
    from langchain_core.language_models.fake_chat_models import FakeListChatModel
    from langchain_core.runnables import RunnableLambda

    @gernas.trace_chain("pricing_chain", provider="fake")
    def build():
        llm = FakeListChatModel(responses=["fine"])
        inner = RunnableLambda(lambda x: x["q"]).with_config(run_name="inner")
        return inner | llm

    chain = build()
    assert chain.invoke({"q": "hello"}).content == "fine"
    chains = spans(SpanName.CHAIN)
    llms = spans(SpanName.LLM)
    assert chains and llms
    root = next(s for s in chains if s.parent is None or s.parent.span_id not in {c.context.span_id for c in chains})
    assert all(s.context.trace_id == root.context.trace_id for s in chains + llms)
    assert llms[0].parent.span_id in {c.context.span_id for c in chains}
    assert llms[0].attributes[Ctx.PROVIDER] == "fake"


def test_trace_chain_invoke_mode(spans):
    @gernas.trace_chain("runner", mode="invoke")
    def run(x):
        return x * 2

    assert run(2) == 4
    assert spans(SpanName.CHAIN)[0].attributes[Ctx.CHAIN_NAME] == "runner"


def test_trace_chain_non_runnable_passthrough():
    @gernas.trace_chain()
    def build():
        return 5

    assert build() == 5


# ── cross-cutting guarantees ─────────────────────────────────────────────────

def test_telemetry_failure_never_reaches_the_agent(monkeypatch, spans):
    import gernas.decorators._core as core

    def broken(*a, **k):
        raise RuntimeError("tracer exploded")

    monkeypatch.setattr(core, "_begin", broken)

    @gernas.trace_tool("t")
    def tool():
        return "still works"

    assert tool() == "still works"


def test_decorators_preserve_metadata():
    @gernas.trace_rag()
    def documented(query):
        """Docs."""
        return []

    assert documented.__name__ == "documented" and documented.__doc__ == "Docs."
