import asyncio
import os
import uuid
import sys
import pathlib

# Ensure project root is in sys.path
project_root = str(pathlib.Path(__file__).resolve().parent)
if project_root not in sys.path:
    sys.path.insert(0, project_root)

from src.config import Config
from src.utils.console_logger import AgentLogger
from src.agents.coordinator_agent import run_multi_agent_workflow
from src.memory.session_store import FileSessionStore

async def main():
    # 1. Validate environment configuration
    try:
        Config.validate()
    except ValueError as e:
        AgentLogger.print_error(str(e))
        return

    # Initialize store to print initial statistics
    memory = FileSessionStore(storage_dir=Config.CONVERSATION_STORE_DIR)

    # 2. Display Welcome Banner
    os.system("cls" if os.name == "nt" else "clear")
    print("\033[38;5;75m" + "=" * 80 + "\033[0m")
    print(f"\033[38;5;75m{AgentLogger.BOLD}      MICROSOFT AGENT FRAMEWORK - MULTI-AGENT MESH DEMO{AgentLogger.RESET}")
    print("\033[38;5;75m" + "=" * 80 + "\033[0m")
    print(f"{AgentLogger.DIM}Scenario: Policy-Aware Employee Action Request Assistant{AgentLogger.RESET}")
    print(f"Mode: LOCAL OLLAMA LLM ({Config.OLLAMA_MODEL})")
    print(f"Host: {Config.OLLAMA_HOST}")
    print(f"Data Dir: {os.path.abspath('data')}")
    print("\033[38;5;75m" + "-" * 80 + "\033[0m")
    print(f"Agent Mesh Topology:")
    print(f" 1. Coordinator/Router Agent       - Analyzes prompt & maps routing path")
    print(f" 2. Policy Retrieval Agent         - Resolves rules using 'policies.json'")
    print(f" 3. Compliance/Guardrail Agent     - Scans inputs, redacts PII, ensures compliance")
    print(f" 4. Approval Gate Agent            - Simulates Manager approval (Human-in-the-loop)")
    print(f" 5. Action/Execution Agent         - Simulates task execution (Access grant, payout)")
    print(f" 6. Observability Layer            - Scrubbed audit log tracking in 'audit_trail.jsonl'")
    print("\033[38;5;75m" + "=" * 80 + "\033[0m")
    print(f"Example queries to try:")
    print(f" - 'Can I access the finance folder?'  (Restricted -> Triggers Compliance & Approval)")
    print(f" - 'What is the travel reimbursement policy?'  (Policy Lookup only)")
    print(f" - 'Submit travel reimbursement for $200'  (Under $500 -> Auto-approved)")
    print(f" - 'Submit travel reimbursement for $650'  (>= $500 -> Requires Manager Approval)")
    print(f" - 'Request access for my SSN 000-12-3456 to finance' (Fails Compliance check)")
    print("\033[38;5;75m" + "=" * 80 + "\033[0m")

    # Generate a unique session ID for this conversation thread
    session_id = f"session_{uuid.uuid4().hex[:8]}"
    AgentLogger.print_system_info(f"New session initialized: {session_id}")
    
    # 3. Interactive Command Loop
    while True:
        try:
            print(f"\n{AgentLogger.COLOR_USER}{AgentLogger.BOLD}Enter query (or 'exit', 'clear' to reset history):{AgentLogger.RESET} ", end="")
            user_input = input().strip()
            
            if not user_input:
                continue
            if user_input.lower() in ("exit", "quit"):
                AgentLogger.print_system_info("Exiting demo. Goodbye!")
                break
            if user_input.lower() == "clear":
                memory.clear_session(session_id)
                AgentLogger.print_system_info(f"Conversation history cleared for session {session_id}.")
                continue
            
            # Print user header
            AgentLogger.print_user_prompt(user_input)
            
            # Execute workflow
            AgentLogger.print_system_info("Invoking multi-agent workflow...")
            final_summary = await run_multi_agent_workflow(
                user_query=user_input,
                session_id=session_id
            )
            
            # Print final success output
            AgentLogger.print_success("Final Agent Summary Output:")
            print(f"\n{final_summary}\n")
            print("\033[38;5;244m" + "-" * 80 + "\033[0m")
            
        except KeyboardInterrupt:
            AgentLogger.print_system_info("\nExiting demo. Goodbye!")
            break
        except Exception as e:
            AgentLogger.print_error(f"Execution Error: {e}")

if __name__ == "__main__":
    # Ensure event loop runs cleanly
    asyncio.run(main())
