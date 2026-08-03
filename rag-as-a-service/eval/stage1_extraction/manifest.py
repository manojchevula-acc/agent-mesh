"""Layout manifest: load, scaffold and validate the stage 1 ground truth.

The manifest is a hand-verified inventory of what each source document actually
contains. Because typing one from scratch is tedious (and the tedium is why it
never gets done), ``scaffold`` produces a draft from a real extraction run that a
human then corrects. Draft entries are written with ``verified: false`` and are
excluded from gated metrics — scoring the extractor against its own unreviewed
output would be a tautology, not a test.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from ..core.io import read_json, write_json
from ..core.models import LayoutManifest, ManifestDocument, ManifestFigure, ManifestTable

if TYPE_CHECKING:  # Import only for typing — audit.py imports this module at runtime.
    from .audit import DocumentAudit


def load_manifest(path: Path) -> LayoutManifest | None:
    """Load and validate the manifest, or ``None`` when it does not exist yet."""
    raw = read_json(path)
    if raw is None:
        return None
    return LayoutManifest.model_validate(raw)


def save_manifest(path: Path, manifest: LayoutManifest) -> Path:
    """Write the manifest, dropping the review-only fields the scorer never reads.

    ``kind``/``description`` (figures) and ``title``/``rows``/``cols`` (tables) exist
    purely for a human's own reference while reviewing — see ``audit.py``'s ``score``,
    which only ever consumes ``page`` and ``must_detect``. Omitting them keeps a fresh
    scaffold small to review; a human can still add any of them back on an entry by
    hand if it helps document a tricky figure, since they stay valid optional fields.
    """
    data = manifest.model_dump(
        mode="json",
        exclude={
            "documents": {
                "__all__": {
                    "figures": {"__all__": {"kind", "description"}},
                    "tables": {"__all__": {"title", "rows", "cols"}},
                }
            }
        },
    )
    return write_json(path, data)


def scaffold_document(audit: "DocumentAudit") -> ManifestDocument:
    """Draft a manifest entry from what the extractor actually produced."""
    return ManifestDocument(
        document=audit.document,
        file=audit.file,
        pages=audit.page_count or None,
        scanned=audit.ocr_used,
        figures=[ManifestFigure(page=element.page or 0) for element in audit.figures],
        tables=[ManifestTable(page=element.page or 0) for element in audit.tables],
        headings=list(audit.headings),
        verified=False,
        notes=(
            "SCAFFOLDED from an extraction run — review every entry against the "
            "source PDF, add anything the extractor missed, remove anything it "
            "invented, then set verified: true."
        ),
    )


def merge_scaffold(
    existing: LayoutManifest | None, drafts: list[ManifestDocument]
) -> LayoutManifest:
    """Add draft entries without ever overwriting a human-verified document."""
    manifest = existing or LayoutManifest()
    by_name = {d.document: d for d in manifest.documents}
    for draft in drafts:
        current = by_name.get(draft.document)
        if current is not None and current.verified:
            continue  # Never clobber reviewed ground truth.
        by_name[draft.document] = draft
    return LayoutManifest(
        version=manifest.version,
        documents=[by_name[name] for name in sorted(by_name)],
    )
