"""Stage 1 CLI — extraction / layout fidelity.

    # First run: draft a manifest from a real extraction, then review it by hand
    python -m eval.stage1_extraction.run --init-manifest
    python -m eval.stage1_extraction.run --dump-crops      # eyeball the captured crops

    # Normal run, once the manifest has verified entries
    python -m eval.stage1_extraction.run
    python -m eval.stage1_extraction.run --doc FAB_Credit_Pricing_Policy_v2_4

Exits non-zero when a gated metric misses (see eval/core/thresholds.py::STAGE1).
"""

from __future__ import annotations

import argparse
from pathlib import Path

from gernas_rag.config.settings import get_settings

from ..core.models import StageReport
from ..core.paths import EvalPaths
from ..core.runner import base_parser, emit, paths_from_args, run_stage
from . import audit as audit_mod
from .manifest import load_manifest, merge_scaffold, save_manifest, scaffold_document

STAGE = audit_mod.STAGE

_SUPPORTED = {".pdf", ".docx", ".doc", ".pptx", ".html", ".md"}


def build_parser() -> argparse.ArgumentParser:
    parser = base_parser(__doc__ or "")
    parser.add_argument(
        "--doc",
        action="append",
        dest="docs",
        default=None,
        help="Audit only this document (stem or filename). Repeatable.",
    )
    parser.add_argument(
        "--init-manifest",
        action="store_true",
        help="Write a draft manifest entry for every audited document. Never "
        "overwrites an entry already marked verified: true.",
    )
    parser.add_argument(
        "--dump-crops",
        action="store_true",
        help="Write every captured figure/table crop to data/eval/crops for review.",
    )
    parser.add_argument(
        "--no-images",
        action="store_true",
        help="Skip image rasterisation (much faster, but image metrics are not measured).",
    )
    return parser


def _select_documents(paths: EvalPaths, wanted: list[str] | None) -> list[Path]:
    if not paths.docs_dir.is_dir():
        raise SystemExit(f"Corpus directory not found: {paths.docs_dir}")
    files = sorted(p for p in paths.docs_dir.iterdir() if p.suffix.lower() in _SUPPORTED)
    if not wanted:
        return files
    keys = {w.lower().removesuffix(".pdf") for w in wanted}
    selected = [p for p in files if p.stem.lower() in keys or p.name.lower() in keys]
    missing = keys - {p.stem.lower() for p in selected} - {p.name.lower() for p in selected}
    if missing:
        raise SystemExit(f"No such document(s) in {paths.docs_dir}: {sorted(missing)}")
    return selected


async def main(args: argparse.Namespace) -> int:
    paths = paths_from_args(args)
    settings = get_settings()
    files = _select_documents(paths, args.docs)

    capture_images = not args.no_images
    extractor = audit_mod.build_extractor(settings.enrichment, capture_images)

    report = StageReport(
        stage=STAGE,
        title="Stage 1 — Extraction & layout fidelity",
        summary=(
            "Measures whether the extractor finds every figure, table and heading the "
            "source documents actually contain. A miss here is invisible to every "
            "later stage: the content simply never enters the index."
        ),
        meta={
            "corpus": str(paths.docs_dir),
            "extractor": "docling",
            "image_capture": capture_images,
            "images_scale": settings.enrichment.images_scale,
            "table_confidence_threshold": settings.enrichment.table_confidence_threshold,
        },
    )

    audits = []
    for path in files:
        print(f"  extracting: {path.name}")
        result = await audit_mod.audit_document(extractor, path, collect_crops=args.dump_crops)
        if result.error:
            print(f"    FAILED: {result.error}")
        else:
            print(
                f"    {len(result.elements)} elements | {len(result.figures)} figures | "
                f"{len(result.tables)} tables | {'OCR' if result.ocr_used else 'text layer'}"
            )
        audits.append(result)

    if args.dump_crops:
        written = audit_mod.dump_crops(audits, paths.crops_dir)
        report.meta["crops_written"] = f"{written} -> {paths.crops_dir}"
        print(f"\n  wrote {written} crop(s) to {paths.crops_dir}")

    manifest = load_manifest(paths.layout_manifest)
    if args.init_manifest:
        drafts = [scaffold_document(a) for a in audits if not a.error]
        manifest = merge_scaffold(manifest, drafts)
        save_manifest(paths.layout_manifest, manifest)
        print(f"\n  manifest drafted -> {paths.layout_manifest}")
        print("  Review each entry against the source PDF, then set verified: true.")

    if manifest is None:
        report.add_finding(
            "warn",
            "MANIFEST_MISSING",
            str(paths.layout_manifest),
            "No layout manifest found, so detection recall cannot be measured. "
            "Run once with --init-manifest and review the draft.",
        )

    audit_mod.score(audits, manifest, report)
    return emit(report, args, paths, extra_json={"audits": [_serialise(a) for a in audits]})


def _serialise(audit: audit_mod.DocumentAudit) -> dict:
    return {
        "document": audit.document,
        "file": audit.file,
        "page_count": audit.page_count,
        "ocr_used": audit.ocr_used,
        "error": audit.error,
        "counts_by_type": dict(audit.counts_by_type()),
        "unmapped_labels": dict(audit.unmapped_labels),
        "figures": [
            {"page": e.page, "has_image": e.has_image, "image_bytes": e.image_bytes, "has_bbox": e.has_bbox}
            for e in audit.figures
        ],
        "tables": [
            {"page": e.page, "confidence": e.table_confidence, "has_image": e.has_image}
            for e in audit.tables
        ],
        "headings": audit.headings,
    }


if __name__ == "__main__":
    run_stage(main, build_parser().parse_args())
