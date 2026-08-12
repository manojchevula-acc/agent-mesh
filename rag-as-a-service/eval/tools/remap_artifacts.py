"""Bind the imported transcriptions to THIS pipeline's asset ids.

The transcriptions arrived from a pipeline that identified each crop by
``sha256:<64-hex>.png`` — the full digest of the raw PNG Docling emits. This
pipeline identifies the same crop by ``sha256(normalised WEBP bytes)[:32]``
(``utils.hashing.make_asset_id``). Different bytes, different digest, different
length: the two ids cannot be converted into one another arithmetically.

They CAN be bridged by recomputing both from the same source. Re-extract each
PDF, and for every crop compute:

    their id  = sha256(raw PNG bytes)
    our id    = sha256(normalize(raw PNG bytes))[:32]

which yields an exact, verifiable mapping — no page-ordinal guessing, and it
stays correct when a page holds two figures (Portfolio Risk p3 holds exactly
that case).

Extraction here mirrors ingestion exactly: same DoclingImageExtractor, same
filters, same ``normalize``. If it did not, the ids would not match what is
actually in image_store/.

    python -m eval remap --dry-run
    python -m eval remap
"""

import json
import subprocess
import sys

sys.path.insert(0, "src")

from gernas_rag.config.settings import get_settings  # noqa: E402
from gernas_rag.images.store import get_asset_store  # noqa: E402

from ..common.ground_truth import (  # noqa: E402
    load_manifest,
    load_transcriptions,
    save_transcriptions,
)
from ._extract_worker import SENTINEL  # noqa: E402


# Native crash codes, so the report says "crashed" instead of an opaque number.
# Windows surfaces an access violation as 0xC0000005 (3221225477); POSIX
# reports SIGSEGV as -11, and shells re-encode it as 139.
_NATIVE_CRASH = {3221225477: "access violation (0xC0000005)", -11: "SIGSEGV", 139: "SIGSEGV"}


def _crash_reason(proc: subprocess.CompletedProcess) -> str:
    if proc.returncode in _NATIVE_CRASH:
        return f"{_NATIVE_CRASH[proc.returncode]} — Docling crashed on this PDF"
    tail = (proc.stderr or "").strip().splitlines()
    return f"exit {proc.returncode}" + (f": {tail[-1][:120]}" if tail else "")


def _index_corpus() -> tuple[dict[str, dict], list[str]]:
    """Walk every manifest PDF, returning {their_ref: {asset_id, doc, page}}.

    Each document is extracted in a SUBPROCESS. Docling segfaults on at least
    one document in this corpus, and a segfault is not catchable in-process —
    running in-line means one bad PDF silently costs the whole mapping. The
    crashed documents come back as a list so they are reported, not hidden.
    """
    by_ref: dict[str, dict] = {}
    crashed: list[str] = []

    for doc in load_manifest():
        proc = subprocess.run(
            [sys.executable, "-m", "eval.tools._extract_worker", str(doc.file)],
            capture_output=True,
            text=True,
            timeout=900,
        )
        if proc.returncode != 0 or SENTINEL not in proc.stdout:
            crashed.append(doc.document)
            print(f"  {doc.document:<48} EXTRACTION FAILED — {_crash_reason(proc)}")
            continue

        crops = json.loads(proc.stdout.split(SENTINEL, 1)[1].strip())
        for crop in crops:
            by_ref[crop["their_ref"]] = {
                "asset_id": crop["asset_id"],
                "document": doc.document,
                "page": crop["page"],
                "role": crop["role"],
                "rejected": crop.get("rejected"),
            }
        kept = sum(1 for c in crops if not c.get("rejected"))
        dropped = len(crops) - kept
        print(
            f"  {doc.document:<48} kept={kept:>3}"
            + (f"  filtered={dropped}" if dropped else "")
        )

    return by_ref, crashed


def run(dry_run: bool = False) -> int:
    settings = get_settings()
    if not settings.multimodal.enabled:
        print(
            "multimodal.enabled is false — the image path is off, so there are "
            "no assets to map onto. Enable it (RAG__MULTIMODAL__ENABLED=true) "
            "and re-run."
        )
        return 2

    print("Re-extracting the corpus to bridge artifact ids ...")
    by_ref, crashed = _index_corpus()
    print(f"\n{len(by_ref)} crops indexed from the corpus.\n")

    store = get_asset_store(settings.multimodal.storage)
    items = load_transcriptions()

    matched = missing_asset = unmatched = filtered = 0
    for item in items:
        hit = by_ref.get(item.artifact_ref)
        if hit is None:
            unmatched += 1
            print(f"  UNMATCHED  {item.document} p{item.page}  {item.artifact_ref[:26]}…")
            continue

        # The crop exists in the corpus but this pipeline's filters dropped it,
        # so no asset was ever stored. That is correct behaviour, not a defect —
        # report it as such rather than as a failed match.
        if hit.get("rejected"):
            filtered += 1
            item.asset_id = None
            print(
                f"  FILTERED   {item.document} p{item.page} — rejected by ingestion "
                f"filters as '{hit['rejected']}'; no asset exists to score"
            )
            continue

        # Cross-check the provenance we already have. A hash match with the
        # wrong document/page means the two corpora have diverged, and the
        # transcription would be scored against the wrong picture.
        if hit["document"] != item.document or hit["page"] != item.page:
            unmatched += 1
            print(
                f"  CONFLICT   ref matches {hit['document']} p{hit['page']} "
                f"but transcription says {item.document} p{item.page} — skipped"
            )
            continue

        item.asset_id = hit["asset_id"]
        matched += 1
        if not store.exists(item.asset_id):
            missing_asset += 1
            print(
                f"  NO ASSET   {item.document} p{item.page} -> {item.asset_id} "
                f"is not in image_store/ (re-ingest needed)"
            )

    print(
        f"\nmatched={matched}  filtered={filtered}  unmatched={unmatched}  "
        f"matched-but-not-in-store={missing_asset}  of {len(items)} transcriptions"
    )

    if filtered:
        print(
            f"\n{filtered} transcription(s) describe crops this pipeline's image "
            "filters deliberately reject (blank/degenerate regions). They report "
            "n/a in stage2b by design — there is no indexed asset behind them."
        )

    if crashed:
        print(
            f"\n{len(crashed)} document(s) could not be extracted at all: "
            + ", ".join(crashed)
            + "\nTheir transcriptions cannot be mapped and will report n/a in "
            "stage2b. This is a Docling crash in the ingestion path, not an "
            "eval bug — the same crash would hit re-ingestion of these files."
        )

    if unmatched:
        print(
            "\nUnmatched entries keep asset_id=null and report n/a in stage2b "
            "rather than being scored against the wrong image. The usual cause "
            "is a Docling version difference changing the rendered PNG bytes."
        )

    if not dry_run:
        save_transcriptions(items)
        print("\nwritten -> data/eval/figure_transcriptions.json")
    else:
        print("\n(dry run — not written)")
    return 0
