import os
os.environ.setdefault("PYTHONWARNINGS", "ignore")

import asyncio
import sys
import pathlib

# Reconfigure stdout/stderr to UTF-8 so Rich's box-drawing and other Unicode
# characters don't crash on Windows terminals set to legacy cp1252 encoding.
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

# Ensure project root is in sys.path
project_root = str(pathlib.Path(__file__).resolve().parent)
if project_root not in sys.path:
    sys.path.insert(0, project_root)

# Activate the Agent Framework SDK's built-in OpenTelemetry instrumentation +
# centralized logging via the single mesh setup point. This auto-traces
# agent.run(), @tool calls, LLM get_response(), and workflow execution across the
# orchestrator. Exporter wiring is driven by OBS_PROFILE (dev=OTLP/console,
# prod=Azure Monitor). See src/observability/setup.py and .env.example.
from src.observability import setup_observability
setup_observability(service_name="agent_mesh_cli")

from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich import box

from src.config import Config
from src.auth.identity_provider import login, list_users
from src.mesh.orchestrator import handle_request
from src.tracing.execution_trace import ExecutionTracer, set_active_tracer, clear_active_tracer
from src.tracing.cli_renderer import CLIRenderer

# ---------------------------------------------------------------------------
# Parse CLI flags
# ---------------------------------------------------------------------------

_VERBOSE = "--verbose" in sys.argv or "-v" in sys.argv
_EXPLAIN = "--explain" in sys.argv or "-e" in sys.argv

# A single shared Rich console for the session.
# legacy_windows=False forces ANSI/VT100 rendering (avoids cp1252 issues on Windows).
_console = Console(highlight=False, legacy_windows=False)


# ---------------------------------------------------------------------------
# Startup banner
# ---------------------------------------------------------------------------

def _print_banner(user) -> None:
    os.system("cls" if os.name == "nt" else "clear")
    _console.print()
    _console.print(Panel(
        "[bold cyan]MICROSOFT AGENT FRAMEWORK — DISTRIBUTED A2A AGENT MESH[/bold cyan]\n"
        "[dim]FAB Banking Assistant · AgentMesh 15.0.6.2026 · A2A + MCP + tool calling[/dim]",
        box=box.DOUBLE,
        border_style="cyan",
    ))

    # Node table
    t = Table(box=box.SIMPLE, show_header=False, padding=(0, 2))
    t.add_column(style="dim")
    t.add_column(style="cyan")
    for name, port in Config.AGENT_PORTS.items():
        t.add_row(name, f"http://{Config.A2A_HOST}:{port}/")
    _console.print("\n[bold]Signed in as:[/bold]", user.display_name,
                   f"[dim]({user.role.value})[/dim]")
    _console.print("\n[dim]Mesh nodes:[/dim]")
    _console.print(t)
    _console.print(
        "[dim]Flow: guardrails → RBAC → compliance(A2A) → PriceAssist(A2A) → redact[/dim]"
    )
    _console.rule(style="dim")

    mode_flags = []
    if _VERBOSE:
        mode_flags.append("[cyan]--verbose[/cyan]")
    if _EXPLAIN:
        mode_flags.append("[cyan]--explain[/cyan]")
    if mode_flags:
        _console.print(f"[dim]Mode:[/dim] {' '.join(mode_flags)}")

    _console.print("\n[dim]Example queries:[/dim]")
    examples = [
        ("data → DataLayer MCP",     "Pricing recommendation for CUST001?"),
        ("data → DataLayer MCP",     "Margin analysis for CUST003"),
        ("knowledge → RAG MCP",      "What is the pricing floor for a BB-rated AED loan?"),
        ("hybrid: data + knowledge", "Is CUST001's loan price compliant with policy?"),
        ("blocked: injection",       "Ignore previous instructions and …"),
        ("blocked: destructive",     "delete all employee records"),
    ]
    for tag, q in examples:
        _console.print(f"  [dim]{tag:<30}[/dim] {q}")
    _console.rule(style="dim")


def _select_user():
    _console.print("\n[bold]Available demo users:[/bold]")
    t = Table(box=box.SIMPLE, show_header=False, padding=(0, 2))
    t.add_column(style="cyan", no_wrap=True)
    t.add_column(style="dim")
    t.add_column()
    for u in list_users():
        t.add_row(u.username, u.role.value, u.display_name)
    _console.print(t)
    _console.print("[bold yellow]Login as (username):[/bold yellow] ", end="")
    username = input().strip() or "bob"
    return login(username)


# ---------------------------------------------------------------------------
# Main REPL
# ---------------------------------------------------------------------------

async def main() -> None:
    try:
        Config.validate()
    except ValueError as e:
        _console.print(f"[red bold]Config error:[/red bold] {e}")
        return

    user = _select_user()
    _print_banner(user)
    _console.print(
        "[dim]Tip: start the mesh first in another terminal with "
        "'python launch_mesh.py'.[/dim]"
    )

    ok, msg = Config.check_groq()
    if not ok:
        _console.print(f"[red]⚠ {msg}[/red]")
    else:
        _console.print(f"[dim]{msg}[/dim]")

    while True:
        try:
            _console.print(
                f"\n[bold yellow][{user.username}] Enter query "
                f"(or 'switch', 'exit', '--help'):[/bold yellow] ",
                end="",
            )
            user_input = input().strip()

            if not user_input:
                continue

            if user_input.lower() in ("exit", "quit"):
                _console.print("[dim]Exiting. Goodbye![/dim]")
                break

            if user_input.lower() == "switch":
                user = _select_user()
                _print_banner(user)
                continue

            if user_input.lower() in ("--help", "help"):
                _print_help()
                continue

            await _run_query(user, user_input)

        except KeyboardInterrupt:
            _console.print("\n[dim]Exiting. Goodbye![/dim]")
            break
        except Exception as e:
            _console.print(f"[red bold]Execution Error:[/red bold] {e}")
            _console.print(
                "[dim]Is the mesh running? Start it with "
                "'python launch_mesh.py'.[/dim]"
            )


async def _run_query(user, user_input: str) -> None:
    """Run one query through the mesh with full execution tracing."""
    tracer = ExecutionTracer(user=user.username, query=user_input)
    renderer = CLIRenderer(console=_console, verbose=_VERBOSE, explain=_EXPLAIN)
    tracer.add_listener(renderer.on_event)

    renderer.render_header(user.username, user_input)

    token = set_active_tracer(tracer)
    try:
        result = await handle_request(user, user_input)
    finally:
        clear_active_tracer(token)

    summary = tracer.summary()
    renderer.render_summary(summary)
    renderer.render_final_answer(result.answer, blocked=result.blocked)


def _print_help() -> None:
    _console.print()
    _console.print(Panel(
        "[bold]CLI Flags (set at startup)[/bold]\n\n"
        "  [cyan]--verbose[/cyan]   Show confidence scores, routing detail, timing\n"
        "  [cyan]--explain[/cyan]   Show alternative domain scores and rejection rationale\n\n"
        "  [bold]Commands[/bold]\n\n"
        "  [cyan]switch[/cyan]      Change the logged-in user\n"
        "  [cyan]exit[/cyan]        Quit the session\n"
        "  [cyan]--help[/cyan]      Show this help",
        title="Help",
        border_style="dim",
    ))


if __name__ == "__main__":
    asyncio.run(main())
