import streamlit as st

from dynamic_cypher_tab import render


st.set_page_config(
    page_title="Dynamic Cypher AI",
    page_icon="🔎",
    layout="wide"
)

render()