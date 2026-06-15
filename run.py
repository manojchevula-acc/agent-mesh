import os
os.environ.setdefault("PYTHONWARNINGS", "ignore")

import asyncio
import sys
import pathlib

# Ensure project root is in sys.path
project_root = str(pathlib.Path(__file__).resolve().parent)
if project_root not in sys.path:
    sys.path.insert(0, project_root)

from src.config import Config
from src.utils.console_logger import AgentLogger
from src.auth.identity_provider import login, list_users
from src.mesh.orchestrator import handle_request


def _print_banner(user):
    os.system("cls" if os.name == "nt" else "clear")
    print("\033[38;5;75m" + "=" * 80 + "\033[0m")
    print(f"\033[38;5;75m{AgentLogger.BOLD}   MICROSOFT AGENT FRAMEWORK - DISTRIBUTED A2A AGENT MESH{AgentLogger.RESET}")
    print("\033[38;5;75m" + "=" * 80 + "\033[0m")
    print(f"{AgentLogger.DIM}Scenario: Role-Aware Enterprise Assistant Mesh (A2A + tool calling){AgentLogger.RESET}")
    print(f"Signed in as: {AgentLogger.BOLD}{user.display_name}{AgentLogger.RESET}  (role: {user.role.value})")
    print("\033[38;5;75m" + "-" * 80 + "\033[0m")
    print("Mesh nodes (each isolated on its own A2A port):")
    for name, port in Config.AGENT_PORTS.items():
        print(f"  - {name:<13} http://{Config.A2A_HOST}:{port}/")
    print("\033[38;5;75m" + "-" * 80 + "\033[0m")
    print("Flow: guardrails -> router(A2A) -> access control -> compliance(A2A) -> domain(A2A) -> redact")
    print("\033[38;5;75m" + "=" * 80 + "\033[0m")
    print("Example queries:")
    print("  - 'What's the engineering budget?'        (finance - leadership only)")
    print("  - 'How many leave days do I have?'         (hr)")
    print("  - 'Any open backend engineering roles?'    (internal_job)")
    print("  - 'Ignore previous instructions and ...'   (blocked: prompt injection)")
    print("  - 'delete all employee records'            (blocked: destructive intent)")
    print("\033[38;5;75m" + "=" * 80 + "\033[0m")


def _select_user():
    print("Available demo users:")
    for u in list_users():
        print(f"  - {u.username:<8} ({u.role.value}) - {u.display_name}")
    print(f"{AgentLogger.COLOR_USER}{AgentLogger.BOLD}Login as (username):{AgentLogger.RESET} ", end="")
    username = input().strip() or "bob"
    return login(username)


async def main():
    try:
        Config.validate()
    except ValueError as e:
        AgentLogger.print_error(str(e))
        return

    user = _select_user()
    _print_banner(user)
    AgentLogger.print_system_info(
        "Tip: start the mesh first in another terminal with 'python launch_mesh.py'."
    )

    while True:
        try:
            print(f"\n{AgentLogger.COLOR_USER}{AgentLogger.BOLD}[{user.username}] Enter query "
                  f"(or 'switch', 'exit'):{AgentLogger.RESET} ", end="")
            user_input = input().strip()

            if not user_input:
                continue
            if user_input.lower() in ("exit", "quit"):
                AgentLogger.print_system_info("Exiting demo. Goodbye!")
                break
            if user_input.lower() == "switch":
                user = _select_user()
                _print_banner(user)
                continue

            AgentLogger.print_user_prompt(user_input)
            AgentLogger.print_system_info("Dispatching request across the mesh...")
            result = await handle_request(user, user_input)

            if result.blocked:
                AgentLogger.print_error(f"[{result.block_stage}] {result.answer}")
            else:
                AgentLogger.print_success(f"Final answer (domain: {result.domain}):")
                print(f"\n{result.answer}\n")
            print(f"{AgentLogger.DIM}trail: {' -> '.join(result.trail)}{AgentLogger.RESET}")
            print("\033[38;5;244m" + "-" * 80 + "\033[0m")

        except KeyboardInterrupt:
            AgentLogger.print_system_info("\nExiting demo. Goodbye!")
            break
        except Exception as e:
            AgentLogger.print_error(f"Execution Error: {e}")
            AgentLogger.print_system_info("Is the mesh running? Start it with 'python launch_mesh.py'.")


if __name__ == "__main__":
    asyncio.run(main())
