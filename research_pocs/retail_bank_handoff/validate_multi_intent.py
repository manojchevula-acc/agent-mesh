"""Live acceptance trace for the native MAF multi-intent handoff workflow.

Run from retail_bank_handoff after configuring the normal agent environment:
    python validate_multi_intent.py
"""

import argparse
import asyncio
import json
import pathlib
import sys

from dotenv import load_dotenv

_HERE = pathlib.Path(__file__).resolve().parent
_ENV_PARENT = _HERE / ".." / ".." / "agent-mesh" / ".env"
_ENV_LOCAL = _HERE / ".env"

if _ENV_PARENT.exists():
    load_dotenv(_ENV_PARENT, override=False)
if _ENV_LOCAL.exists():
    load_dotenv(_ENV_LOCAL, override=True)

sys.path.insert(0, str(_HERE))

from agent_framework import Content
from agent_framework._types import AgentResponseUpdate
from agent_framework.orchestrations import HandoffAgentUserRequest
from agents.agent_factory import create_agents
from workflows.handoff_workflow import build_workflow


DEFAULT_REQUEST = "Please check my account balance, card status, and personal-loan eligibility."
DEFAULT_DETAILS = "My account number is 1234567890, my card ends in 1234, and I need a personal loan of 300000."


def _record_output(event, trace: dict) -> None:
    data = event.data
    agent = event.executor_id or "agent"
    if not isinstance(data, AgentResponseUpdate):
        text = getattr(data, "text", "") or ""
        if text:
            trace["response_text"][agent] = trace["response_text"].get(agent, "") + text
        return

    for item in getattr(data, "contents", []):
        if getattr(item, "type", None) == "function_call":
            trace["tool_calls"].append({
                "agent": agent,
                "tool": getattr(item, "name", "unknown"),
                "arguments": getattr(item, "parse_arguments", lambda: {})() or {},
            })
        text = getattr(item, "text", "") or ""
        if text:
            trace["response_text"][agent] = trace["response_text"].get(agent, "") + text


async def _drain(stream, trace: dict) -> list:
    pending = []
    async for event in stream:
        if event.type == "executor_invoked":
            agent = event.executor_id or "unknown"
            if not trace["agent_path"] or trace["agent_path"][-1] != agent:
                trace["agent_path"].append(agent)
        elif event.type == "handoff_sent":
            trace["handoffs"].append({
                "from": getattr(event.data, "source", "?"),
                "to": getattr(event.data, "target", "?"),
            })
        elif event.type == "output":
            _record_output(event, trace)
        elif event.type == "request_info":
            pending.append(event)
    return pending


def _report(trace: dict, expected_agents: list[str]) -> int:
    path = trace["agent_path"]
    response_text = trace["response_text"]
    handoff_targets = [handoff["to"] for handoff in trace["handoffs"]]
    expected_handoff_targets = [*expected_agents, "triage_agent"]
    expected_tools = {
        "account_agent": "get_account_balance",
        "card_agent": "get_card_status",
        "loan_agent": "check_loan_eligibility",
        "transfer_agent": "authorize_large_transfer",
        "fraud_agent": "flag_suspicious_transaction",
    }
    observed_tools = {call["tool"] for call in trace["tool_calls"]}
    required_tools = {expected_tools[agent] for agent in expected_agents}
    triage_text = response_text.get("triage_agent", "").lower()
    final_triage_response = (
        handoff_targets[:len(expected_handoff_targets)] == expected_handoff_targets
        and all(term in triage_text for term in ("account", "card", "loan"))
    )
    result = {
        "expected_agents": expected_agents,
        "agent_path": path,
        "handoffs": trace["handoffs"],
        "tool_calls": trace["tool_calls"],
        "response_text": response_text,
        "checks": {
            "all_expected_agents_activated": all(agent in path for agent in expected_agents),
            "expected_native_handoff_sequence_observed": handoff_targets[:len(expected_handoff_targets)] == expected_handoff_targets,
            "all_expected_domain_tools_observed": required_tools <= observed_tools,
            "final_triage_consolidation_observed": final_triage_response,
        },
    }
    print(json.dumps(result, indent=2))
    return 0 if all(result["checks"].values()) else 1


async def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--request", default=DEFAULT_REQUEST)
    parser.add_argument("--details", default=DEFAULT_DETAILS)
    parser.add_argument(
        "--expected-agents",
        default="account_agent,card_agent,loan_agent",
        help="Comma-separated agents expected for this scenario.",
    )
    args = parser.parse_args()
    trace = {"agent_path": [], "handoffs": [], "tool_calls": [], "response_text": {}}

    workflow = build_workflow(*create_agents())
    pending = await _drain(workflow.run(args.request, stream=True), trace)

    responses = {}
    for request in pending:
        if isinstance(request.data, HandoffAgentUserRequest):
            responses[request.request_id] = HandoffAgentUserRequest.create_response(args.details)
        elif isinstance(request.data, Content) and request.data.type == "function_approval_request":
            raise RuntimeError("This validator does not auto-approve tools. Use main.py for approval scenarios.")

    if not responses:
        raise RuntimeError("The workflow did not request the supplied customer details.")

    await _drain(workflow.run(responses=responses, stream=True), trace)
    expected_agents = [agent.strip() for agent in args.expected_agents.split(",") if agent.strip()]
    return _report(trace, expected_agents)


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))