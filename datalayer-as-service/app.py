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
    layout="centered",
)


# ---------------------------------------------------------------------------
# Cached agent (built once per session)
# ---------------------------------------------------------------------------
@st.cache_resource(show_spinner=False)
def get_agent():
    """Build the LangChain Groq agent once and cache it."""
    return build_agent()


# ---------------------------------------------------------------------------
# Header
# ---------------------------------------------------------------------------
st.title("🏦 FAB Pricing Recommendation Agent")
st.caption(
    "Ask about customer pricing, margins, profitability and RWA impact. "
    "Powered by the fab_semantic layer (semantic views only)."
)

with st.sidebar:
    st.header("About")
    st.markdown(
        """
        This assistant answers questions using FAB's **semantic layer**
        in MySQL (`fab_semantic`).

        **Available tools**
        - `customer_360`
        - `pricing_recommendation`
        - `margin_analysis`
        - `profitability_summary`
        - `rwa_impact`

        **Try asking**
        - Show customer profile for CUST001
        - Pricing recommendation for CUST002
        - Which deals are non-compliant for CUST013?
        - RWA impact for CUST005
        """
    )
    if st.button("Clear conversation"):
        st.session_state.messages = []
        st.rerun()

# ---------------------------------------------------------------------------
# Pre-flight checks (clear error messages)
# ---------------------------------------------------------------------------
if not os.getenv("GROQ_API_KEY"):
    st.error(
        "GROQ_API_KEY is not set. Add it to your `.env` file and restart the app."
    )
    st.stop()

# Build agent (handles Groq / config errors gracefully)
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

# Render history
for msg in st.session_state.messages:
    with st.chat_message(msg["role"]):
        st.markdown(msg["content"])

# ---------------------------------------------------------------------------
# Chat input
# ---------------------------------------------------------------------------
user_input = st.chat_input("Ask about a customer, e.g. 'Show customer profile for CUST001'")

if user_input:
    # Show user message
    st.session_state.messages.append({"role": "user", "content": user_input})
    with st.chat_message("user"):
        st.markdown(user_input)

    # Generate answer
    with st.chat_message("assistant"):
        with st.spinner("Querying semantic layer..."):
            try:
                answer = run_agent(agent, user_input)
            except Exception as exc:
                logger.error("Agent error: %s", exc)
                answer = (
                    "Sorry, something went wrong while processing your request. "
                    "Please check that MySQL is running and the database "
                    f"connection is valid.\n\n**Details:** {exc}"
                )
        st.markdown(answer)

    st.session_state.messages.append({"role": "assistant", "content": answer})
