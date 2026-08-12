"""Loaders + validation for the curated ground truth under data/eval/.

Validation is strict and fails loudly. A silently-malformed manifest produces a
green run that proves nothing, which is strictly worse than a red one.
"""

import json
from dataclasses import dataclass, field
from pathlib import Path

DATA_DIR = Path("data/eval")
MANIFEST = DATA_DIR / "layout_manifest.json"
TRANSCRIPTIONS = DATA_DIR / "figure_transcriptions.json"


class GroundTruthError(RuntimeError):
    pass


# ── Stage 1: layout manifest ─────────────────────────────────────────────
@dataclass
class DocLayout:
    document: str
    file: Path
    pages: int
    scanned: bool
    figures: list[dict]
    tables: list[dict]
    headings: list[str]
    verified: bool
    notes: str = ""

    # `must_detect: false` marks a region that genuinely exists on the page but
    # that this pipeline's image filters are RIGHT to drop — signature
    # scribbles, blank regions. Scoring those as misses would punish the
    # filters for working. They are still listed (so the manifest stays a
    # faithful description of the page) and still counted, just separately.
    @property
    def figure_pages(self) -> list[int]:
        """Required figure pages, WITH duplicates — two figures on one page
        must count as two, or recall silently rounds up."""
        return [f["page"] for f in self.figures if f.get("must_detect", True)]

    @property
    def optional_figure_pages(self) -> list[int]:
        return [f["page"] for f in self.figures if not f.get("must_detect", True)]

    @property
    def table_pages(self) -> list[int]:
        return [t["page"] for t in self.tables if t.get("must_detect", True)]


def load_manifest(path: Path = MANIFEST) -> list[DocLayout]:
    if not path.exists():
        raise GroundTruthError(
            f"{path} not found. Stage 1 has no ground truth to score against.\n"
            f"Draft one with:  python -m eval stage1 --init-manifest"
        )
    raw = json.loads(path.read_text(encoding="utf-8"))
    docs = []
    for entry in raw.get("documents", []):
        file_path = Path(entry["file"])
        if not file_path.exists():
            raise GroundTruthError(
                f"{path}: document {entry['document']!r} points at {file_path}, "
                f"which does not exist. Paths must be repo-relative."
            )
        docs.append(
            DocLayout(
                document=entry["document"],
                file=file_path,
                pages=entry["pages"],
                scanned=entry.get("scanned", False),
                figures=entry.get("figures", []),
                tables=entry.get("tables", []),
                headings=entry.get("headings", []),
                verified=entry.get("verified", False),
                notes=entry.get("notes", ""),
            )
        )
    if not docs:
        raise GroundTruthError(f"{path} contains no documents.")
    return docs


# ── Stage 2b: figure transcriptions ──────────────────────────────────────
@dataclass
class Transcription:
    artifact_ref: str  # originating pipeline's ref — provenance only
    asset_id: str | None  # THIS pipeline's id; None until remapped
    document: str
    page: int
    modality: str
    transcription: str
    verified: bool
    notes: str = ""
    unresolved: list[str] = field(default_factory=list)

    @property
    def scoreable(self) -> bool:
        """Only a verified item with an asset this pipeline can actually load
        contributes a score. Everything else reports n/a."""
        return self.verified and bool(self.asset_id)


def load_transcriptions(path: Path = TRANSCRIPTIONS) -> list[Transcription]:
    if not path.exists():
        raise GroundTruthError(
            f"{path} not found. Stage 2b has no ground truth to score against."
        )
    raw = json.loads(path.read_text(encoding="utf-8"))
    items = [
        Transcription(
            artifact_ref=entry.get("artifact_ref", ""),
            asset_id=entry.get("asset_id"),
            document=entry["document"],
            page=entry["page"],
            modality=entry.get("modality", "figure"),
            transcription=entry["transcription"],
            verified=entry.get("verified", False),
            notes=entry.get("notes", ""),
        )
        for entry in raw.get("items", [])
    ]
    if not items:
        raise GroundTruthError(f"{path} contains no items.")
    return items


def save_transcriptions(items: list[Transcription], path: Path = TRANSCRIPTIONS) -> None:
    """Rewrite the file, preserving the _schema block if present."""
    existing = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    payload = {
        "version": existing.get("version", 1),
        **({"_schema": existing["_schema"]} if "_schema" in existing else {}),
        "items": [
            {
                "artifact_ref": t.artifact_ref,
                "asset_id": t.asset_id,
                "document": t.document,
                "page": t.page,
                "modality": t.modality,
                "transcription": t.transcription,
                "verified": t.verified,
                "notes": t.notes,
            }
            for t in items
        ],
    }
    path.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
