"""Stage 2a CLI — index and artifact integrity.

    python -m eval.stage2_enrichment.run_integrity

Reads the live collection and the artifact store; no models, no LLM calls, no
network. Cheap enough to run on every ingest, and it is the check that catches a
figure that was captured, stored and then silently dropped from the index.
"""

from __future__ import annotations

import argparse

from gernas_rag.config.settings import get_settings
from gernas_rag.storage.artifact_store import get_artifact_store

from ..core.corpus import ChunkIndex
from ..core.models import StageReport
from ..core.runner import base_parser, emit, paths_from_args, run_stage
from . import integrity

STAGE = integrity.STAGE


def build_parser() -> argparse.ArgumentParser:
    return base_parser(__doc__ or "")


async def main(args: argparse.Namespace) -> int:
    paths = paths_from_args(args)
    settings = get_settings()

    print(f"  loading collection: {settings.vectordb.collection_name}")
    index = await ChunkIndex.load(settings)
    print(f"  {len(index.chunks)} chunk(s), {len(index.media)} media chunk(s)")

    store = get_artifact_store(settings.artifact_store)

    report = StageReport(
        stage=STAGE,
        title="Stage 2a — Index & artifact integrity",
        summary=(
            "Reconciles what was extracted against what is actually stored and "
            "linked: every captured image must resolve to exactly one correctly "
            "keyed chunk, and every chunk must be reachable by the identity the "
            "pipeline derives for it."
        ),
        meta={"artifact_store": settings.artifact_store.local_path},
    )

    integrity.summarise(index, report)
    await integrity.check_artifacts(index, store, report)
    integrity.check_orphan_artifacts(index, store, report)
    integrity.check_identity_and_mapping(index, settings, report)
    integrity.check_text_chunks(index, report)
    integrity.check_captions_shallow(index, report)

    return emit(report, args, paths)


if __name__ == "__main__":
    run_stage(main, build_parser().parse_args())
