"""Cross-domain collaboration tools (genuine agent-to-agent consultation).

These let one domain agent reach a *peer* domain agent over A2A during its own
reasoning — the hybrid pattern: the orchestrator still owns the front-door
security gates, but agents may delegate to each other for true data dependencies
(e.g. Finance needs HR's headcount to compute a per-employee budget).

This mirrors ``consult_policy`` in ``governance_tools.py`` but adds two safeguards
that matter once agents call *each other* (rather than a leaf service like Policy):

1. Depth guard — a ContextVar bounds nested delegation so a misbehaving chain
   (A -> B -> A -> ...) cannot recurse without limit. NOTE: a ContextVar only
   bounds delegation *within a single process* (e.g. the DevUI single-process
   run, or nested tool calls in one agent). Across separate A2A server processes
   the counter does not propagate; bounding true cross-process cycles requires
   carrying a hop count in the request itself. For this prototype no peer grants
   a tool that calls back, so cross-process cycles cannot form.
2. Restricted-domain note — if a peer call targets an access-controlled domain
   (e.g. finance), it should be re-checked against ``_allowed`` from the mesh
   workflow before the hop. The example below only consults HR (open to all),
   so no re-check is needed here.
"""
import sys
import pathlib
import contextvars

project_root = str(pathlib.Path(__file__).resolve().parents[2])
if project_root not in sys.path:
    sys.path.insert(0, project_root)

from agent_framework import tool
from src.a2a.clients import ask_remote

# Bounds nested peer delegation within a single process (see module docstring).
_peer_depth: contextvars.ContextVar[int] = contextvars.ContextVar("peer_call_depth", default=0)
_MAX_PEER_DEPTH = 2


@tool(description="Consult the HR agent for the current headcount of a department.")
async def get_department_headcount(department: str) -> str:
    """Agent-to-agent hop: the Finance agent asks the HR agent (over A2A) for a
    department's headcount, e.g. to compute a per-employee budget."""
    depth = _peer_depth.get()
    if depth >= _MAX_PEER_DEPTH:
        return "PEER_LIMIT: delegation depth exceeded; aborting to prevent loops."
    token = _peer_depth.set(depth + 1)
    try:
        return await ask_remote(
            "hr",
            f"How many employees are in the {department} department? "
            f"Use your headcount tool and reply with just the number and department.",
        )
    except Exception as e:
        return f"HR_UNAVAILABLE: could not reach the HR agent ({e})."
    finally:
        _peer_depth.reset(token)


COLLABORATION_TOOLS = [get_department_headcount]
