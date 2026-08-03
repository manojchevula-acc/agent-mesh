"""Report assembly: metrics + findings -> Markdown, JSON and a process exit code.

Every stage produces the same shape, so a reviewer reads one format regardless of
which stage failed, and CI wires each stage in identically.
"""

from __future__ import annotations

from typing import Iterable

from .models import Finding, MetricResult, ReportTable, StageReport
from .thresholds import Gate

_STATUS = {True: "PASS", False: "FAIL", None: "n/a"}
_SEVERITY_ORDER = {"error": 0, "warn": 1, "info": 2}


def build_metric(
    stage_gates: dict[str, Gate],
    name: str,
    value: float | None,
    unit: str = "",
    detail: str = "",
) -> MetricResult:
    """Create a :class:`MetricResult`, applying the stage's configured gate."""
    gate = stage_gates.get(name)
    if gate is None:
        return MetricResult.build(name, value, unit=unit, detail=detail, gating=False)
    return MetricResult.build(
        name,
        value,
        threshold=gate.threshold,
        direction=gate.direction,
        unit=unit or gate.unit,
        detail=detail,
        gating=gate.gating,
    )


def _fmt(value: float | None, unit: str = "") -> str:
    if value is None:
        return "-"
    if unit == "count" or (float(value).is_integer() and abs(value) >= 1000):
        return f"{int(value):,}"
    if float(value).is_integer() and unit != "ratio":
        return str(int(value))
    return f"{value:.4f}"


def _fmt_threshold(metric: MetricResult) -> str:
    if metric.threshold is None:
        return "-"
    op = ">=" if metric.direction == "min" else "<="
    return f"{op} {_fmt(metric.threshold, metric.unit)}"


def render_markdown(report: StageReport) -> str:
    lines: list[str] = [f"# {report.title}", ""]
    lines.append(f"- Stage: `{report.stage}`")
    lines.append(f"- Generated: {report.generated_at}")
    for key, value in report.meta.items():
        lines.append(f"- {key}: {value}")
    lines.append("")

    if report.summary:
        lines += [report.summary, ""]

    # ── Verdict ───────────────────────────────────────────────────────
    failed = report.failed_metrics
    errors = report.errors
    warnings = report.warnings
    verdict = "PASS" if not failed and not errors else "FAIL"
    lines.append(f"**Verdict: {verdict}**")
    lines.append("")
    lines.append(
        f"{len(report.metrics)} metric(s) | {len(failed)} failing gate(s) | "
        f"{len(errors)} error(s) | {len(warnings)} warning(s)"
    )
    lines.append("")

    # ── Metrics ───────────────────────────────────────────────────────
    if report.metrics:
        lines += ["## Metrics", "", "| Metric | Value | Gate | Status | Detail |", "|---|---|---|---|---|"]
        for metric in report.metrics:
            status = _STATUS[metric.passed]
            if not metric.gating and metric.passed is not None:
                status += " (not gating)"
            detail = metric.detail.replace("|", "\\|")
            lines.append(
                f"| `{metric.name}` | {_fmt(metric.value, metric.unit)} | "
                f"{_fmt_threshold(metric)} | {status} | {detail} |"
            )
        lines.append("")

    # ── Supporting tables ─────────────────────────────────────────────
    for table in report.tables:
        lines += [f"## {table.title}", ""]
        if table.note:
            lines += [table.note, ""]
        if table.rows:
            lines.append("| " + " | ".join(table.columns) + " |")
            lines.append("|" + "|".join(["---"] * len(table.columns)) + "|")
            for row in table.rows:
                lines.append("| " + " | ".join(str(c).replace("|", "\\|") for c in row) + " |")
        else:
            lines.append("_(no rows)_")
        lines.append("")

    # ── Findings ──────────────────────────────────────────────────────
    if report.findings:
        ordered = sorted(report.findings, key=lambda f: (_SEVERITY_ORDER[f.severity], f.code))
        lines += ["## Findings", "", "| Severity | Code | Subject | Message |", "|---|---|---|---|"]
        for finding in ordered:
            lines.append(
                f"| {finding.severity.upper()} | `{finding.code}` | "
                f"`{finding.subject}` | {finding.message.replace('|', chr(92) + '|')} |"
            )
        lines.append("")

    return "\n".join(lines)


def render_console_summary(report: StageReport) -> str:
    """Compact terminal summary — the metric table plus the failure reasons."""
    lines = [f"{report.title}"]
    width = max((len(m.name) for m in report.metrics), default=10)
    for metric in report.metrics:
        status = _STATUS[metric.passed]
        suffix = "" if metric.gating else " (not gating)"
        lines.append(
            f"  {metric.name.ljust(width)}  {_fmt(metric.value, metric.unit):>10}"
            f"  {_fmt_threshold(metric):>10}  {status}{suffix}"
        )
    errors = report.errors
    if errors:
        lines.append(f"  -- {len(errors)} error finding(s):")
        for finding in errors[:10]:
            lines.append(f"     [{finding.code}] {finding.subject} {finding.message}")
        if len(errors) > 10:
            lines.append(f"     ... and {len(errors) - 10} more (see the report)")
    return "\n".join(lines)


def exit_code(report: StageReport, fail_on: str = "gate") -> int:
    """0 when the stage passed at the requested strictness.

    ``fail_on``:
      ``gate``  gated metric misses or error findings fail the run (default)
      ``error`` only error findings fail the run
      ``warn``  warnings fail too — useful for a nightly strict job
      ``never`` always exit 0; for exploratory local runs
    """
    if fail_on == "never":
        return 0
    if report.errors:
        return 1
    if fail_on in ("gate", "warn") and report.failed_metrics:
        return 1
    if fail_on == "warn" and report.warnings:
        return 1
    return 0


def rate(numerator: int, denominator: int) -> float | None:
    """Guarded ratio: ``None`` when there is nothing to measure."""
    if denominator <= 0:
        return None
    return numerator / denominator


def summarize_findings(findings: Iterable[Finding]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for finding in findings:
        counts[finding.code] = counts.get(finding.code, 0) + 1
    return counts


def table(title: str, columns: list[str], rows: Iterable[Iterable[object]], note: str = "") -> ReportTable:
    return ReportTable(
        title=title,
        columns=columns,
        rows=[[str(c) for c in row] for row in rows],
        note=note,
    )
