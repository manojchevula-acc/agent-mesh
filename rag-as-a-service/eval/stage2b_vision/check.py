"""Stage 2b — Vision perception fidelity.

RAG-Check calls the failure this stage isolates *context-generation-
hallucination*: the vision model misreading the pixels. Everything here is
deliberately decoupled from retrieval and from the answer prompt — one crop,
one model call, one comparison against a human transcription.

Why isolate it: eval_runs.md case 12 (the MRM six-panel dashboard) was logged
as a RETRIEVAL failure. It was not. The chart was extracted, indexed and
retrieved correctly; the monthly value labels were simply sub-pixel at
vision_image_max_side_px=256 and the model could not read them. No retrieval
metric can catch that, and no prompt change can fix it. This stage measures it
directly, and it is also the stage that tells you what resolution is enough.

The model sees the crop at exactly the resolution the live answer path would
send it — the same ImagePayloadBuilder, the same vision_image_max_side_px — so
a pass here means the real pipeline can read the figure, not merely that some
larger version of it was legible.
"""

import asyncio
import re
import sys

sys.path.insert(0, "src")

from gernas_rag.config.settings import get_settings  # noqa: E402
from gernas_rag.generation.image_payload import ImagePayloadBuilder  # noqa: E402
from gernas_rag.images.store import get_asset_store  # noqa: E402
from gernas_rag.llm.base import ImagePart, Message, TextPart  # noqa: E402
from gernas_rag.llm.factory import get_llm  # noqa: E402
from gernas_rag.models.retrieval import RetrievedImage  # noqa: E402

from ..common import numeric  # noqa: E402
from ..common.ground_truth import Transcription, load_transcriptions  # noqa: E402
from ..common.text import normalize  # noqa: E402
from ..core import report  # noqa: E402
from ..core.cases import CaseStore  # noqa: E402

# Mirrors the paper's isolated-crop prompt: describe, do not interpret. Asking
# for a reading rather than an analysis keeps the output comparable to a
# transcription, and keeps hedging out of the numeric comparison.
_PROMPT = (
    "Transcribe this figure exactly as printed. List: the title, every axis "
    "label and its tick values, every legend entry, and every data value or "
    "cell you can read. Do not interpret, summarise, or infer trends. If a "
    "value is not legible, write 'illegible' — do NOT estimate it."
)


def _pct(value: float | None) -> str:
    return "n/a" if value is None else f"{value:.0%}"


# Word characters plus % and the decimal point, so "18.4%" survives as one
# token while "(bps);" reduces to "bps".
_WORD = re.compile(r"[a-z0-9][a-z0-9.%/-]*", re.IGNORECASE)


def _tokenize(text: str) -> list[str]:
    return _WORD.findall(normalize(text))


def _entity_recall(expected: str, actual: str) -> tuple[float | None, list[str]]:
    """Recall over the transcription's non-numeric content words.

    Complements numeric fidelity: a model can get every number right and still
    attach them to the wrong series. Stopwords and pure numbers are dropped so
    the score reflects labels, legend entries and axis names.
    """
    stop = {
        "the", "and", "a", "an", "of", "to", "in", "on", "at", "is", "are", "for",
        "with", "by", "from", "as", "it", "its", "this", "that", "line", "bar",
        "axis", "label", "ticks", "legend", "title", "values", "left", "right",
        "top", "bottom", "each", "above", "below", "per", "no", "not", "text",
    }
    # Strip punctuation from BOTH sides before comparing. Without this,
    # "(bps);" from the transcription is compared literally against a reply
    # that says "bps" and can never match — measured on a real run, almost
    # every reported "missing label" was a punctuation artefact rather than a
    # word the model failed to read.
    words = {
        w for w in (_tokenize(expected))
        if len(w) > 2 and w not in stop and not w.replace(".", "").isdigit()
    }
    if not words:
        return None, []

    haystack = set(_tokenize(actual))
    missing = sorted(w for w in words if w not in haystack)
    return (len(words) - len(missing)) / len(words), missing


class CropUnavailable(RuntimeError):
    """The asset could not be loaded, so there is nothing to score."""


async def _read_crop(llm, builder: ImagePayloadBuilder, item: Transcription) -> str:
    """Send one crop through the real vision path and return the raw reading."""
    part = builder.build(
        RetrievedImage(
            asset_id=item.asset_id,
            uri="",
            caption="",
            source=item.document,
            page_number=item.page,
            score=1.0,
            rank=1,
        )
    )
    if part is None:
        # ImagePayloadBuilder swallows a missing/corrupt asset and returns None
        # so live generation never dies over one image. Here that must be an
        # ERROR, not an empty reading: scoring "" gives numeric_fidelity 0%,
        # which reads as "the model misread the chart" when the truth is the
        # file is not on disk. Two different bugs, two different fixes.
        raise CropUnavailable(
            f"asset {item.asset_id} could not be loaded from the store "
            f"({item.document} p{item.page}) — re-ingest that document"
        )
    return await llm.generate(
        [Message(role="user", content=[TextPart(text=_PROMPT), ImagePart(data_uri=part.data_uri)])]
    )


def run(
    limit: int | None = None,
    delay: float = 1.0,
    fresh: bool = False,
    rescore: bool = False,
) -> report.StageResult:
    settings = get_settings()
    items = load_transcriptions()

    result = report.StageResult(
        stage="stage2b",
        title="Stage 2b — Vision Perception Fidelity",
        context={
            "vision_model": settings.llm.vision_model_name,
            "max_side_px": settings.llm.vision_image_max_side_px,
            "transcriptions": len(items),
        },
    )

    if not settings.llm.vision_enabled:
        result.notes.append("llm.vision_enabled is false — nothing to measure.")
        for name in ("numeric_fidelity", "entity_recall", "no_fabrication_rate"):
            result.add(name, report.NA, "vision disabled")
        return result

    scoreable = [i for i in items if i.scoreable]
    if not scoreable:
        unmapped = sum(1 for i in items if i.verified and not i.asset_id)
        detail = (
            f"{unmapped} verified transcription(s) have no asset_id — run "
            "`python -m eval remap` first"
            if unmapped
            else "no verified transcriptions"
        )
        for name in ("numeric_fidelity", "entity_recall", "no_fabrication_rate"):
            result.add(name, report.NA, detail)
        return result

    # Checkpoint so a quota wall costs nothing already paid for, and so
    # repeated --limit runs accumulate instead of overwriting each other.
    checkpoint = CaseStore("stage2b")
    if fresh:
        checkpoint.clear()

    if rescore:
        # Recompute every score from the persisted readings — no API calls.
        # This is the path for "I fixed a scorer bug", which on this stage has
        # already happened twice.
        by_id = {i.asset_id: i for i in items if i.asset_id}
        rewritten = missing_reading = 0
        for row in checkpoint.rows():
            item, reading = by_id.get(row["key"]), row.get("reading")
            if row.get("error") or item is None:
                continue
            if reading is None:
                missing_reading += 1
                continue
            n_score, n_missing = numeric.recall(item.transcription, reading)
            e_score, e_missing = _entity_recall(item.transcription, reading)
            f_score, f_invented = numeric.fabricated(item.transcription, reading)
            checkpoint.append(
                row["key"],
                {**row, "numeric": n_score, "entity": e_score,
                 "clean": None if f_score is None else 1.0 - f_score,
                 "detail": (
                     ("missed " + ", ".join(n_missing[:4]) if n_missing else "")
                     + (" | invented " + ", ".join(f_invented[:3]) if f_invented else "")
                     + (" | no labels " + ", ".join(e_missing[:3]) if e_missing else "")
                 ).strip(" |")},
            )
            rewritten += 1
        print(f"rescored {rewritten} crop(s) from stored readings — no API calls")
        if missing_reading:
            print(
                f"{missing_reading} crop(s) predate reading capture and CANNOT be "
                "rescored; re-run them to pick up the scorer fixes."
            )

    already = checkpoint.done_keys()
    total_scoreable = len(scoreable)
    scoreable = [i for i in scoreable if i.asset_id not in already]
    if limit:
        scoreable = scoreable[:limit]

    asset_store = get_asset_store(settings.multimodal.storage)
    builder = ImagePayloadBuilder(asset_store, settings.llm)

    # vision_fallback_to_text MUST be off here, even though production wants it
    # on. In production, degrading to a text-only answer when the vision model
    # 429s is correct — the user gets a worse answer instead of an error.
    #
    # In THIS stage it is catastrophic. The router silently strips the image and
    # retries; the model then "transcribes" a figure it was never shown, and the
    # response comes back looking like a successful reading. Measured on a real
    # rate-limited run: those crops scored entity_recall 0.02-0.12 versus
    # 0.36-0.88 for genuine readings, and were checkpointed as SUCCESSES.
    # Fabricated data is worse than missing data, so a rate limit must raise.
    llm = get_llm(
        settings.llm.model_copy(
            update={
                "model_name": settings.llm.vision_model_name,
                "vision_model_name": settings.llm.vision_model_name,
                "vision_fallback_to_text": False,
            }
        )
    )

    async def _drive() -> None:
        for item in scoreable:
            try:
                reading = await _read_crop(llm, builder, item)
            except Exception as exc:  # noqa: BLE001 - one bad crop must not end the run
                # Checkpointed WITHOUT a score, so a rerun retries this crop
                # while keeping every crop already paid for.
                checkpoint.append(
                    item.asset_id,
                    {"document": item.document, "page": item.page, "error": str(exc)[:200]},
                )
                print(f"  {item.document} p{item.page} FAILED: {str(exc)[:110]}")
                if delay:
                    await asyncio.sleep(delay)
                continue

            n_score, n_missing = numeric.recall(item.transcription, reading)
            e_score, e_missing = _entity_recall(item.transcription, reading)
            f_score, f_invented = numeric.fabricated(item.transcription, reading)

            checkpoint.append(
                item.asset_id,
                {
                    "document": item.document,
                    "page": item.page,
                    # The RAW model reading is persisted, not just its scores.
                    # Scorers are the part most likely to need correcting (two
                    # bugs found on the first full run), and without the reading
                    # every scorer fix costs a full re-run of paid API calls.
                    # With it, `--rescore` reruns the maths for free.
                    "reading": reading,
                    "max_side_px": settings.llm.vision_image_max_side_px,
                    "numeric": n_score,
                    "entity": e_score,
                    "clean": None if f_score is None else 1.0 - f_score,
                    "detail": (
                        ("missed " + ", ".join(n_missing[:4]) if n_missing else "")
                        + (" | invented " + ", ".join(f_invented[:3]) if f_invented else "")
                        + (" | no labels " + ", ".join(e_missing[:3]) if e_missing else "")
                    ).strip(" |"),
                }
            )
            if delay:
                await asyncio.sleep(delay)

    asyncio.run(_drive())

    # Aggregate over EVERY crop ever recorded, so repeated --limit runs build
    # up a full picture instead of each overwriting the last.
    rows = checkpoint.rows()
    scored = [r for r in rows if not r.get("error")]
    errored = [r for r in rows if r.get("error")]

    def mean(name: str) -> float | None:
        xs = [r[name] for r in scored if r.get(name) is not None]
        return sum(xs) / len(xs) if xs else report.NA

    def count(name: str) -> int:
        return len([r for r in scored if r.get(name) is not None])

    result.add("numeric_fidelity", mean("numeric"), f"over {count('numeric')} crops with numbers")
    result.add("entity_recall", mean("entity"), f"over {count('entity')} crops")
    result.add("no_fabrication_rate", mean("clean"), f"over {count('clean')} crops")

    result.rows = [
        {
            "document": r.get("document", ""),
            "page": r.get("page", ""),
            "numeric": "err" if r.get("error") else _pct(r.get("numeric")),
            "entity": "err" if r.get("error") else _pct(r.get("entity")),
            "clean": "err" if r.get("error") else _pct(r.get("clean")),
            "detail": (r.get("error") or r.get("detail", ""))[:150],
        }
        for r in rows
    ]

    result.context["scored_total"] = f"{len(scored)}/{total_scoreable} crops recorded"
    remaining = total_scoreable - len(already) - len(scoreable)
    if remaining > 0:
        result.notes.append(
            f"{remaining} crop(s) not yet run. Re-run the same command to continue "
            "— already-scored crops are skipped and their scores retained."
        )
    if errored:
        result.notes.append(
            f"{len(errored)} crop(s) errored (likely rate limit) and were NOT scored. "
            "Re-run to retry only those."
        )

    skipped = len(items) - total_scoreable
    if skipped:
        result.notes.append(
            f"{skipped} transcription(s) skipped: unverified, or asset_id not yet "
            "mapped by `python -m eval remap`."
        )
    result.notes.append(
        f"Crops were read at max_side_px={settings.llm.vision_image_max_side_px}. "
        "If numeric_fidelity is low, raise it before touching retrieval or the prompt."
    )
    return result
