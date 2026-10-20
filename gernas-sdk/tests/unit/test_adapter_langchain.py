"""LangChain and LangGraph adapters: real runnables with fake models (no network)."""

from __future__ import annotations

from typing import TypedDict

import pytest
from opentelemetry.trace import StatusCode

pytest.importorskip("langchain_core")

from langchain_core.callbacks import CallbackManagerForRetrieverRun  # noqa: E402
from langchain_core.documents import Document  # noqa: E402
from langchain_core.language_models.fake_chat_models import GenericFakeChatModel  # noqa: E402
from langchain_core.messages import AIMessage  # noqa: E402
from langchain_core.prompts import ChatPromptTemplate  # noqa: E402
from langchain_core.retrievers import BaseRetriever  # noqa: E402
from langchain_core.runnables import RunnableLambda  # noqa: E402
from langchain_core.tools import tool  # noqa: E402

import gernas  # noqa: E402
from gernas import Ctx, SpanName  # noqa: E402
from gernas.adapters.langchain import GernasCallbackHandler, instrument  # noqa: E402
from gernas.conformance import check, from_readable  # noqa: E402

from conftest import attrs  # noqa: E402


def _model(*answers):
    return GenericFakeChatModel(
        messages=iter(
            [
                AIMessage(
                    content=a,
                    usage_metadata={"input_tokens": 11, "output_tokens": 3, "total_tokens": 14},
                    response_metadata={"finish_reason": "stop", "model_name": "fake-chat"},
                )
                for a in answers
            ]
        )
    )


class Docs(BaseRetriever):
    def _get_relevant_documents(self, query: str, *, run_manager: CallbackManagerForRetrieverRun):
        return [Document(page_content="secret text", metadata={"id": "doc-1", "score": 0.8})]


@tool
def lookup(customer_id: str) -> str:
    """Customer lookup."""
    return "Jane Doe"


def _conformant(spans):
    report = check(from_readable(spans()))
    assert report.passed, [(v.rule, v.span, v.key) for v in report.violations]


def test_chain_produces_one_run_with_llm_child(spans):
    chain = instrument(
        ChatPromptTemplate.from_messages([("user", "{q}")]) | _model("hello"),
        agent_id="faq_chain",
        agent_type="worker",
        provider="fake",
    )
    assert chain.invoke({"q": "my account 12345678901234"}).content == "hello"
    run, llm = spans(SpanName.AGENT)[0], spans(SpanName.LLM)[0]
    assert len(spans(SpanName.AGENT)) == 1
    assert llm.parent.span_id == run.context.span_id
    r, m = attrs(run), attrs(llm)
    assert r[Ctx.FRAMEWORK] == "langchain" and r[Ctx.AGENT_ID] == "faq_chain" and r[Ctx.STEP_COUNT] == 1
    assert m[Ctx.AGENT_RUN_ID] == r[Ctx.AGENT_RUN_ID] and m[Ctx.PROVIDER] == "fake"
    assert (m[Ctx.TOKEN_INPUT], m[Ctx.TOKEN_OUTPUT]) == (11, 3) and m[Ctx.STOP_REASON] == "stop"
    assert not [v for s in spans() for v in attrs(s).values() if "12345678901234" in str(v)]
    _conformant(spans)


def test_tool_and_retriever_spans(spans):
    def use_tools(_):
        lookup.invoke({"customer_id": "C-1"})
        return Docs().invoke("query text")

    chain = instrument(RunnableLambda(use_tools), agent_id="rag_chain", agent_type="worker")
    chain.invoke({})
    t, ret = attrs(spans(SpanName.TOOL)[0]), attrs(spans(SpanName.RETRIEVAL)[0])
    assert t[Ctx.TOOL_ID] == "lookup" and t[Ctx.TOOL_TYPE] == "function" and t[Ctx.AUTHORIZATION_DECISION] == "allow"
    assert ret[Ctx.RESULT_COUNT] == 1 and ret[Ctx.EMPTY_RESULT] is False and ret[Ctx.SOURCE_ID] == "doc-1"
    assert ret[Ctx.RANKING_SCORE] == 0.8
    assert not [v for s in spans() for v in attrs(s).values() if v in ("query text", "secret text", "Jane Doe")]
    _conformant(spans)


def test_chain_error_marks_run_and_closes_every_span(spans):
    def fail(_):
        raise ConnectionError("down")

    handler = GernasCallbackHandler("c", agent_type="worker")
    with pytest.raises(ConnectionError):
        RunnableLambda(fail).invoke({}, config={"callbacks": [handler]})
    run = spans(SpanName.AGENT)[0]
    assert run.status.status_code == StatusCode.ERROR and attrs(run)[Ctx.ERROR_CATEGORY] == "dependency"
    assert handler.open_count() == 0


def test_inside_an_existing_gernas_run_no_second_agent_span(spans):
    chain = instrument(_model("x"), agent_id="inner", agent_type="worker")

    @gernas.trace_agent("outer", agent_type="orchestrator")
    def run():
        return chain.invoke("q")

    run()
    assert len(spans(SpanName.AGENT)) == 1
    llm, agent = spans(SpanName.LLM)[0], spans(SpanName.AGENT)[0]
    assert attrs(llm)[Ctx.AGENT_ID] == "outer" and llm.parent.span_id == agent.context.span_id


def test_trace_tools_false_avoids_duplicates(spans):
    @tool
    @gernas.trace_tool("lookup2")
    def lookup2(customer_id: str) -> str:
        """Lookup."""
        return "x"

    chain = instrument(
        RunnableLambda(lambda _: lookup2.invoke({"customer_id": "1"})),
        agent_id="c",
        agent_type="worker",
        trace_tools=False,
    )
    chain.invoke({})
    assert len(spans(SpanName.TOOL)) == 1


async def test_async_chain(spans):
    chain = instrument(_model("a"), agent_id="async_chain", agent_type="worker")
    assert (await chain.ainvoke("q")).content == "a"
    assert spans(SpanName.LLM)[0].parent.span_id == spans(SpanName.AGENT)[0].context.span_id


# ── LangGraph ────────────────────────────────────────────────────────────────

langgraph = pytest.importorskip("langgraph")


class State(TypedDict):
    question: str
    answer: str


def test_graph_nodes_become_steps_under_one_run(spans):
    from langgraph.graph import END, START, StateGraph

    from gernas.adapters.langgraph import instrument_graph

    model = _model("priced")

    def classify(state: State):
        return {"question": state["question"]}

    def answer(state: State):
        return {"answer": model.invoke(state["question"]).content}

    builder = StateGraph(State)
    builder.add_node("classify", classify)
    builder.add_node("answer", answer)
    builder.add_edge(START, "classify")
    builder.add_edge("classify", "answer")
    builder.add_edge("answer", END)
    app = instrument_graph(builder.compile(), agent_id="pricing_graph", agent_type="orchestrator", provider="fake")

    assert app.invoke({"question": "q", "answer": ""})["answer"] == "priced"
    runs, steps = spans(SpanName.AGENT), spans(SpanName.STEP)
    assert len(runs) == 1 and attrs(runs[0])[Ctx.FRAMEWORK] == "langgraph"
    assert sorted(attrs(s)[Ctx.STEP_ID] for s in steps) == ["answer", "classify"]
    for s in steps:
        assert attrs(s)[Ctx.STEP_TYPE] == "graph_node" and s.parent.span_id == runs[0].context.span_id
        assert attrs(s)[Ctx.AGENT_RUN_ID] == attrs(runs[0])[Ctx.AGENT_RUN_ID]
    llm = spans(SpanName.LLM)[0]
    answer_step = next(s for s in steps if attrs(s)[Ctx.STEP_ID] == "answer")
    assert llm.parent.span_id == answer_step.context.span_id
    assert attrs(runs[0])[Ctx.STEP_COUNT] == 3  # two nodes + one model call
    _conformant(spans)


async def test_graph_node_failure(spans):
    from langgraph.graph import END, START, StateGraph

    from gernas.adapters.langgraph import instrument_graph

    def broken(state: State):
        raise ValueError("bad input")

    builder = StateGraph(State)
    builder.add_node("broken", broken)
    builder.add_edge(START, "broken")
    builder.add_edge("broken", END)
    app = instrument_graph(builder.compile(), agent_id="g", agent_type="worker")
    with pytest.raises(ValueError):
        await app.ainvoke({"question": "q", "answer": ""})
    step = spans(SpanName.STEP)[0]
    assert step.status.status_code == StatusCode.ERROR and attrs(step)[Ctx.ERROR_CATEGORY] == "validation"
    assert spans(SpanName.AGENT)[0].status.status_code == StatusCode.ERROR
