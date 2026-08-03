"""Stage 2b CLI — VLM caption fidelity against human transcriptions.

    # One-time: scaffold the ground-truth file and export the images to look at
    python -m eval.stage2_enrichment.run_captions --init --export-images

    # Then, after transcribing each image by hand and setting verified: true
    python -m eval.stage2_enrichment.run_captions

Deterministic and offline: no judge, no API key, no run-to-run variance.
"""

from __future__ import annotations

import argparse

from gernas_rag.config.settings import get_settings
from gernas_rag.storage.artifact_store import get_artifact_store

from ..core.corpus import ChunkIndex
from ..core.io import read_json, write_json
from ..core.models import StageReport, TranscriptionSet
from ..core.runner import base_parser, emit, paths_from_args, run_stage
from . import captions

STAGE = captions.STAGE


def build_parser() -> argparse.ArgumentParser:
    parser = base_parser(__doc__ or "")
    parser.add_argument(
        "--init",
        action="store_true",
        help="Scaffold/refresh figure_transcriptions.json. Verified entries are never "
        "overwritten.",
    )
    parser.add_argument(
        "--export-images",
        action="store_true",
        help="Write each media artifact next to the ground-truth file so a reviewer can "
        "transcribe it without digging through the artifact store.",
    )
    return parser


async def main(args: argparse.Namespace) -> int:
    paths = paths_from_args(args)
    settings = get_settings()

    print(f"  loading collection: {settings.vectordb.collection_name}")
    index = await ChunkIndex.load(settings)

    raw = read_json(paths.figure_transcriptions)
    transcriptions = TranscriptionSet.model_validate(raw) if raw else TranscriptionSet()

    if args.init:
        transcriptions = captions.scaffold(index, transcriptions)
        write_json(paths.figure_transcriptions, transcriptions.model_dump(mode="json"))
        pending = sum(1 for i in transcriptions.items if not i.verified)
        print(f"  scaffolded -> {paths.figure_transcriptions}")
        print(f"  {pending} entr(ies) awaiting a human transcription")

    if args.export_images:
        store = get_artifact_store(settings.artifact_store)
        out_dir = paths.root / "figures"
        out_dir.mkdir(parents=True, exist_ok=True)
        exported = 0
        for item in transcriptions.items:
            try:
                data, _ = await store.get_bytes(item.artifact_ref)
            except Exception as exc:  # noqa: BLE001 — an unresolvable ref is stage 2a's finding.
                print(f"    skip {item.artifact_ref}: {exc}")
                continue
            page = f"p{item.page}" if item.page is not None else "pna"
            digest = item.artifact_ref.removeprefix("sha256:").split(".")[0][:12]
            (out_dir / f"{item.document}__{page}__{digest}.png").write_bytes(data)
            exported += 1
        print(f"  exported {exported} image(s) to {out_dir}")

    report = StageReport(
        stage=STAGE,
        title="Stage 2b — VLM caption fidelity",
        summary=(
            "Compares each indexed caption against a human transcription of the same "
            "image. Numeric recall and hallucination rate are the metrics that matter: "
            "a transposed digit here produces answers that are grounded, cited and false."
        ),
        meta={
            "ground_truth": str(paths.figure_transcriptions),
            "vlm_model": settings.enrichment.vlm_model_name,
            "enrichment_enabled": settings.enrichment.enabled,
        },
    )
    captions.score(index, transcriptions, report)
    return emit(report, args, paths)


if __name__ == "__main__":
    run_stage(main, build_parser().parse_args())
