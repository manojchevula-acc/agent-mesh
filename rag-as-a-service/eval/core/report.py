"""Shared result model plus the JSON + Markdown writers every stage uses.

One writer for all stages so that ``python -m eval all`` produces a uniform
trail: machine-readable under data/eval/runs/ for diffing between runs,
human-readable under data/eval/reports/ for review.
"""

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .thresholds import ALL as ALL_BARS

RUNS_DIR = Path("data/eval/runs")
REPORTS_DIR = Path("data/eval/reports")

# A metric that could not be computed at all — no verified ground truth, or a
# dependency was down. Distinct from 0.0, which is a real and very bad score.
NA = None


@dataclass
class Metric:
    name: str
    value: float | None  # None => n/a, see NA above
    detail: str = ""

    def verdict(self, stage: str) -> str:
        bar = ALL_BARS.get(stage, {}).get(self.name)
        if self.value is None:
            return "n/a"
        if bar is None:
            return "—"
        if bar.passed(self.value):
            return "pass"
        # A watch-metric below its bar is a signal, not a gate — it must not
        # look identical to a failure that will break CI.
        return "FAIL" if bar.required else "warn"


@dataclass
class StageResult:
    stage: str
    title: str
    metrics: list[Metric] = field(default_factory=list)
    rows: list[dict[str, Any]] = field(default_factory=list)  # per-item detail
    notes: list[str] = field(default_factory=list)
    context: dict[str, Any] = field(default_factory=dict)  # models, config, k

    def add(self, name: str, value: float | None, detail: str = "") -> None:
        self.metrics.append(Metric(name, value, detail))

    @property
    def failed(self) -> list[Metric]:
        """Required metrics that scored below their bar.

        An n/a never fails the run — you cannot fail a test you did not run.
        The Markdown report shows n/a counts prominently instead, so missing
        ground truth stays visible rather than silently passing.
        """
        out = []
        for m in self.metrics:
            bar = ALL_BARS.get(self.stage, {}).get(m.name)
            if bar and bar.required and m.value is not None and not bar.passed(m.value):
                out.append(m)
        return out

    @property
    def na(self) -> list[Metric]:
        return [m for m in self.metrics if m.value is None]


def _fmt(value: float | None) -> str:
    return "n/a" if value is None else f"{value:.1%}" if value <= 1.0 else f"{value:.3f}"


def write(result: StageResult) -> tuple[Path, Path]:
    RUNS_DIR.mkdir(parents=True, exist_ok=True)
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    json_path = RUNS_DIR / f"{result.stage}.json"
    json_path.write_text(
        json.dumps(
            {
                "stage": result.stage,
                "title": result.title,
                "generated_at": stamp,
                "context": result.context,
                "metrics": [
                    {
                        "name": m.name,
                        "value": m.value,
                        "detail": m.detail,
                        "verdict": m.verdict(result.stage),
                    }
                    for m in result.metrics
                ],
                "rows": result.rows,
                "notes": result.notes,
            },
            indent=2,
            ensure_ascii=False,
            default=str,
        ),
        encoding="utf-8",
    )

    out = [f"# {result.title}", "", f"_{stamp}_", ""]
    if result.context:
        out += ["| | |", "|---|---|"]
        out += [f"| {k} | `{v}` |" for k, v in result.context.items()]
        out.append("")

    out += ["## Metrics", "", "| Metric | Score | Bar | Verdict | Detail |", "|---|---|---|---|---|"]
    for m in result.metrics:
        bar = ALL_BARS.get(result.stage, {}).get(m.name)
        if bar is None:
            bar_s = "—"
        else:
            # Direction is explicit: a reader must not have to guess whether
            # 30% is above a floor or below a ceiling.
            sign = "≥" if bar.higher_is_better else "≤"
            bar_s = f"{sign} {bar.value:.0%}{'' if bar.required else ' (watch)'}"
        verdict = m.verdict(result.stage)
        out.append(
            f"| {m.name} | {_fmt(m.value)} | {bar_s} | "
            f"{'**FAIL**' if verdict == 'FAIL' else verdict} | {m.detail} |"
        )

    if result.na:
        out += [
            "",
            f"> {len(result.na)} metric(s) reported **n/a** — no verified ground truth, "
            "or a dependency was unavailable. n/a never fails a run; it means the "
            "check did not happen.",
        ]

    if result.rows:
        out += ["", "## Detail", ""]
        cols = list(result.rows[0].keys())
        out += ["| " + " | ".join(cols) + " |", "|" + "---|" * len(cols)]
        for row in result.rows:
            cells = [str(row.get(c, "")).replace("\n", " ").replace("|", "\\|")[:160] for c in cols]
            out.append("| " + " | ".join(cells) + " |")

    if result.notes:
        out += ["", "## Notes", ""] + [f"- {n}" for n in result.notes]

    md_path = REPORTS_DIR / f"{result.stage}.md"
    md_path.write_text("\n".join(out) + "\n", encoding="utf-8")
    return json_path, md_path


def print_summary(result: StageResult) -> None:
    print(f"\n{result.title}")
    print("-" * 78)
    for m in result.metrics:
        verdict = m.verdict(result.stage)
        mark = {"pass": "ok  ", "FAIL": "FAIL", "warn": "warn", "n/a": "n/a ", "—": "    "}[verdict]
        print(f"  {mark} {m.name:<26} {_fmt(m.value):>8}   {m.detail}")
    if result.failed:
        print(f"\n  {len(result.failed)} required metric(s) below bar.")
