"""Canonical on-disk layout for evaluation inputs and outputs.

Ground-truth files are *inputs a human curates* and live directly under
``data/eval/``. Everything a stage produces is derived and lands under
``data/eval/runs/`` (machine-readable) or ``data/eval/reports/`` (human-readable),
so the curated files can never be clobbered by a run and ``runs/`` can be wiped
without losing anything that was hand-written.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .. import REPO_ROOT


@dataclass(frozen=True)
class EvalPaths:
    """Resolved locations for every artifact the suite reads or writes."""

    root: Path
    docs_dir: Path

    # ── Human-curated ground truth ────────────────────────────────────
    @property
    def layout_manifest(self) -> Path:
        """Stage 1 ground truth: expected figures/tables/headings per document."""
        return self.root / "layout_manifest.json"

    @property
    def figure_transcriptions(self) -> Path:
        """Stage 2 ground truth: human transcription per figure artifact."""
        return self.root / "figure_transcriptions.json"

    @property
    def qrels(self) -> Path:
        """Stage 3 ground truth: graded relevance judgments keyed by chunk_id."""
        return self.root / "qrels.json"

    @property
    def gold_qa(self) -> Path:
        """Stage 4 ground truth: questions and expected answers."""
        return self.root / "gold_qa.json"

    # ── Derived ───────────────────────────────────────────────────────
    @property
    def runs_dir(self) -> Path:
        return self.root / "runs"

    @property
    def reports_dir(self) -> Path:
        return self.root / "reports"

    def run_file(self, name: str) -> Path:
        return self.runs_dir / f"{name}.json"

    def report_file(self, name: str) -> Path:
        return self.reports_dir / f"{name}.md"

    @property
    def crops_dir(self) -> Path:
        """Where ``stage1 --dump-crops`` writes captured figure images for review."""
        return self.root / "crops"

    def ensure(self) -> "EvalPaths":
        for directory in (self.root, self.runs_dir, self.reports_dir):
            directory.mkdir(parents=True, exist_ok=True)
        return self

    @classmethod
    def default(cls, root: Path | str | None = None, docs_dir: Path | str | None = None) -> "EvalPaths":
        return cls(
            root=Path(root) if root else REPO_ROOT / "data" / "eval",
            docs_dir=Path(docs_dir) if docs_dir else REPO_ROOT / "docs",
        )
