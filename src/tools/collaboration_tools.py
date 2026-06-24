"""Cross-agent collaboration tools (agent-to-agent peer delegation).

These let the Price Assist coordinator reach its peer domain agents over A2A
during its own reasoning. The coordinator's LLM decides which peer(s) to call:

  - query_structured_data  -> Data Agent  -> DataLayer service (structured SQL data)
  - query_knowledge_base   -> RAG Agent   -> RAG service (banking knowledge documents)

Two safeguards:
1. Depth guard — a ContextVar bounds nested delegation within a single process so
   a misbehaving chain cannot recurse without limit.
2. Soft-fail — a failed hop returns an error STRING rather than raising, so the
   coordinator can tell the user a source is unavailable instead of crashing.
"""
import sys
import pathlib
import contextvars

project_root = str(pathlib.Path(__file__).resolve().parents[2])
if project_root not in sys.path:
    sys.path.insert(0, project_root)

from agent_framework import tool
from src.a2a.clients import ask_remote

# Bounds nested peer delegation within a single process.
_peer_depth: contextvars.ContextVar[int] = contextvars.ContextVar("peer_call_depth", default=0)
_MAX_PEER_DEPTH = 2


async def _consult_peer(node: str, question: str, unavailable_label: str) -> str:
    depth = _peer_depth.get()
    if depth >= _MAX_PEER_DEPTH:
        return "PEER_LIMIT: delegation depth exceeded; aborting to prevent loops."
    token = _peer_depth.set(depth + 1)
    try:
        return await ask_remote(node, question)
    except Exception as e:
        return f"{unavailable_label}: could not reach the {node} agent ({e})."
    finally:
        _peer_depth.reset(token)


@tool(description=(
    "Query FAB structured banking data via the Data Agent: customer profiles, deal "
    "pricing, margins, profitability, and RWA/capital (DataLayer). Use for any "
    "numeric or record lookup about a customer or deal (e.g. CUST001's recommended "
    "price, margin analysis, profitability tier, RWA impact)."
))
async def query_structured_data(question: str) -> str:
    """Agent-to-agent hop: Price Assist asks the Data Agent (over A2A)."""
    return await _consult_peer("data_agent", question, "DATA_UNAVAILABLE")


@tool(description=(
    "Query FAB banking knowledge via the RAG Agent: pricing floors and ceilings, "
    "fee schedules, credit/regulatory policy, product guidelines, FAQs, operational "
    "procedures, AML/KYC rules, concentration limits, model risk policy "
    "(RAG-as-a-Service knowledge base). Use to retrieve the rule or benchmark that "
    "a price or action must comply with."
))
async def query_knowledge_base(question: str) -> str:
    """Agent-to-agent hop: Price Assist asks the RAG Agent (over A2A)."""
    return await _consult_peer("rag_agent", question, "RAG_UNAVAILABLE")


COORDINATION_TOOLS = [query_structured_data, query_knowledge_base]
