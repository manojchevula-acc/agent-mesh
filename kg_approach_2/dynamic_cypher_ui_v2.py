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

st.title("🤖 Dynamic Cypher AI Assistant")

st.caption(
    "Ask business questions in natural language. "
    "No Cypher knowledge required."
)


# ---------------------------------------------------------
# Welcome message
# ---------------------------------------------------------

if not st.session_state.messages:

    st.info(
        "👋 Welcome! Ask me questions about customers, deals, "
        "products, policy exceptions and other information "
        "available in the Neo4j Knowledge Graph."
    )

    st.markdown("### Try a question")

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