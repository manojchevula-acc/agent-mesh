"""Canonical on-disk layout for evaluation inputs and outputs.

Ground-truth files are *inputs a human curates* and live directly under
``data/eval/``. Everything a stage produces is derived and lands under
``data/eval/runs/`` (machine-readable) or ``data/eval/reports/`` (human-readable),
so the curated files can never be clobbered by a run and ``runs/`` can be wiped
without losing anything that was hand-written.

The split is the whole point: if a file is in the root a human wrote it and it is
the answer key; if it is under ``runs/`` the suite computed it and regenerating
is free. Stage 3's relevance judgments moved to ``runs/`` when they stopped being
hand-reviewed — filing a derived artifact next to the answer keys is what made it
look like a second, competing source of truth.
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
    def gold_qa(self) -> Path:
        """Stage 3 and 4 ground truth: questions, expected answers, source documents.

        The only curated input either retrieval or generation is scored against.
        Stage 3's relevance judgments are *derived* from this file — see
        :attr:`qrels`.
        """
        return self.root / "gold_qa.json"

    # ── Derived ───────────────────────────────────────────────────────
    @property
    def qrels(self) -> Path:
        """Stage 3 relevance judgments, derived from :attr:`gold_qa` on every run.

        Lives under ``runs/`` rather than beside the curated files because it is
        an output, not an input: nothing here is hand-written, and deleting it
        costs only the time to regenerate. Kept on disk rather than held in
        memory so ``--score-only`` can work without the index, and so a diff
        distinguishes "the judgments moved" (chunk ids changed after a re-ingest)
        from "retrieval got worse" when a metric drops.
        """
        return self.runs_dir / "stage3_qrels.json"

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
