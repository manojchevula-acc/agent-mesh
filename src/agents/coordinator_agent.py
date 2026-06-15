import sys
import pathlib

# Ensure project root is in sys.path
project_root = str(pathlib.Path(__file__).resolve().parents[2])
if project_root not in sys.path:
    sys.path.insert(0, project_root)

import os
from typing import Dict, Any, List
from agent_framework import Agent, Message
from src.agents.agent_factory import create_demo_agent
from src.agents.policy_retrieval_agent import get_policy_agent
from src.agents.compliance_agent import get_compliance_agent
from src.agents.approval_gate_agent import get_approval_agent
from src.agents.action_execution_agent import get_action_agent
from src.memory.session_store import FileSessionStore
from src.config import Config
from src.utils.console_logger import AgentLogger

COORDINATOR_INSTRUCTIONS = """
You are the Coordinator and Router Agent.
Your job is to analyze user queries, manage the overall multi-agent workflow, and synthesize the final summary response.

Synthesize instructions:
- Read the conversation history and specialist execution logs.
- Provide a clear, professional, and friendly response summarizing what happened: whether compliance passed, if approval was obtained (or not needed), and if the system action succeeded or failed.
"""

def get_coordinator_agent(log_path: str = None) -> Agent:
    return create_demo_agent(
        name="CoordinatorAgent",
        instructions=COORDINATOR_INSTRUCTIONS,
        log_path=log_path
    )

agent = get_coordinator_agent()


async def run_multi_agent_workflow(
    user_query: str, 
    session_id: str, 
    log_path: str = None
) -> str:
    """
    Orchestrates the multi-agent mesh workflow:
    1. Coordinator intercepts query.
    2. Policy Retrieval determines constraints.
    3. Compliance checks safety.
    4. Approval Gate evaluates permissions.
    5. Action Agent executes the task.
    6. Coordinator summarizes results.
    
    Ensures memory is persisted using FileSessionStore.
    """
    # Initialize Memory Store
    memory = FileSessionStore(storage_dir=Config.CONVERSATION_STORE_DIR)
    
    # Load past session context
    context_str = memory.get_context_summary(session_id)
    
    # 1. Initialize specialist agents
    coordinator = get_coordinator_agent(log_path=log_path)
    policy_agent = get_policy_agent(log_path=log_path)
    compliance_agent = get_compliance_agent(log_path=log_path)
    approval_agent = get_approval_agent(log_path=log_path)
    action_agent = get_action_agent(log_path=log_path)
    
    # Record user prompt in session
    memory.append_message(session_id, "user", user_query, "User")
    
    # 2. STEP 1: Routing (Coordinator analyzes and logs routing step)
    AgentLogger.print_agent_header("CoordinatorAgent", "Analyzing query and routing task")
    route_prompt = f"User Request: {user_query}\nContext:\n{context_str}\nDecide on agent routing."
    coordinator_res = await coordinator.run(route_prompt)
    coordinator_text = getattr(coordinator_res, "text", str(coordinator_res))
    AgentLogger.print_agent_response("CoordinatorAgent", coordinator_text)
    memory.append_message(session_id, "assistant", coordinator_text, "CoordinatorAgent (Routing)")
    
    # 3. STEP 2: Compliance Verification (Scan inputs for PII / safety)
    AgentLogger.print_agent_header("ComplianceAgent", "Evaluating query safety & PII scanner")
    compliance_prompt = f"Verify request for safety: '{user_query}'"
    compliance_res = await compliance_agent.run(compliance_prompt)
    compliance_text = getattr(compliance_res, "text", str(compliance_res))
    AgentLogger.print_agent_response("ComplianceAgent", compliance_text)
    memory.append_message(session_id, "assistant", compliance_text, "ComplianceAgent")
    
    if "failed" in compliance_text.lower() or "compliance_failed" in compliance_text.lower():
        # Short-circuit if compliance check fails
        AgentLogger.print_agent_header("CoordinatorAgent", "Short-circuiting workflow due to safety violation")
        fail_prompt = f"Request: {user_query}\nStatus: Failed Compliance Check\nReason: {compliance_text}. Synthesize summary."
        summary_res = await coordinator.run(fail_prompt)
        summary_text = getattr(summary_res, "text", str(summary_res))
        AgentLogger.print_agent_response("CoordinatorAgent", summary_text)
        memory.append_message(session_id, "assistant", summary_text, "CoordinatorAgent (Summary)")
        return summary_text
        
    # 4. STEP 3: Policy Retrieval (Look up rules)
    AgentLogger.print_agent_header("PolicyRetrievalAgent", "Querying policies.json for context boundaries")
    policy_prompt = f"Retrieve rules applicable to: '{user_query}'"
    policy_res = await policy_agent.run(policy_prompt)
    policy_text = getattr(policy_res, "text", str(policy_res))
    AgentLogger.print_agent_response("PolicyRetrievalAgent", policy_text)
    memory.append_message(session_id, "assistant", policy_text, "PolicyRetrievalAgent")
    
    # 5. STEP 4: Approval Checking (Check if manager approval is required and run human check)
    needs_approval = "requires manager approval" in policy_text.lower() or "restricted" in policy_text.lower()
    
    approval_text = "APPROVED: Auto-approved as request is within bounds."
    if needs_approval:
        AgentLogger.print_agent_header("ApprovalGateAgent", "Evaluating manager sign-off (Human-in-the-loop Gate)")
        approval_prompt = f"Request: '{user_query}'. Policy requires approval. Evaluate manager sign-off."
        approval_res = await approval_agent.run(approval_prompt)
        approval_text = getattr(approval_res, "text", str(approval_res))
        AgentLogger.print_agent_response("ApprovalGateAgent", approval_text)
        memory.append_message(session_id, "assistant", approval_text, "ApprovalGateAgent")
        
    if "denied" in approval_text.lower() or "rejected" in approval_text.lower():
        # Short-circuit if denied
        AgentLogger.print_agent_header("CoordinatorAgent", "Short-circuiting workflow due to approval rejection")
        fail_prompt = f"Request: {user_query}\nStatus: Denied by Manager\nReason: {approval_text}. Synthesize summary."
        summary_res = await coordinator.run(fail_prompt)
        summary_text = getattr(summary_res, "text", str(summary_res))
        AgentLogger.print_agent_response("CoordinatorAgent", summary_text)
        memory.append_message(session_id, "assistant", summary_text, "CoordinatorAgent (Summary)")
        return summary_text

    # 6. STEP 5: Action Execution (Execute simulated command)
    AgentLogger.print_agent_header("ActionAgent", "Executing provisioning system commands")
    action_prompt = f"Execute approved request: '{user_query}'. Verification: {approval_text} and {compliance_text}."
    action_res = await action_agent.run(action_prompt)
    action_text = getattr(action_res, "text", str(action_res))
    AgentLogger.print_agent_response("ActionAgent", action_text)
    memory.append_message(session_id, "assistant", action_text, "ActionAgent")
    
    # 7. STEP 6: Synthesis (Coordinator prepares the final response)
    AgentLogger.print_agent_header("CoordinatorAgent", "Synthesizing execution transcript details")
    final_prompt = (
        f"User query: '{user_query}'\n"
        f"Execution Summary:\n"
        f"- Compliance: {compliance_text}\n"
        f"- Policy retrieved: {policy_text}\n"
        f"- Approval: {approval_text}\n"
        f"- Action result: {action_text}\n"
        f"Generate the final summary response."
    )
    final_res = await coordinator.run(final_prompt)
    final_text = getattr(final_res, "text", str(final_res))
    AgentLogger.print_agent_response("CoordinatorAgent", final_text)
    memory.append_message(session_id, "assistant", final_text, "CoordinatorAgent (Summary)")
    
    return final_text
