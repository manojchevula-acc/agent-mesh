"""LangChain chain (prompt → model, tool, retriever) instrumented with the Gernas callback handler.

Uses LangChain's fake chat model so the example runs offline.

    pip install gernas-telemetry[langchain]
    set GERNAS_SERVICE_NAME=faq-chain GERNAS_USE_CASE_ID=UC-FAQ-001 GERNAS_APPLICATION_ID=faq-app ^
        GERNAS_BUSINESS_DOMAIN=retail_banking GERNAS_ENVIRONMENT=dev GERNAS_EXPORTER=console
    python examples/langchain_agent.py
"""

from __future__ import annotations

from langchain_core.callbacks import CallbackManagerForRetrieverRun
from langchain_core.documents import Document
from langchain_core.language_models.fake_chat_models import GenericFakeChatModel
from langchain_core.messages import AIMessage
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.retrievers import BaseRetriever
from langchain_core.runnables import RunnableLambda
from langchain_core.tools import tool

import gernas
from gernas.adapters.langchain import instrument


class PolicyRetriever(BaseRetriever):
    def _get_relevant_documents(self, query: str, *, run_manager: CallbackManagerForRetrieverRun) -> list[Document]:
        return [
            Document(page_content="Fees are waived for premier customers.", metadata={"id": "policy-7", "score": 0.9})
        ]


@tool
def account_tier(customer_id: str) -> str:
    """Return the customer's account tier."""
    return "premier"


model = GenericFakeChatModel(
    messages=iter(
        [
            AIMessage(
                content="Fees are waived for you.",
                usage_metadata={"input_tokens": 95, "output_tokens": 7, "total_tokens": 102},
                response_metadata={"finish_reason": "stop", "model_name": "gpt-4o"},
            )
        ]
    )
)
prompt = ChatPromptTemplate.from_messages([("system", "Context: {context}"), ("user", "{question}")])


def build_inputs(question: str) -> dict:
    tier = account_tier.invoke({"customer_id": "C-1001"})
    docs = PolicyRetriever().invoke(question)
    return {"question": question, "context": f"tier={tier}; " + " ".join(d.page_content for d in docs)}


chain = instrument(
    RunnableLambda(build_inputs) | prompt | model, agent_id="faq_chain", agent_type="worker", provider="azure_openai"
)


def main() -> None:
    handle = gernas.init_gernas()
    try:
        with gernas.request_context(session_id="sess-faq-1", channel="web"):
            print(chain.invoke("Do I pay account fees?").content)
    finally:
        handle.shutdown()


if __name__ == "__main__":
    main()
