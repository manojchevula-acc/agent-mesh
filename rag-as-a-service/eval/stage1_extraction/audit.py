"""Run the real extractor over the corpus and score it against the manifest.

Uses ``DoclingExtractor`` exactly as ingestion does, with enrichment forced on so
image capture and bbox derivation are exercised — with enrichment off the
extractor never rasterises a picture and every image-related metric would report
a perfect zero for the wrong reason.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

from gernas_rag.config.enrichment import EnrichmentConfig
from gernas_rag.extraction.base import ElementType, ExtractedElement
from gernas_rag.extraction.docling_extractor import _LABEL_MAP, DoclingExtractor

from ..core.models import LayoutManifest, ManifestDocument, StageReport
from ..core.reporting import build_metric, rate, table
from ..core.text import normalize
from ..core.thresholds import STAGE1

STAGE = "stage1_extraction"


@dataclass
class ElementRecord:
    """One extracted element, flattened for reporting."""

    element_type: str
    label: str
    page: int | None
    text_length: int
    has_image: bool = False
    image_bytes: int = 0
    has_bbox: bool = False
    table_confidence: float | None = None
    heading_text: str = ""  # Populated for HEADING elements only.


@dataclass
class DocumentAudit:
    document: str
    file: str
    page_count: int
    ocr_used: bool
    elements: list[ElementRecord] = field(default_factory=list)
    unmapped_labels: Counter = field(default_factory=Counter)
    # (filename_stem, png_bytes) pairs, collected only when crop dumping is on —
    # the bytes are not retained otherwise, to keep a full-corpus audit cheap.
    crops: list[tuple[str, bytes]] = field(default_factory=list, repr=False)
    error: str | None = None

    @property
    def figures(self) -> list[ElementRecord]:
        return [e for e in self.elements if e.element_type == ElementType.FIGURE.value]

    @property
    def tables(self) -> list[ElementRecord]:
        return [e for e in self.elements if e.element_type == ElementType.TABLE.value]

    @property
    def headings(self) -> list[str]:
        return [
            e.heading_text
            for e in self.elements
            if e.element_type == ElementType.HEADING.value and e.heading_text
        ]

    def counts_by_type(self) -> Counter:
        return Counter(e.element_type for e in self.elements)


def build_extractor(enrichment: EnrichmentConfig, capture_images: bool) -> DoclingExtractor:
    """Extractor configured the way ingestion configures it, with images forced on.

    ``images_scale`` and ``table_confidence_threshold`` are taken from the real
    config so the audit measures the deployed settings, not defaults.
    """
    config = enrichment.model_copy(update={"enabled": capture_images})
    return DoclingExtractor(enrichment=config)


async def audit_document(
    extractor: DoclingExtractor, file_path: Path, collect_crops: bool = False
) -> DocumentAudit:
    """Extract one document and record everything stage 1 needs to score."""
    document = file_path.stem  # Matches ChunkMetadata.document_name (see MetadataExtractor).
    try:
        # Same routing decision ingestion makes; recorded so it can be asserted
        # against the manifest's `scanned` flag.
        ocr_used = (
            not extractor._pdf_has_text_layer(file_path)
            if file_path.suffix.lower() == ".pdf"
            else True
        )
        extraction = await extractor.extract(file_path)
    except Exception as exc:  # noqa: BLE001 — one bad document must not stop the audit.
        return DocumentAudit(
            document=document, file=str(file_path), page_count=0, ocr_used=False, error=str(exc)
        )

    audit = DocumentAudit(
        document=document,
        file=str(file_path),
        page_count=extraction.page_count,
        ocr_used=ocr_used,
    )
    for index, element in enumerate(extraction.elements):
        audit.elements.append(_record(element))
        label = str(element.metadata.get("label", "")).lower()
        if label and label not in _LABEL_MAP:
            # Unknown labels fall back to PARAGRAPH inside the extractor, which
            # turns a figure into an empty text element without raising anything.
            audit.unmapped_labels[label] += 1
        if collect_crops and element.image_bytes:
            page = element.page_number if element.page_number is not None else "na"
            audit.crops.append((f"p{page}_{index:03d}_{element.element_type.value}", element.image_bytes))
    return audit


def _record(element: ExtractedElement) -> ElementRecord:
    return ElementRecord(
        element_type=element.element_type.value,
        label=str(element.metadata.get("label", "")),
        page=element.page_number,
        text_length=len(element.text or ""),
        has_image=element.image_bytes is not None,
        image_bytes=len(element.image_bytes) if element.image_bytes else 0,
        has_bbox=element.bbox is not None,
        table_confidence=element.metadata.get("table_confidence"),
        heading_text=element.text or "" if element.element_type == ElementType.HEADING else "",
    )


# ══════════════════════════════════════════════════════════════════════
# Scoring
# ══════════════════════════════════════════════════════════════════════
def _page_counter(pages: list[int | None]) -> Counter:
    return Counter(p for p in pages if p is not None)


def _matched_per_page(expected: Counter, detected: Counter) -> int:
    """Elements matched page by page — pairing by page is the only signal available.

    Counting corpus-wide would let a figure detected on page 2 cover a figure
    missed on page 7, which is exactly the error this stage exists to catch.
    """
    return sum(min(count, detected.get(page, 0)) for page, count in expected.items())


def score(
    audits: list[DocumentAudit],
    manifest: LayoutManifest | None,
    report: StageReport,
) -> None:
    """Populate ``report`` with stage 1 metrics, tables and findings."""
    manifest_docs: dict[str, ManifestDocument] = manifest.by_document() if manifest else {}
    verified = {name: doc for name, doc in manifest_docs.items() if doc.verified}

    # ── Extraction failures ───────────────────────────────────────────
    for audit in audits:
        if audit.error:
            report.add_finding("error", "EXTRACTION_FAILED", audit.document, audit.error)

    ok_audits = [a for a in audits if not a.error]

    # ── Unmapped Docling labels ───────────────────────────────────────
    unmapped_total = 0
    for audit in ok_audits:
        for label, count in audit.unmapped_labels.items():
            unmapped_total += count
            report.add_finding(
                "error",
                "UNMAPPED_LABEL",
                f"{audit.document}:{label}",
                f"Docling label {label!r} has no entry in _LABEL_MAP and silently "
                f"became a PARAGRAPH ({count} element(s)). Figures/tables mapped this "
                f"way lose their image and are never enriched.",
            )
    report.add_metric(build_metric(STAGE1, "unmapped_label_count", float(unmapped_total), unit="count"))

    # ── Image capture and bbox ────────────────────────────────────────
    media = [e for a in ok_audits for e in (a.figures + a.tables)]
    figures = [e for a in ok_audits for e in a.figures]
    captured = [e for e in figures if e.has_image]
    report.add_metric(
        build_metric(
            STAGE1,
            "figure_image_capture_rate",
            rate(len(captured), len(figures)),
            detail=f"{len(captured)}/{len(figures)} figure crops captured",
        )
    )
    report.add_metric(
        build_metric(
            STAGE1,
            "bbox_presence_rate",
            rate(sum(1 for e in captured if e.has_bbox), len(captured)),
            detail="of captured figure crops",
        )
    )
    for audit in ok_audits:
        for element in audit.figures:
            if not element.has_image:
                report.add_finding(
                    "warn",
                    "FIGURE_CROP_MISSING",
                    f"{audit.document} p{element.page}",
                    "Docling detected a picture but returned no image bytes; this "
                    "figure cannot be captioned or hydrated.",
                )

    # ── Table structure confidence ────────────────────────────────────
    confidences = [e.table_confidence for e in media if e.table_confidence is not None]
    if confidences and all(abs(c - 1.0) < 1e-9 for c in confidences):
        report.add_finding(
            "warn",
            "TABLE_CONFIDENCE_UNAVAILABLE",
            "corpus",
            f"All {len(confidences)} tables report confidence exactly 1.0. Docling most "
            "likely does not expose `.confidence` on this version, so the getattr "
            "default in DoclingExtractor applies and no table will ever fall below "
            "table_confidence_threshold — the table VLM path is effectively dead.",
        )

    # ── Manifest-based recall ─────────────────────────────────────────
    figure_expected = figure_found = 0
    table_expected = table_found = 0
    heading_expected = heading_found = 0
    ocr_checked = ocr_correct = 0
    rows: list[list[object]] = []

    for audit in ok_audits:
        expected_doc = verified.get(audit.document)
        detected_figures = _page_counter([e.page for e in audit.figures])
        detected_tables = _page_counter([e.page for e in audit.tables])

        if expected_doc is None:
            rows.append(
                [
                    audit.document,
                    audit.page_count,
                    "OCR" if audit.ocr_used else "text",
                    len(audit.figures),
                    len(audit.tables),
                    len(audit.headings),
                    "unverified" if audit.document in manifest_docs else "not in manifest",
                ]
            )
            report.add_finding(
                "warn",
                "MANIFEST_UNVERIFIED" if audit.document in manifest_docs else "MANIFEST_MISSING",
                audit.document,
                "No verified manifest entry, so detection recall is not measured for "
                "this document. Run with --init-manifest, review the draft, then set "
                "verified: true.",
            )
            continue

        wanted_figures = _page_counter([f.page for f in expected_doc.figures if f.must_detect])
        wanted_tables = _page_counter([t.page for t in expected_doc.tables if t.must_detect])
        matched_figures = _matched_per_page(wanted_figures, detected_figures)
        matched_tables = _matched_per_page(wanted_tables, detected_tables)

        figure_expected += sum(wanted_figures.values())
        figure_found += matched_figures
        table_expected += sum(wanted_tables.values())
        table_found += matched_tables

        for page, count in sorted(wanted_figures.items()):
            missing = count - min(count, detected_figures.get(page, 0))
            if missing:
                report.add_finding(
                    "error",
                    "FIGURE_NOT_DETECTED",
                    f"{audit.document} p{page}",
                    f"{missing} expected figure(s) on this page were not detected. "
                    "They can never be captioned, retrieved or cited.",
                )
        for page, count in sorted(wanted_tables.items()):
            missing = count - min(count, detected_tables.get(page, 0))
            if missing:
                report.add_finding(
                    "error",
                    "TABLE_NOT_DETECTED",
                    f"{audit.document} p{page}",
                    f"{missing} expected table(s) on this page were not detected.",
                )

        detected_headings = {normalize(h) for h in audit.headings}
        wanted_headings = [h for h in expected_doc.headings if h.strip()]
        found_headings = [
            h
            for h in wanted_headings
            if any(normalize(h) in d or d in normalize(h) for d in detected_headings)
        ]
        heading_expected += len(wanted_headings)
        heading_found += len(found_headings)
        for heading in wanted_headings:
            if heading not in found_headings:
                report.add_finding(
                    "warn",
                    "HEADING_NOT_DETECTED",
                    f"{audit.document}",
                    f"Expected heading {heading!r} was not extracted; chunks under it "
                    "will carry the wrong section_heading.",
                )

        ocr_checked += 1
        if audit.ocr_used == expected_doc.scanned:
            ocr_correct += 1
        else:
            report.add_finding(
                "error",
                "OCR_ROUTING_WRONG",
                audit.document,
                f"Text-layer detection chose {'OCR' if audit.ocr_used else 'no OCR'} but "
                f"the manifest marks this document as "
                f"{'scanned' if expected_doc.scanned else 'digital'}.",
            )

        rows.append(
            [
                audit.document,
                audit.page_count,
                "OCR" if audit.ocr_used else "text",
                f"{matched_figures}/{sum(wanted_figures.values())}",
                f"{matched_tables}/{sum(wanted_tables.values())}",
                f"{len(found_headings)}/{len(wanted_headings)}",
                "verified",
            ]
        )

    report.add_metric(
        build_metric(
            STAGE1,
            "figure_detection_recall",
            rate(figure_found, figure_expected),
            detail=f"{figure_found}/{figure_expected} manifest figures detected",
        )
    )
    report.add_metric(
        build_metric(
            STAGE1,
            "table_detection_recall",
            rate(table_found, table_expected),
            detail=f"{table_found}/{table_expected} manifest tables detected",
        )
    )
    report.add_metric(
        build_metric(
            STAGE1,
            "heading_recall",
            rate(heading_found, heading_expected),
            detail=f"{heading_found}/{heading_expected} manifest headings detected",
        )
    )
    report.add_metric(
        build_metric(
            STAGE1,
            "ocr_routing_accuracy",
            rate(ocr_correct, ocr_checked),
            detail=f"{ocr_correct}/{ocr_checked} documents routed as the manifest expects",
        )
    )

    report.tables.append(
        table(
            "Per-document extraction",
            ["Document", "Pages", "Mode", "Figures", "Tables", "Headings", "Manifest"],
            rows,
            note="Figure/Table/Heading columns show matched/expected for verified "
            "manifest entries, and raw detected counts otherwise.",
        )
    )
    report.tables.append(
        table(
            "Element type distribution",
            ["Type", "Count"],
            sorted(
                ((t, c) for t, c in sum((a.counts_by_type() for a in ok_audits), Counter()).items()),
                key=lambda r: -r[1],
            ),
        )
    )

    report.meta.update(
        {
            "documents_audited": len(audits),
            "documents_verified_in_manifest": len(verified),
            "elements_extracted": sum(len(a.elements) for a in ok_audits),
        }
    )


def dump_crops(audits: list[DocumentAudit], out_dir: Path) -> int:
    """Write captured crops to disk for visual review. Returns the file count.

    No numeric metric reliably catches "Docling found the figure but cropped the
    caption text instead of the chart". Twenty PNGs in a folder do, once.
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    written = 0
    for audit in audits:
        for name, data in audit.crops:
            (out_dir / f"{audit.document}__{name}.png").write_bytes(data)
            written += 1
    return written
