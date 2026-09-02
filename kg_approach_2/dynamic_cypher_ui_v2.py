import streamlit as st
import pandas as pd

from dynamic_cypher_engine import ask_question


# ---------------------------------------------------------
# Page configuration
# ---------------------------------------------------------

st.set_page_config(
    page_title="Dynamic Cypher AI",
    page_icon="🔎",
    layout="wide"
)

st.markdown(
    """
    <style>

    /* Overall page */
    .stApp {
        background: #faf8fe;
    }

    /* Main title */
    .main-title {
        color: #460073;
        font-size: 2rem;
        font-weight: 800;
        margin-bottom: 0.15rem;
    }

    .main-subtitle {
        color: #6b6b7b;
        font-size: 0.95rem;
        margin-bottom: 1.2rem;
    }

    /* Welcome card */
    .welcome-card {
        background: linear-gradient(135deg, #f4eefc, #ffffff);
        border: 1px solid #e6dbf7;
        border-left: 5px solid #a100ff;
        border-radius: 14px;
        padding: 1.3rem 1.5rem;
        margin-bottom: 1.2rem;
        box-shadow: 0 2px 8px rgba(70,0,115,.06);
    }

    .welcome-title {
        color: #460073;
        font-size: 1.25rem;
        font-weight: 750;
    }

    .welcome-text {
        color: #5f5f6f;
        margin-top: .35rem;
        font-size: .92rem;
    }

    /* Assistant answer */
    .answer-card {
        background: #ffffff;
        border: 1px solid #e6dbf7;
        border-left: 4px solid #a100ff;
        border-radius: 12px;
        padding: 1rem 1.2rem;
        margin: .35rem 0 .8rem;
        box-shadow: 0 2px 7px rgba(70,0,115,.05);
    }

    .answer-label {
        color: #460073;
        font-size: .78rem;
        font-weight: 750;
        text-transform: uppercase;
        letter-spacing: .04em;
        margin-bottom: .45rem;
    }

    /* Suggested question buttons */
    div[data-testid="stButton"] > button {
        border-radius: 10px;
        border: 1px solid #e6dbf7;
        background: #ffffff;
        color: #460073;
        min-height: 48px;
    }

    div[data-testid="stButton"] > button:hover {
        border-color: #a100ff;
        background: #f4eefc;
        color: #a100ff;
    }

    /* Expanders */
    div[data-testid="stExpander"] {
        border: 1px solid #e6dbf7;
        border-radius: 10px;
        background: #ffffff;
    }

    </style>
    """,
    unsafe_allow_html=True
)

# ---------------------------------------------------------
# Session state
# ---------------------------------------------------------

if "messages" not in st.session_state:
    st.session_state.messages = []


# ---------------------------------------------------------
# Sidebar
# ---------------------------------------------------------

with st.sidebar:

    st.title("🔎 Dynamic Cypher AI")

    st.markdown("### Navigation")

    st.button(
        "💬 AI Chat",
        width="stretch"
    )

    st.button(
        "📋 Sample Questions",
        width="stretch"
    )

    st.button(
        "ℹ️ About",
        width="stretch"
    )

    st.divider()

    st.markdown("### System")

    st.success("🟢 Neo4j Connected")

    st.divider()

    st.markdown("### Dynamic Cypher")

    st.caption("LLM generates and validates Cypher before execution.")

    st.metric(
        "Max Retry Attempts",
        "3"
    )

    st.divider()

    if st.button(
        "🗑️ Clear Conversation",
        width="stretch"
    ):
        st.session_state.messages = []
        st.rerun()


# ---------------------------------------------------------
# Main header
# ---------------------------------------------------------

st.markdown(
    '<div class="main-title">🤖 Dynamic Cypher AI Assistant</div>',
    unsafe_allow_html=True
)

st.markdown(
    '<div class="main-subtitle">'
    'Ask business questions in natural language. '
    'No Cypher knowledge required.'
    '</div>',
    unsafe_allow_html=True
)


# ---------------------------------------------------------
# Welcome / Suggested questions
# ---------------------------------------------------------

if not st.session_state.messages:

    st.markdown(
        """
        <div class="welcome-card">
            <div class="welcome-title">
                👋 Ask your business question
            </div>
            <div class="welcome-text">
                Explore customers, deals, products and policy exceptions
                using natural language. The assistant will find the relevant
                information from the Knowledge Graph.
            </div>
        </div>
        """,
        unsafe_allow_html=True
    )

    st.markdown("### Suggested questions")

    sample_questions = [
        "Which products have more than 5 deals, and how many deals does each have?",
        "Which customers have the largest total margin shortfall from policy exceptions?",
        "Which business rule is associated with the most policy exceptions?"
    ]

    for i, question in enumerate(sample_questions):

        if st.button(
            question,
            key=f"sample_{i}",
            width="stretch"
        ):
            st.session_state.pending_question = question
            st.rerun()

# ---------------------------------------------------------
# Display conversation
# ---------------------------------------------------------

for message in st.session_state.messages:

    with st.chat_message(message["role"]):

        if message["role"] == "user":

            st.markdown(message["content"])

        else:

            result = message["result"]

            st.markdown(result["answer"])

            with st.expander("🔍 Generated Cypher"):

                st.code(
                    result["cypher"],
                    language="cypher"
                )

            records = result.get("records", [])

            if records:

                with st.expander(
                    f"📊 Query Results ({len(records)} rows)"
                ):

                    df = pd.DataFrame(records)

                    st.dataframe(
                        df,
                        width="stretch",
                        hide_index=True
                    )


# ---------------------------------------------------------
# Handle sample question
# ---------------------------------------------------------

pending_question = st.session_state.pop(
    "pending_question",
    None
)


# ---------------------------------------------------------
# Chat input
# ---------------------------------------------------------

question = st.chat_input(
    "Ask a business question..."
)

if pending_question:
    question = pending_question


# ---------------------------------------------------------
# Process question
# ---------------------------------------------------------

if question:

    # User message
    with st.chat_message("user"):

        st.markdown(question)

    st.session_state.messages.append(
        {
            "role": "user",
            "content": question
        }
    )

    # Assistant
    with st.chat_message("assistant"):

        with st.spinner(
            "Generating, validating and executing..."
        ):

            try:

                result = ask_question(question)

            except Exception as e:

                st.error(
                    f"Unable to process the question: {e}"
                )

                st.stop()

        st.markdown(result["answer"])

        with st.expander("🔍 Generated Cypher"):

            st.code(
                result["cypher"],
                language="cypher"
            )

        records = result.get("records", [])

        if records:

            with st.expander(
                f"📊 Query Results ({len(records)} rows)"
            ):

                df = pd.DataFrame(records)

                st.dataframe(
                    df,
                    width="stretch",
                    hide_index=True
                )

    # Save assistant response
    st.session_state.messages.append(
        {
            "role": "assistant",
            "result": result
        }
    )
