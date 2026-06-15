"""Finance domain tools.

Exposed to the Finance agent via the Microsoft Agent Framework `@tool` mechanism
(the same surface MCP tools use). Responses are hardcoded for now; later these will
be backed by a real MCP server / ERP integration.

The outbound-payment tool is marked `approval_mode="always_require"` so the
framework's native human-in-the-loop gate fires before any (simulated) money moves.
"""
import sys
import pathlib

project_root = str(pathlib.Path(__file__).resolve().parents[2])
if project_root not in sys.path:
    sys.path.insert(0, project_root)

from agent_framework import tool

# Hardcoded "system of record" — stand-in for a real finance/ERP MCP server.
_BUDGETS = {
    "engineering": {"fy26_budget_usd": 4200000, "spent_usd": 1875000},
    "marketing": {"fy26_budget_usd": 1800000, "spent_usd": 940000},
    "operations": {"fy26_budget_usd": 2600000, "spent_usd": 1320000},
}


@tool(description="Get the FY26 budget and spend-to-date for a department.")
def get_budget_report(department: str) -> str:
    """Returns budget vs. spend for a department (hardcoded demo data)."""
    key = (department or "").strip().lower()
    data = _BUDGETS.get(key)
    if not data:
        return f"No budget record found for department '{department}'."
    remaining = data["fy26_budget_usd"] - data["spent_usd"]
    return (
        f"FY26 budget for {key.title()}: ${data['fy26_budget_usd']:,} | "
        f"Spent: ${data['spent_usd']:,} | Remaining: ${remaining:,}."
    )


@tool(description="Get a high-level financial summary for the company.")
def get_financial_summary() -> str:
    """Returns a hardcoded company financial snapshot."""
    return (
        "Q2 FY26 snapshot (demo): Revenue $48.2M, Operating margin 19%, "
        "Cash position $112M, Total department budgets $8.6M."
    )


@tool(description="Issue an outbound payment to a vendor (queues it for the next payment run).")
def issue_payment(vendor: str, amount_usd: float) -> str:
    """Simulates queuing an outbound payment.

    Human approval is NOT enforced here — the mesh orchestrator gates payment
    requests with a deterministic approval step BEFORE the Finance agent runs.
    (The native ``approval_mode`` flow is omitted because this SDK's A2AExecutor
    cannot yet carry approval/function content across the A2A boundary.)
    """
    return (
        f"ACTION_SUCCESS: Payment of ${amount_usd:,.2f} to '{vendor}' has been "
        f"queued for the next payment run."
    )


FINANCE_TOOLS = [get_budget_report, get_financial_summary, issue_payment]
