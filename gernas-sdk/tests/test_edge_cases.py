"""Edge paths: async compliance decorators, LangChain tool/retriever/error events,
interceptor chaining, structured logging through the OTel provider."""
from __future__ import annotations

import uuid
from types import SimpleNamespace

import pytest
from opentelemetry import baggage, context
from opentelemetry.trace import StatusCode

import gernas
from gernas import Ctx, SpanName

from conftest import run_python


async def test_async_compliance_decorators():
    @gernas.compliance_gate("hitl_approval_decision")
    async def decide(actor="officer", run_id="r"):
        return "approved"

    @gernas.compliance_gate("hitl_approval_decision")
    async def explode():
        raise RuntimeError

    @gernas.guardrail_check("pii_warn", action_on_block="warn")
    async def warn(text):
        return (True, "pii")

    @gernas.guardrail_check("hard", action_on_block="block")
    async def block(text):
        return True

    @gernas.rbac_check("compliance_officer")
    async def audit():
        return "ok"

    assert await decide() == "approved"
    with pytest.raises(RuntimeError):
        await explode()
    assert await warn("x") == (True, "pii")
    with pytest.raises(gernas.GuardrailBlockedError):
        await block("x")
    token = context.attach(baggage.set_baggage(Ctx.USER_ROLE, "compliance_officer"))
    try:
        assert await audit() == "ok"
    finally:
        context.detach(token)
    with pytest.raises(gernas.RBACDeniedError):
        await audit()


def test_langchain_callback_tool_retriever_and_errors(spans):
    from gernas.adapters.langchain import GernasLangChainCallback

    cb = GernasLangChainCallback(provider="azure_openai")
    root, tool, retr, llm, failing = (uuid.uuid4() for _ in range(5))
    cb.on_chain_start({"id": ["x", "AgentExecutor"]}, {"q": 1}, run_id=root)
    cb.on_tool_start({"name": "lookup"}, "C-1", run_id=tool, parent_run_id=root)
    cb.on_tool_end("result", run_id=tool)
    cb.on_retriever_start({}, "query", run_id=retr, parent_run_id=root)
    cb.on_retriever_end([1, 2], run_id=retr)
    cb.on_llm_start({}, ["p"], run_id=llm, parent_run_id=root, invocation_params={"model": "gpt-4o"})
    cb.on_llm_end(SimpleNamespace(llm_output={"token_usage": {"prompt_tokens": 10, "completion_tokens": 2}},
                                  generations=[]), run_id=llm)
    cb.on_tool_start({"name": "boom"}, "x", run_id=failing, parent_run_id=root)
    cb.on_tool_error(RuntimeError("down"), run_id=failing)
    cb.on_chain_error(ValueError("chain failed"), run_id=root)
    cb.on_llm_error(RuntimeError(), run_id=uuid.uuid4())  # unknown run: ignored
    assert cb.open_run_count() == 0
    chain = spans(SpanName.CHAIN)[0]
    assert chain.attributes[Ctx.CHAIN_NAME] == "AgentExecutor" and chain.status.status_code == StatusCode.ERROR
    assert spans("tool.lookup")[0].parent.span_id == chain.context.span_id
    assert spans(SpanName.RETRIEVAL)[0].attributes[Ctx.RESULT_COUNT] == 2
    llm_span = spans(SpanName.LLM)[0]
    assert (llm_span.attributes[Ctx.INPUT_TOKENS], llm_span.attributes[Ctx.OUTPUT_TOKENS]) == (10, 2)
    assert llm_span.attributes[Ctx.EST_COST_AED] > 0
    assert spans("tool.boom")[0].status.status_code == StatusCode.ERROR


def test_langchain_callback_reads_usage_metadata_from_chat_messages(spans):
    from gernas.adapters.langchain import GernasLangChainCallback

    cb = GernasLangChainCallback()
    rid = uuid.uuid4()
    message = SimpleNamespace(usage_metadata={"input_tokens": 4, "output_tokens": 1})
    cb.on_chat_model_start({"name": "ChatX"}, [[]], run_id=rid, metadata={"ls_provider": "openai"})
    cb.on_llm_end(SimpleNamespace(llm_output=None, generations=[[SimpleNamespace(message=message)]]), run_id=rid)
    span = spans(SpanName.LLM)[0]
    assert span.attributes[Ctx.PROVIDER] == "openai" and span.attributes[Ctx.INPUT_TOKENS] == 4


async def test_a2a_interceptor_chains_an_existing_interceptor_and_context():
    from a2a.client.client import ClientCallContext
    from a2a.client.interceptors import AfterArgs, BeforeArgs, ClientCallInterceptor
    from gernas.adapters.a2a import GernasA2AInterceptor

    calls = []

    class Auth(ClientCallInterceptor):
        async def before(self, args):
            calls.append("before")
            args.context = ClientCallContext(service_parameters={"Authorization": "Bearer t"})

        async def after(self, args):
            calls.append("after")

    interceptor = GernasA2AInterceptor(Auth())
    args = BeforeArgs(input=None, method="m", agent_card=None)
    with gernas.get_tracer().start_as_current_span("hop"):
        await interceptor.before(args)
    await interceptor.after(AfterArgs(result=None, method="m", agent_card=None))
    assert calls == ["before", "after"]
    assert args.context.service_parameters["Authorization"] == "Bearer t"
    assert "traceparent" in args.context.service_parameters


def test_structured_logger_through_the_otel_provider(tmp_path):
    code = """
import gernas
h = gernas.init_gernas()
log = gernas.get_logger("gernas.app")
with gernas.get_tracer().start_as_current_span("s"):
    log.info("customer alice@fab.ae", note="call +971 50 123 4567", n=3)
h.flush(); h.shutdown()
"""
    res = run_python(code, env={"GERNAS_EXPORTER": "file", "GERNAS_FILE_OUTPUT_DIR": str(tmp_path)})
    assert res.returncode == 0, res.stderr
    text = "".join(p.read_text() for p in (tmp_path / "logs").glob("*.jsonl"))
    assert "[EMAIL]" in text and "alice@fab.ae" not in text and "123 4567" not in text


def test_cost_is_skipped_for_bad_rate_cards(tmp_path):
    from gernas.cost import estimate_cost_aed

    bad = tmp_path / "bad.json"
    bad.write_text("{not json")
    partial = tmp_path / "partial.json"
    partial.write_text('{"rates": [{"model": "m", "provider": "p"}]}')
    assert estimate_cost_aed("m", "p", 1, 1, str(bad)) is None
    assert estimate_cost_aed("m", "p", 1, 1, str(partial)) is None
    assert estimate_cost_aed("m", "p", 1, 1, str(tmp_path / "missing.json")) is None
    assert estimate_cost_aed("m", "other", 1, 1, str(partial)) is None
    assert estimate_cost_aed(None, "p", 1, 1, str(partial)) is None
