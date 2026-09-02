import streamlit as st

st.set_page_config(
    page_title="Chat Test",
    layout="wide"
)

st.title("Chat Test")

st.write("If you can see the chat box at the bottom, Streamlit chat_input works.")

question = st.chat_input("Ask something...")

if question:
    st.write("You asked:", question)    