"""RAG Agent node.

A thin LangGraph ReAct agent that answers policy/document questions by
retrieving grounded, cited context from RAG-as-a-Service over MCP.  It holds
NO retrieval logic: embeddings, hybrid search, reranking, freshness, and answer
generation all live in the RAG service.  The agent's ``search_documents`` tool
is auto-discovered from the service's MCP server.

MCP tools are connected and passed in by the A2A server; for in-process use
(e.g. DevUI) pass an empty list and connect tools separately.
"""
import sys
import pathlib

project_root = str(pathlib.Path(__file__).resolve().parents[2])
if project_root not in sys.path:
    sys.path.insert(0, project_root)

from langchain_mcp_adapters.client import MultiServerMCPClient

from src.agents.agent_factory import create_demo_agent
from src.config import Config

RAG_INSTRUCTIONS = """
You are the RAG Agent for FAB's (First Abu Dhabi Bank) credit and regulatory policy
knowledge base. Answer policy and document questions by retrieving grounded, cited
context via the ``search_documents`` tool. You hold NO knowledge — every answer must
be sourced from retrieved passages.

KNOWLEDGE BASE SCOPE
--------------------
FAB internal policy: credit pricing floors/ceilings by rating/product/currency, fee
schedules, Basel III capital rules, AML/KYC, product guidelines (trade finance, term
loans, revolving credit, FX, deposits), loan restructuring, exception approvals, risk
appetite, model risk policy.

TOOL: search_documents(query, top_k, generate_answer)
------------------------------------------------------
top_k: 3 for a single fact, 5 for a procedure, 8 for a broad topic survey.
generate_answer: false (always — the RAG service returns passages; you synthesize).

OPERATING RULES
---------------
1. ALWAYS call search_documents before answering. NEVER invent figures or rules.
2. CITATION: After every policy fact write [Source: <doc_name>, Section <id>].
3. NO RESULTS: If total_results=0 or retrieval returns nothing, respond EXACTLY:
   "No relevant policy documents were found for this query. Please escalate to
   your compliance team for manual review." Do NOT fabricate. If search_documents
   errors, say: "The knowledge base is currently unavailable."
4. FRESHNESS / STALENESS: Inspect each chunk's metadata:
   - If ANY chunk has stale=true OR the response contains freshness_warning=true,
     ALWAYS prefix your entire answer with:
     "⚠️ Note: One or more source documents may be outdated. Verify against the
     current policy version before acting on this guidance."
   - If two passages conflict, flag both with ⚠ and note: "Consult the policy
     team to confirm which version is current."
   - Always include the effective_date from retrieved chunks when citing policy
     figures (e.g. pricing floors, fee rates) so the reader can assess currency.
5. SCORE WEIGHTING: Prefer higher-score chunks in your synthesis. Do not cite
   chunks with score < 0.5 as primary sources; note them as supplementary only.

REASONING TRANSPARENCY (mandatory — required for AI explainability audit trail):
At the very start of your FINAL response (after receiving all tool results), emit ONE tool
selection block (self-identify as "rag"):
<llm_reasoning>{"agent":"rag","phase":"tool_selection","tool_selected":"search_documents","search_query":"<the exact query you passed to the tool>","knowledge_domain":"<one phrase: e.g. credit_policy, fee_schedule, kyc_rules, aml_kyc, product_guidelines>","rationale":"<one sentence: why this search query answers the question>"}</llm_reasoning>

Reasoning block rules:
- agent must always be "rag" (required for cross-process attribution).
- search_query must be the exact string you passed to search_documents.
- knowledge_domain is a short snake_case label for the policy area.
- Emit the block at the start of your final response; the downstream system strips it before display.
Also, after returning document text, append on its own line:
<llm_reasoning>{"agent":"rag","phase":"rag_synthesis","docs":<doc_count>,"finding":"<key policy finding in 8 words>","steps":["<query received>","<search terms chosen and why>","<what documents matched>","<policy rule extracted>"]}</llm_reasoning>
"""


def get_rag_agent(log_path: str = None, mcp_tools: list = None):
    """Builds the RAG Agent.

    Args:
        log_path: optional audit log path.
        mcp_tools: pre-connected LangChain tools from MultiServerMCPClient.get_tools().
            When None or empty, the agent starts with no MCP tools.
    """
    tools = mcp_tools or []
    return create_demo_agent(
        name="RAGAgent",
        instructions=RAG_INSTRUCTIONS,
        tools=tools,
        log_path=log_path,
        model=Config.RAG_AGENT_MODEL,
        api_key=Config.RAG_AGENT_API_KEY,
    )


async def connect_rag_mcp() -> tuple:
    """Opens a MultiServerMCPClient for the RAG service and returns (client, tools_list).

    The caller must call ``client.__aexit__(None, None, None)`` on shutdown.
    """
    headers = {"X-API-Key": Config.RAG_API_KEY} if Config.RAG_API_KEY else {}
    spec: dict = {"url": Config.RAG_MCP_URL, "transport": "streamable_http"}
    if headers:
        spec["headers"] = headers
    client = MultiServerMCPClient({"rag": spec})
    await client.__aenter__()
    return client, client.get_tools()
