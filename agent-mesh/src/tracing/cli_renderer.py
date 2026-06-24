"""Rich terminal renderer for AgentMesh execution transparency.

Subscribes to ExecutionTracer events and renders a live execution console.
--verbose : confidence scores, routing detail, timing
--explain : adds alternative domain scores and rejection rationale
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from rich.console import Console
from rich.markdown import Markdown
from rich.panel import Panel
from rich.rule import Rule
from rich.table import Table
from rich.text import Text
from rich.tree import Tree
from rich import box

from src.tracing.execution_trace import ExecutionEvent, ExecutionSummary

# ASCII-safe symbols that render correctly on Windows cp1252 / legacy consoles
_SYM_OK      = "[green]+[/green]"
_SYM_BLOCKED = "[red bold]![/bold red]"
_SYM_ARROW   = "[dim]>[/dim]"


_STAGE_LABELS: Dict[str, str] = {
    "input_processing":      "INPUT PROCESSING",
    "guardrail":             "GUARDRAIL VALIDATION",
    "rbac":                  "RBAC VALIDATION",
    "compliance":            "COMPLIANCE VALIDATION",
    "domain_classification": "DOMAIN CLASSIFICATION",
    "routing":               "ROUTING DECISION",
    "agent_handoff":         "AGENT HANDOFF",
    "data_retrieval":        "DATA RETRIEVAL",
    "response_generation":   "RESPONSE GENERATION",
    "output_redaction":      "OUTPUT REDACTION",
}


class CLIRenderer:
    """Renders AgentMesh execution events to the terminal using Rich.

    Register ``on_event`` as a listener on an ExecutionTracer before calling
    handle_request. The renderer is stateful per-request — create a fresh
    instance for each query.
    """

    def __init__(
        self,
        console: Optional[Console] = None,
        verbose: bool = False,
        explain: bool = False,
    ) -> None:
        # legacy_windows=False forces ANSI/VT100 path instead of Win32 console API,
        # which avoids cp1252 encoding errors on Windows for Unicode characters.
        self._con = console or Console(highlight=False, legacy_windows=False)
        self._verbose = verbose
        self._explain = explain
        self._step = 0
        self._active_status: Any = None
        self._saw_started: set = set()

    # -- Public surface -------------------------------------------------------

    def render_header(self, user: str, query: str) -> None:
        self._con.print()
        self._con.print(Panel(
            "[bold cyan]AGENT MESH EXECUTION[/bold cyan]",
            box=box.DOUBLE,
            expand=False,
            border_style="cyan",
        ))
        self._con.print(f"\n[bold yellow]User:[/bold yellow] {query}\n")

    def on_event(self, event: ExecutionEvent) -> None:
        if event.status == "started":
            self._on_started(event)
        elif event.status == "completed":
            self._on_completed(event)
        elif event.status in ("blocked", "failed"):
            self._on_blocked(event)

    def render_summary(self, summary: ExecutionSummary) -> None:
        self._stop_spinner()
        self._con.print()
        self._con.rule("[bold cyan]EXECUTION SUMMARY[/bold cyan]", style="cyan")
        self._con.print()

        t = Table(box=box.SIMPLE, show_header=False, padding=(0, 2))
        t.add_column(style="dim", no_wrap=True)
        t.add_column(style="bold")

        t.add_row("Request ID", summary.request_id)
        t.add_row("User", summary.user)
        t.add_row("Domain", summary.domain or "—")
        t.add_row("Route", summary.route or "—")
        if summary.execution_path:
            t.add_row("Execution Path", " -> ".join(summary.execution_path))
        t.add_row("Agents Invoked", str(summary.agents_invoked))
        t.add_row("Tools Used", str(summary.tools_used))
        t.add_row("Total Duration", f"{summary.total_duration_ms / 1000:.1f} sec")
        if summary.confidence is not None:
            t.add_row("Confidence", f"{int(summary.confidence * 100)}%")
        if summary.blocked:
            t.add_row("Status", f"[red bold]BLOCKED[/] (at {summary.block_stage})")
        else:
            t.add_row("Status", "[green bold]SUCCESS[/]")

        self._con.print(t)
        self._con.rule(style="cyan")

    def render_final_answer(self, answer: str, blocked: bool = False) -> None:
        self._con.print()
        self._con.rule("[bold cyan]FINAL RESPONSE[/bold cyan]", style="cyan")
        self._con.print()
        if blocked:
            self._con.print(f"[red bold]BLOCKED:[/red bold] {answer}")
        else:
            # Render as Markdown so tables, lists, and bold headings display correctly.
            self._con.print(Markdown(answer))
        self._con.print()
        self._con.rule(style="dim")

    # -- Event handlers -------------------------------------------------------

    def _on_started(self, event: ExecutionEvent) -> None:
        self._stop_spinner()
        self._saw_started.add(event.stage)
        self._step += 1
        label = _STAGE_LABELS.get(event.stage, event.stage.upper().replace("_", " "))
        self._con.print()
        self._con.rule(
            f"[bold]STEP {self._step}: {label}[/bold]",
            style="cyan",
        )
        self._con.print()
        if event.message:
            self._active_status = self._con.status(
                f"[cyan]{event.message}[/cyan]", spinner="dots"
            )
            self._active_status.start()

    def _on_completed(self, event: ExecutionEvent) -> None:
        self._stop_spinner()
        if event.stage not in self._saw_started:
            # Step completed without a prior "started" (remote step) — print header
            self._step += 1
            label = _STAGE_LABELS.get(event.stage, event.stage.upper().replace("_", " "))
            self._con.print()
            self._con.rule(
                f"[bold]STEP {self._step}: {label}[/bold]",
                style="cyan",
            )
            self._con.print()
        self._render_completed(event)

    def _on_blocked(self, event: ExecutionEvent) -> None:
        self._stop_spinner()
        self._con.print()
        self._con.print(f"[red bold]! BLOCKED[/red bold]")
        if event.message:
            self._con.print(f"  [red]{event.message}[/red]")
        if event.result:
            self._con.print()
            self._con.print(f"  Result:  [red bold]{event.result}[/red bold]")
        if event.rationale:
            self._con.print()
            for r in event.rationale:
                self._con.print(f"  [red dim]->[/red dim] {r}")

    # -- Rendering helpers ----------------------------------------------------

    def _render_completed(self, event: ExecutionEvent) -> None:
        for chk in event.checks:
            self._con.print(f"  [green]+[/green] {chk}")

        if event.result:
            self._con.print()
            self._con.print("  [dim]Result:[/dim]")
            self._con.print(f"  [bold green]-> {event.result}[/bold green]")

        show_detail = self._verbose or self._explain

        if event.confidence is not None and show_detail:
            self._con.print()
            self._con.print("  [dim]Confidence Score:[/dim]")
            self._con.print(f"  [bold]-> {int(event.confidence * 100)}%[/bold]")

        if event.rationale and show_detail:
            self._con.print()
            self._con.print("  [dim]Reasoning:[/dim]")
            for r in event.rationale:
                self._con.print(f"  [dim]->[/dim] {r}")

        # --explain: alternative domain confidence breakdown
        alt_scores: Dict[str, float] = event.metadata.get("alt_scores", {})
        if alt_scores and self._explain:
            self._con.print()
            self._con.print("  [dim]Domain Confidence Breakdown:[/dim]")
            for domain, score in sorted(alt_scores.items(), key=lambda x: -x[1]):
                bar = self._conf_bar(score)
                self._con.print(
                    f"  [dim]->[/dim] {domain:<28} {bar}  {int(score * 100)}%"
                )
            selected = max(alt_scores, key=lambda k: alt_scores[k])
            self._con.print()
            self._con.print(f"  [dim]Selected:[/dim]")
            self._con.print(f"  [bold green]-> {selected}[/bold green]")

        # Agent handoff tree
        handoff_path: List[str] = event.metadata.get("handoff_path", [])
        if handoff_path and event.stage == "agent_handoff":
            self._con.print()
            self._render_handoff_tree(handoff_path)

        # Data retrieval stats
        if event.stage == "data_retrieval":
            records = event.metadata.get("records_retrieved")
            latency = event.metadata.get("latency_ms")
            if records is not None:
                self._con.print()
                self._con.print("  [dim]Records Retrieved:[/dim]")
                self._con.print(f"  [bold]-> {records}[/bold]")
            if latency is not None and (show_detail or event.stage == "data_retrieval"):
                self._con.print("  [dim]Latency:[/dim]")
                self._con.print(f"  [bold]-> {latency} ms[/bold]")

        if self._verbose and event.duration_ms is not None:
            self._con.print(f"  [dim]Duration: {event.duration_ms} ms[/dim]")

    def _render_handoff_tree(self, path: List[str]) -> None:
        if not path:
            return
        root = Tree(f"[bold cyan]{path[0]}[/bold cyan]")
        node = root
        for step in path[1:]:
            node = node.add(f"[cyan]{step}[/cyan]")
        self._con.print("  ", root)
        self._con.print()
        self._con.print(f"  [green]+[/green] Handoff successful")

    @staticmethod
    def _conf_bar(score: float, width: int = 10) -> str:
        filled = round(score * width)
        return (
            "[green]" + "#" * filled + "[/green]"
            + "[dim]" + "." * (width - filled) + "[/dim]"
        )

    def _stop_spinner(self) -> None:
        if self._active_status is not None:
            try:
                self._active_status.stop()
            except Exception:
                pass
            self._active_status = None
