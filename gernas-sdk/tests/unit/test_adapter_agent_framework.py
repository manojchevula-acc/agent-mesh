"""Microsoft Agent Framework adapter: a real ``Agent`` run with a fake chat client (no network)."""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from opentelemetry.trace import StatusCode

pytest.importorskip("agent_framework")

from agent_framework import (  # noqa: E402
    Agent,
    AgentContext,
    BaseChatClient,
    ChatMiddlewareLayer,
    ChatResponse,
    Content,
    FunctionInvocationLayer,
    Message,
    tool,
)

import gernas  # noqa: E402
from gernas import Ctx, SpanName  # noqa: E402
from gernas.adapters.agent_framework import GernasAgentMiddleware, gernas_middleware  # noqa: E402
from gernas.conformance import check, from_readable  # noqa: E402

from conftest import attrs  # noqa: E402


class FakeClient(FunctionInvocationLayer, ChatMiddlewareLayer, BaseChatClient):
    """First call asks for the ``lookup`` tool, second call answers."""

    def __init__(self, **kw):
        super().__init__(**kw)
        self.model_id = "fake-model"
        self.calls = 0

    def _inner_get_response(self, *, messages, stream, options, **kwargs):
        async def respond():
            self.calls += 1
            if self.calls % 2 == 1:
                return ChatResponse(
                    messages=[
                        Message(
                            "assistant",
                            [
                                Content.from_function_call(
                                    call_id=f"c{self.calls}", name="lookup", arguments={"customer_id": "C-1"}
                                )
                            ],
                        )
                    ],
                    usage_details={"input_token_count": 10, "output_token_count": 2},
                )
            return ChatResponse(
                messages=[Message("assistant", ["done"])],
                finish_reason="stop",
                usage_details={"input_token_count": 20, "output_token_count": 5},
            )

        return respond()


@tool
def lookup(customer_id: str) -> str:
    """Customer lookup."""
    return "Jane Doe, AE070331234567890123456"


def _gernas(spans):
    return [s for s in spans() if Ctx.OPERATION in (s.attributes or {})]


async def test_agent_llm_and_tool_spans_share_one_run(spans):
    agent = Agent(
        FakeClient(),
        name="DataAgent",
        tools=[lookup],
        middleware=gernas_middleware("data_agent", agent_type="worker", provider="groq"),
    )
    result = await agent.run("who is C-1?")
    assert result.text == "done"
    run = spans(SpanName.AGENT)[0]
    llms, tools = spans(SpanName.LLM), spans(SpanName.TOOL)
    assert len(llms) == 2 and len(tools) == 1
    r = attrs(run)
    assert r[Ctx.AGENT_ID] == "data_agent" and r[Ctx.FRAMEWORK] == "agent_framework" and r[Ctx.AGENT_TYPE] == "worker"
    assert r[Ctx.AGENT_NAME] == "DataAgent" and r[Ctx.STEP_COUNT] == 3
    for child in (*llms, *tools):
        assert attrs(child)[Ctx.AGENT_RUN_ID] == r[Ctx.AGENT_RUN_ID]
        assert child.context.trace_id == run.context.trace_id
    first, last = sorted(llms, key=lambda s: s.start_time)
    assert attrs(first)[Ctx.MODEL_ID] == "fake-model" and attrs(first)[Ctx.PROVIDER] == "groq"
    assert (attrs(last)[Ctx.TOKEN_INPUT], attrs(last)[Ctx.TOKEN_OUTPUT]) == (20, 5)
    assert attrs(last)[Ctx.STOP_REASON] == "stop"
    t = attrs(tools[0])
    assert t[Ctx.TOOL_ID] == "lookup" and t[Ctx.TOOL_TYPE] == "function" and t[Ctx.REQUEST_SIZE_BYTES] > 0
    assert not [v for s in _gernas(spans) for v in attrs(s).values() if isinstance(v, str) and "Jane" in v]
    report = check(from_readable(spans()), require_gernas_only=True)
    assert report.passed, report.violations


async def test_mcp_server_marks_tool_type_and_dependency(spans):
    agent = Agent(
        FakeClient(),
        name="DataAgent",
        tools=[lookup],
        middleware=gernas_middleware("data_agent", agent_type="worker", provider="groq", mcp_server="datalayer"),
    )
    await agent.run("q")
    t = attrs(spans(SpanName.TOOL)[0])
    assert t[Ctx.TOOL_TYPE] == "mcp" and t[Ctx.DEPENDENCY] == "datalayer"


async def test_no_duplicate_tool_span_when_tools_are_already_decorated(spans):
    @tool
    @gernas.trace_tool("lookup")
    def decorated_lookup(customer_id: str) -> str:
        """Customer lookup."""
        return "x"

    decorated_lookup.name = "lookup"
    agent = Agent(
        FakeClient(),
        name="A",
        tools=[decorated_lookup],
        middleware=gernas_middleware("a", agent_type="worker", provider="p", trace_functions=False),
    )
    await agent.run("q")
    assert len(spans(SpanName.TOOL)) == 1
    assert len(gernas_middleware("a", agent_type="w", provider="p")) == 3
    assert len(gernas_middleware("a", agent_type="w", provider="p", trace_functions=False)) == 2


async def test_agent_failure_is_recorded(spans):
    async def boom():
        raise TimeoutError

    with pytest.raises(TimeoutError):
        await GernasAgentMiddleware("rag_agent", agent_type="worker").process(
            AgentContext(agent=SimpleNamespace(name="x"), messages=[]), boom
        )
    span = spans(SpanName.AGENT)[0]
    assert attrs(span)[Ctx.TERMINATION_REASON] == "timeout" and span.status.status_code == StatusCode.ERROR


async def test_workflow_executor_steps_use_the_common_step_model(spans):
    @gernas.trace_step("input_guardrail", step_type="workflow_executor")
    async def executor(message):
        return message

    @gernas.trace_agent("mesh", agent_type="orchestrator", framework="agent_framework")
    async def workflow():
        return await executor("m")

    await workflow()
    step = attrs(spans(SpanName.STEP)[0])
    assert step[Ctx.STEP_ID] == "input_guardrail" and step[Ctx.STEP_TYPE] == "workflow_executor"
    assert step[Ctx.AGENT_ID] == "mesh"


def test_disable_native_telemetry_turns_off_maf_spans(spans):
    import agent_framework.observability as maf_obs

    from gernas.adapters.agent_framework import disable_native_telemetry

    saved = (
        maf_obs.OBSERVABILITY_SETTINGS._user_disabled,
        maf_obs.OBSERVABILITY_SETTINGS._enable_instrumentation,
        maf_obs.OBSERVABILITY_SETTINGS._enable_sensitive_data,
    )
    try:
        disable_native_telemetry()
        assert maf_obs.OBSERVABILITY_SETTINGS.enable_instrumentation is False
        assert maf_obs.OBSERVABILITY_SETTINGS.enable_sensitive_data is False
    finally:
        (
            maf_obs.OBSERVABILITY_SETTINGS._user_disabled,
            maf_obs.OBSERVABILITY_SETTINGS._enable_instrumentation,
            maf_obs.OBSERVABILITY_SETTINGS._enable_sensitive_data,
        ) = saved
