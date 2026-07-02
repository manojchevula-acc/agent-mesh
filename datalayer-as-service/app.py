"""
app.py
------
Streamlit chat GUI for the FAB Pricing Recommendation Data Assistant.

Run with:
    streamlit run app.py

The agent queries ONLY the fab_semantic MySQL views via mcp_server/tools.py.
"""

import os
import logging

import streamlit as st
from dotenv import load_dotenv
from sqlalchemy import text

from agent import build_agent, run_agent

# ---------------------------------------------------------------------------
# Setup
# ---------------------------------------------------------------------------
load_dotenv()

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

st.set_page_config(
    page_title="FAB Pricing Recommendation Agent",
    page_icon="🏦",
    layout="wide",
)

SEMANTIC_VIEWS = [
    "customer_360",
    "pricing_recommendation_view",
    "margin_analysis",
    "profitability_summary",
    "rwa_impact_view",
    "new_customer_pricing_view",
    "competitor_price_analysis",
    "pricing_trace_view",
    "segment_pricing_benchmark",
    "operations_cost_impact",
    "relationship_discount_view",
    "win_loss_insights",
    "policy_exception_view",
]

SUGGESTED_QUESTIONS = [
    "What interest rate should I offer to customer CUST001?",
    "What interest rate should I offer to a new SME customer for Trade Finance?",
    "Explain step by step how the price was calculated for DEAL040.",
    "Customer CUST001 has a competitor offer lower than FAB recommendation. What should we do?",
    "Which deals are non-compliant and why?",
    "Show competitor pricing pressure by product.",
    "Show operations cost impact on pricing.",
    "Show profitability and RWA impact for CUST005.",
    "Compare approved price vs recommended price vs competitor price.",
    "What should the relationship manager do next for CUST001?",
]


# ---------------------------------------------------------------------------
# Cached agent
# ---------------------------------------------------------------------------
@st.cache_resource(show_spinner=False)
def get_agent():
    return build_agent()


def run_health_check() -> list[tuple[str, str]]:
    """Test MySQL connection and each semantic view; return (view, status) rows."""
    from mcp_server.db import get_engine
    results: list[tuple[str, str]] = []
    engine = get_engine()
    with engine.connect() as conn:
        row = conn.execute(text("SELECT VERSION(), DATABASE()")).fetchone()
        results.append(("MySQL connection", f"OK (v{row[0]}, db={row[1]})"))
        for view in SEMANTIC_VIEWS:
            try:
                count = conn.execute(text(f"SELECT COUNT(*) FROM fab_semantic.`{view}`")).scalar()
                results.append((view, f"OK — {count} rows"))
            except Exception as exc:
                results.append((view, f"ERROR: {exc}"))
    return results


# ---------------------------------------------------------------------------
# Header
# ---------------------------------------------------------------------------
st.title("🏦 FAB Pricing Recommendation Agent")
st.caption(
    "Ask about customer pricing, margins, profitability, RWA impact and competitor "
    "comparisons. Powered by the fab_semantic layer (semantic views only)."
)

# ---------------------------------------------------------------------------
# Sidebar
# ---------------------------------------------------------------------------
with st.sidebar:
    st.header("💬 Suggested questions")
    for i, q in enumerate(SUGGESTED_QUESTIONS):
        if st.button(q, key=f"suggest_{i}", use_container_width=True):
            st.session_state.pending_prompt = q

    st.divider()
    st.header("📚 Data scope (fab_semantic)")
    st.caption("The agent can only read these semantic views:")
    st.markdown("\n".join(f"- `{v}`" for v in SEMANTIC_VIEWS))

    st.divider()
    st.header("🩺 Health check")
    if st.button("Run health check", use_container_width=True):
        with st.spinner("Testing MySQL connection and semantic views..."):
            try:
                for name, status in run_health_check():
                    icon = "✅" if status.startswith("OK") else "❌"
                    st.write(f"{icon} **{name}** — {status}")
            except Exception as exc:
                st.error(f"Health check failed: {exc}")

    st.divider()
    if st.button("Clear conversation", use_container_width=True):
        st.session_state.messages = []
        st.rerun()

# ---------------------------------------------------------------------------
# Pre-flight checks
# ---------------------------------------------------------------------------
missing_env = [k for k in ("GROQ_API_KEY", "MYSQL_USER", "MYSQL_PASSWORD",
                           "MYSQL_HOST", "MYSQL_DATABASE") if not os.getenv(k)]
if missing_env:
    st.error(
        "Missing required environment variable(s): "
        + ", ".join(missing_env)
        + ". Add them to your `.env` file (see `.env.example`) and restart the app."
    )
    st.stop()

try:
    agent = get_agent()
except Exception as exc:
    st.error(f"Failed to initialise the agent: {exc}")
    st.stop()

# ---------------------------------------------------------------------------
# Conversation state
# ---------------------------------------------------------------------------
if "messages" not in st.session_state:
    st.session_state.messages = []

for msg in st.session_state.messages:
    with st.chat_message(msg["role"]):
        st.markdown(msg["content"])


def handle_prompt(prompt: str) -> None:
    st.session_state.messages.append({"role": "user", "content": prompt})
    with st.chat_message("user"):
        st.markdown(prompt)
    with st.chat_message("assistant"):
        with st.spinner("Querying semantic layer..."):
            try:
                answer = run_agent(agent, prompt)
            except Exception as exc:
                logger.error("Agent error: %s", exc)
                answer = (
                    "Sorry, something went wrong while processing your request. "
                    "Please check that MySQL is running and the database connection "
                    f"is valid.\n\n**Details:** {exc}"
                )
        st.markdown(answer)
    st.session_state.messages.append({"role": "assistant", "content": answer})


# A suggested-question button was clicked
pending = st.session_state.pop("pending_prompt", None)
if pending:
    handle_prompt(pending)

# Free-text chat input
user_input = st.chat_input("Ask about a customer, e.g. 'What interest rate should I offer to CUST001?'")
if user_input:
    handle_prompt(user_input)
