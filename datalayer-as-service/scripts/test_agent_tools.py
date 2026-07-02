"""
scripts/test_agent_tools.py
---------------------------
Validates the MCP tool layer (mcp_server/tools.py) without an LLM:

  1. Calls every query_* tool function with a small sample filter.
  2. Confirms each returns a JSON-serialisable list of dicts.
  3. Confirms NO tool issues SQL against fab_curated (semantic-only guarantee)
     by inspecting the tools source for 'fab_curated'.

Run from the project root:
    python scripts/test_agent_tools.py
"""

import sys
import os
import json
import inspect

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from dotenv import load_dotenv
import mcp_server.tools as tools

SEPARATOR = "-" * 60

# (function, kwargs) pairs — small, safe sample filters.
CASES = [
    (tools.query_customer_360, {"customer_id": "CUST001"}),
    (tools.query_pricing_recommendation, {"customer_id": "CUST001"}),
    (tools.query_margin_analysis, {"customer_id": "CUST001"}),
    (tools.query_profitability_summary, {"customer_id": "CUST005"}),
    (tools.query_rwa_impact, {"customer_id": "CUST005"}),
    (tools.query_new_customer_pricing, {"segment": "SME"}),
    (tools.query_competitor_price_analysis, {"customer_id": "CUST001"}),
    (tools.query_pricing_trace, {"customer_id": "CUST001"}),
    (tools.query_segment_pricing_benchmark, {"segment": "SME"}),
    (tools.query_operations_cost_impact, {"customer_segment": "SME"}),
    (tools.query_relationship_discount, {"customer_id": "CUST001"}),
    (tools.query_win_loss_insights, {"customer_id": "CUST001"}),
    (tools.query_policy_exception, {"customer_id": "CUST001"}),
    (tools.query_non_compliant_deals, {}),
    (tools.query_compare_fab_vs_competitor, {"customer_id": "CUST001"}),
]


def print_section(title: str) -> None:
    print(f"\n{SEPARATOR}\n  {title}\n{SEPARATOR}")


def main() -> None:
    print("\n==========================================")
    print("  FAB MCP Tools – Agent Tool Validation")
    print("==========================================")

    load_dotenv()

    # --- Static guarantee: tools query fab_semantic only ---
    print_section("Semantic-only guarantee")
    src = inspect.getsource(tools)
    if "fab_curated" in src or "fab_raw" in src:
        print("  FAIL: tools.py references fab_curated / fab_raw directly.")
        sys.exit(1)
    print("  PASS: tools.py references only fab_semantic views.")

    # --- Runtime: each tool returns JSON-friendly list[dict] ---
    print_section("Tool invocation results")
    failures = 0
    for func, kwargs in CASES:
        try:
            result = func(**kwargs)
            json.dumps(result, default=str)  # must be serialisable
            assert isinstance(result, list) and (not result or isinstance(result[0], dict))
            preview = result[0] if result else {}
            keys = list(preview.keys())[:4]
            print(f"  OK   {func.__name__:<34} rows={len(result)} keys={keys}")
        except Exception as exc:
            failures += 1
            print(f"  FAIL {func.__name__:<34} {exc}")

    print(f"\n{SEPARATOR}")
    if failures:
        print(f"  {failures} tool(s) failed. Ensure MySQL is running and views exist.")
        sys.exit(1)
    print("  All MCP tools returned JSON-friendly data.")
    print(SEPARATOR)


if __name__ == "__main__":
    main()
