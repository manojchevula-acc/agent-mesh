"""Internal Job domain tools.

The Internal Job agent searches internal postings loaded from
``data/job_postings.json``. Exposed via the Agent Framework `@tool` mechanism;
later this will be backed by a real ATS MCP server.
"""
import sys
import json
import pathlib

project_root = str(pathlib.Path(__file__).resolve().parents[2])
if project_root not in sys.path:
    sys.path.insert(0, project_root)

from agent_framework import tool
from src.config import Config


def _load_postings() -> list[dict]:
    try:
        with open(Config.JOB_POSTINGS_FILE, "r", encoding="utf-8") as f:
            return json.load(f).get("postings", [])
    except Exception:
        return []


@tool(description="Search internal job postings by keyword, department, or skill.")
def search_job_postings(query: str) -> str:
    """Returns matching internal postings (keyword match over the JSON KB)."""
    postings = _load_postings()
    if not postings:
        return "No internal postings are currently available."
    q = (query or "").strip().lower()
    if not q:
        matches = postings
    else:
        terms = [t for t in q.replace(",", " ").split() if t]
        matches = [
            p for p in postings
            if any(t in " ".join([
                p.get("title", ""), p.get("department", ""), p.get("level", ""),
                p.get("location", ""), " ".join(p.get("skills", [])), p.get("description", ""),
            ]).lower() for t in terms)
        ]
    if not matches:
        return f"No internal postings matched '{query}'."
    lines = [
        f"- [{p['id']}] {p['title']} ({p['department']}, {p['level']}) - {p['location']}; "
        f"skills: {', '.join(p.get('skills', []))}"
        for p in matches
    ]
    return f"Found {len(matches)} internal posting(s):\n" + "\n".join(lines)


@tool(description="Get full details for a specific internal posting by its ID (e.g. ENG-2041).")
def get_posting_details(posting_id: str) -> str:
    """Returns full details for one posting id."""
    pid = (posting_id or "").strip().upper()
    for p in _load_postings():
        if p.get("id", "").upper() == pid:
            return (
                f"{p['id']}: {p['title']}\nDepartment: {p['department']} | Level: {p['level']}\n"
                f"Location: {p['location']}\nSkills: {', '.join(p.get('skills', []))}\n"
                f"Open to: {p.get('open_to', 'all_employees')}\n{p.get('description', '')}"
            )
    return f"No posting found with ID '{posting_id}'."


JOB_TOOLS = [search_job_postings, get_posting_details]
