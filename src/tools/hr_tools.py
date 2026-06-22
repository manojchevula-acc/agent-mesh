"""HR domain tools.

Exposed to the HR agent via the Agent Framework `@tool` mechanism. Hardcoded for
now; later backed by a real HRIS MCP server.
"""
import sys
import pathlib

project_root = str(pathlib.Path(__file__).resolve().parents[2])
if project_root not in sys.path:
    sys.path.insert(0, project_root)

from agent_framework import tool


@tool(description="Get the leave / PTO balance summary for the current employee.")
def get_leave_balance() -> str:
    """Returns hardcoded leave balances for the requesting employee."""
    return (
        "Leave balances (demo): Annual leave 14.5 days, Sick leave 8 days, "
        "Carry-over expiring 31 Mar: 3 days."
    )


@tool(description="Get a summary of available employee benefits.")
def get_benefits_summary() -> str:
    """Returns a hardcoded benefits overview."""
    return (
        "Benefits (demo): Medical/Dental/Vision (PPO + HDHP), 401(k) 6% match, "
        "$1,500 annual learning stipend, 16 weeks parental leave, ESPP 15% discount."
    )


@tool(description="Look up an HR policy by topic (e.g. 'remote work', 'expenses', 'leave').")
def get_hr_policy(topic: str) -> str:
    """Returns a hardcoded HR policy snippet for a topic."""
    policies = {
        "remote work": "Remote work is available up to 3 days/week with manager approval.",
        "leave": "Submit leave requests at least 5 business days in advance via the portal.",
        "expenses": "Expenses under $500 auto-approve; $500+ requires manager sign-off.",
        "hours": "Standard hours are 9:00 AM - 5:00 PM local time.",
    }
    key = (topic or "").strip().lower()
    return policies.get(key, f"No specific HR policy found for '{topic}'. Contact People Ops.")


# HR is the system of record for headcount — other domains consult HR for it.
_HEADCOUNT = {"engineering": 35, "marketing": 12, "operations": 21}


@tool(description="Get the current employee headcount for a department.")
def get_headcount(department: str) -> str:
    """Returns the headcount for a department (hardcoded demo data)."""
    key = (department or "").strip().lower()
    count = _HEADCOUNT.get(key)
    if count is None:
        return f"No headcount record found for department '{department}'."
    return f"The {key.title()} department currently has {count} employees."


HR_TOOLS = [get_leave_balance, get_benefits_summary, get_hr_policy, get_headcount]
