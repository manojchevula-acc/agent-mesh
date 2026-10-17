"""LangGraph graph instrumented with the Gernas SDK: one ``agent.run``, one ``agent.step`` per node.

Uses LangChain's fake chat model so the example runs offline.

    pip install gernas-telemetry[langgraph]
    set GERNAS_SERVICE_NAME=pricing-graph GERNAS_USE_CASE_ID=UC-PRICING-001 GERNAS_APPLICATION_ID=pricing-app ^
        GERNAS_BUSINESS_DOMAIN=retail_banking GERNAS_ENVIRONMENT=dev GERNAS_EXPORTER=console
    python examples/langgraph_agent.py
"""

from __future__ import annotations

from typing import TypedDict

from langchain_core.language_models.fake_chat_models import GenericFakeChatModel
from langchain_core.messages import AIMessage
from langgraph.graph import END, START, StateGraph

import gernas
from gernas.adapters.langgraph import instrument_graph

model = GenericFakeChatModel(
    messages=iter(
        [
            AIMessage(
                content="The premier FX margin is 0.75%.",
                usage_metadata={"input_tokens": 60, "output_tokens": 9, "total_tokens": 69},
                response_metadata={"finish_reason": "stop", "model_name": "gpt-4o"},
            )
        ]
    )
)


class State(TypedDict):
    question: str
    intent: str
    answer: str


def classify(state: State) -> dict:
    return {"intent": "pricing" if "margin" in state["question"].lower() else "other"}


@gernas.trace_guardrail("pricing_scope")
def in_scope(intent: str) -> tuple[str, str | None]:
    return ("allow", None) if intent == "pricing" else ("deny", "out_of_scope")


def answer(state: State) -> dict:
    if in_scope(state["intent"])[0] == "deny":
        return {"answer": "I can only answer pricing questions."}
    return {"answer": model.invoke(state["question"]).content}


builder = StateGraph(State)
builder.add_node("classify", classify)
builder.add_node("answer", answer)
builder.add_edge(START, "classify")
builder.add_edge("classify", "answer")
builder.add_edge("answer", END)
graph = instrument_graph(
    builder.compile(), agent_id="pricing_graph", agent_type="orchestrator", provider="azure_openai"
)


def main() -> None:
    handle = gernas.init_gernas()
    try:
        with gernas.request_context(session_id="sess-graph-1"):
            print(graph.invoke({"question": "What is the FX margin?", "intent": "", "answer": ""})["answer"])
    finally:
        handle.shutdown()


if __name__ == "__main__":
    main()
