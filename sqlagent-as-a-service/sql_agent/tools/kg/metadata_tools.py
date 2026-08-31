"""Approach B templates as agent tools.

These exist for the configuration the reference doc assumes — an LLM tool-selector choosing a
template and extracting its parameters. This deployment currently runs with both fixed tiers
off, so the deterministic matcher in kg/retrieval.py is the live path; these are bound only
when KG_TOOLS_ENABLED=true, so the doc's variant is one flag away if the tiers return.

Every tool degrades to a typed error envelope rather than raising when the KG is unavailable:
a metadata lookup failing must not fail the turn.
"""

from langchain_core.tools import tool

from sql_agent.formatting import format_error
from sql_agent.kg.client import get_kg_client
from sql_agent.kg.templates import TEMPLATES_BY_NAME, run_join_path, run_template
from sql_agent.logging_config import get_logger

log = get_logger("kg.tools")

_UNAVAILABLE = "The metadata knowledge graph is not available (not enabled, or not built)."


def _run_entity_template(template_name: str, **params) -> dict:
    """Shared body: resolve the client, run one pre-authored template, envelope the result."""
    client = get_kg_client()
    if client is None:
        return format_error("KGUnavailable", _UNAVAILABLE, retryable=False)
    template = TEMPLATES_BY_NAME[template_name]
    rows = run_template(template, params, client)
    log.info("KG tool | %s(%s) -> %d object(s)", template_name, params, len(rows))
    return {
        "status": "success",
        "tool": template_name,
        "params": params,
        "kg_fingerprint": client.snapshot().fingerprint,
        "objects": rows,
        "rows_returned": len(rows),
    }


@tool
def get_customer_metadata(customer_id: str) -> dict:
    """Look up the SCHEMA METADATA needed to answer a question about one named customer.

    Returns every governed table/view keyed by customer_id, with its columns, grain and
    purpose. Returns metadata only — no customer data. Pass an id like 'CUST002'."""
    return _run_entity_template("get_customer_metadata", customer_id=customer_id)


@tool
def get_deal_metadata(deal_id: str) -> dict:
    """Look up the SCHEMA METADATA needed to answer a question about one named deal.

    Returns every governed table/view keyed by deal_id, with its columns, grain and purpose.
    Returns metadata only — no deal data. Pass an id like 'DEAL010'."""
    return _run_entity_template("get_deal_metadata", deal_id=deal_id)


@tool
def get_product_metadata(product_id: str) -> dict:
    """Look up the SCHEMA METADATA needed to answer a question about one named product.

    Returns every governed table/view keyed by product_id, with its columns, grain and
    purpose. Returns metadata only — no product data. Pass an id like 'PROD002'."""
    return _run_entity_template("get_product_metadata", product_id=product_id)


@tool
def get_join_path(table_a: str, table_b: str) -> dict:
    """Find the validated join path between two governed tables.

    Returns the tables along the path (including any bridge table) and the exact ON keys for
    each hop, taken from the knowledge graph's foreign-key edges. An empty path means the two
    tables may NOT be joined — do not invent a relationship."""
    client = get_kg_client()
    if client is None:
        return format_error("KGUnavailable", _UNAVAILABLE, retryable=False)
    result = run_join_path(table_a, table_b, client)
    log.info("KG tool | get_join_path(%s, %s) -> %s", table_a, table_b, result["tables"])
    return {
        "status": "success",
        "tool": "get_join_path",
        "params": {"table_a": table_a, "table_b": table_b},
        "kg_fingerprint": client.snapshot().fingerprint,
        **result,
        "rows_returned": len(result["tables"]),
    }
