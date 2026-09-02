import streamlit as st
import pandas as pd

from dynamic_cypher_engine import ask_question


def panel_title(text: str):
    st.markdown(
        f"<div class='panel-title'>{text}</div>",
        unsafe_allow_html=True
    )


def render():
    panel_title("Dynamic Cypher AI Assistant")

    st.markdown(
        "Ask business questions in natural language. "
        "The system generates, validates and executes Cypher against "
        "the Neo4j Knowledge Graph."
    )

    # ---------------------------------------------------------
    # Chat history
    # ---------------------------------------------------------

    if "dynamic_cypher_messages" not in st.session_state:
        st.session_state.dynamic_cypher_messages = []

    # ---------------------------------------------------------
    # Display previous conversation
    # ---------------------------------------------------------

    for message in st.session_state.dynamic_cypher_messages:

        with st.chat_message(message["role"]):

            if message["role"] == "user":
                st.markdown(message["content"])

            else:
                result = message["result"]

                st.markdown("### Business Answer")
                st.markdown(result["answer"])

                # Technical details
                with st.expander("🔍 View Generated Cypher"):
                    st.code(
                        result["cypher"],
                        language="cypher"
                    )

                records = result.get("records", [])

                if records:

                    with st.expander(
                        f"📊 View Query Results ({len(records)} rows)"
                    ):

                        df = pd.DataFrame(records)

                        st.dataframe(
                            df,
                            use_container_width=True,
                            hide_index=True
                        )

                        st.download_button(
                            "⬇️ Download Results as CSV",
                            df.to_csv(index=False),
                            "dynamic_cypher_results.csv",
                            "text/csv",
                            key=message["key"]
                        )

    # ---------------------------------------------------------
    # Chat input
    # ---------------------------------------------------------

    question = st.chat_input(
        "Ask a business question..."
    )

    if question:

        # Display user question immediately
        with st.chat_message("user"):
            st.markdown(question)

        # Save user message
        st.session_state.dynamic_cypher_messages.append(
            {
                "role": "user",
                "content": question
            }
        )

        # Process question
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
                    return

            # Business answer
            st.markdown("### Business Answer")
            st.markdown(result["answer"])

            # Generated Cypher
            with st.expander("🔍 View Generated Cypher"):

                st.code(
                    result["cypher"],
                    language="cypher"
                )

            # Neo4j results
            records = result.get("records", [])

            if records:

                with st.expander(
                    f"📊 View Query Results ({len(records)} rows)"
                ):

                    df = pd.DataFrame(records)

                    st.dataframe(
                        df,
                        use_container_width=True,
                        hide_index=True
                    )

                    st.download_button(
                        "⬇️ Download Results as CSV",
                        df.to_csv(index=False),
                        "dynamic_cypher_results.csv",
                        "text/csv",
                        key=f"download_{len(st.session_state.dynamic_cypher_messages)}"
                    )

            else:
                st.info("The query returned no results.")

        # Save assistant response
        st.session_state.dynamic_cypher_messages.append(
            {
                "role": "assistant",
                "result": result,
                "key": f"download_{len(st.session_state.dynamic_cypher_messages)}"
            }
        )

    # ---------------------------------------------------------
    # Clear conversation
    # ---------------------------------------------------------

    if st.session_state.dynamic_cypher_messages:

        if st.button(
            "🗑️ Clear Conversation",
            key="clear_dynamic_cypher"
        ):

            st.session_state.dynamic_cypher_messages = []

            st.rerun()