"""Intelligence Tab — AI-powered analytics + interactive graph workbench.

Sub-tabs:
  1. Semantic Search  — natural-language query over the knowledge graph
  2. Retention Risk   — at-risk customers prioritised by LTV × churn signal
  3. Fraud Detection  — flagged customers, risk signals, recommended actions
  4. Dynamic Graph    — interactive traversal with highlight/fade on node/edge click
  5. Cypher Workbench — prebuilt + custom Cypher with live Neo4j execution

Requires: pipeline/seed_discovery.py + pipeline/generate_embeddings.py
          + pipeline/seed_graph_extensions.py
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import streamlit as st

from tabs.discovery_tab import render_semantic_search, render_retention, render_fraud
from tabs.cypher_studio_tab import render as render_cypher_studio
from tabs.unified_kg_explorer import render as render_unified_kg
from dynamic_cypher_tab import render as render_dynamic_cypher


def panel_title(text: str):
    st.markdown(f"<div class='panel-title'>{text}</div>", unsafe_allow_html=True)


# ══════════════════════════════════════════════════════════════════════════════
#  MAIN
# ══════════════════════════════════════════════════════════════════════════════

def render():
    sub_search, sub_retain, sub_fraud, sub_dyn, sub_cypher, sub_dynamic_cypher = st.tabs([
        "Semantic Search",
        "Retention Risk",
        "Fraud Detection",
        "Dynamic Graph",
        "Cypher Studio",
        "Dynamic Cypher",
    ])

    with sub_search:
        render_semantic_search()

    with sub_retain:
        render_retention()

    with sub_fraud:
        render_fraud()

    with sub_dyn:
        render_unified_kg()

    with sub_cypher:
        render_cypher_studio()

    with sub_dynamic_cypher:
        render_dynamic_cypher()