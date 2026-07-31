"""Batch ingest pipeline — embeds Q/A pairs from JSONL conversation files into ChromaDB.

Replaces the inline per-turn store() call when CACHE_INLINE_STORE_ENABLED=false.
Idempotent: the SHA256 doc ID means re-running never creates duplicates.

Data source: data/conversations/cleaned_conversations/ (CACHE_INGEST_SOURCE_DIR).
Only the conversation source is supported — the audit_trail.jsonl path has been removed.

Usage
-----
CLI (run from agent-mesh directory):
    python -m src.cache.ingest_pipeline [--source-dir PATH] [--dry-run] [--overwrite] [--role ROLE]

API endpoint (triggers background job):
    POST /api/cache/ingest
    GET  /api/cache/ingest/{job_id}
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import pathlib
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Dict, List, Optional

from src.config import Config

_log = logging.getLogger("agent_mesh.cache.ingest")


@dataclass
class IngestReport:
    """Summary returned by run_ingest()."""
    total_scanned: int = 0
    already_present: int = 0
    newly_stored: int = 0
    paraphrases_stored: int = 0
    skipped_stale: int = 0
    skipped_empty: int = 0
    skipped_cache_hit: int = 0
    errors: List[str] = field(default_factory=list)
    elapsed_ms: float = 0.0

    def as_dict(self) -> dict:
        return {
            "total_scanned": self.total_scanned,
            "already_present": self.already_present,
            "newly_stored": self.newly_stored,
            "paraphrases_stored": self.paraphrases_stored,
            "skipped_stale": self.skipped_stale,
            "skipped_empty": self.skipped_empty,
            "skipped_cache_hit": self.skipped_cache_hit,
            "errors": self.errors,
            "elapsed_ms": round(self.elapsed_ms, 1),
        }


# ---------------------------------------------------------------------------
# In-memory job tracker for the API endpoint
# ---------------------------------------------------------------------------

@dataclass
class IngestJob:
    job_id: str
    status: str = "running"   # "running" | "done" | "error"
    report: Optional[IngestReport] = None
    error: str = ""
    started_at: float = field(default_factory=time.time)
    finished_at: Optional[float] = None


_jobs: Dict[str, IngestJob] = {}


def get_job(job_id: str) -> Optional[IngestJob]:
    return _jobs.get(job_id)


# ---------------------------------------------------------------------------
# Paraphrase augmentation
# ---------------------------------------------------------------------------

def _generate_paraphrases(query: str, n: int) -> list[str]:
    """Generate up to n paraphrases of query via the project's LLM provider.

    Uses the same OpenAI-compatible client (LLM_BASE_URL + GROQ_API_KEY + GROQ_MODEL)
    that the rest of the mesh uses — no separate API key required.
    Retries up to 3 times with exponential backoff on HTTP 429 (rate limit).
    Returns [] on failure or when CACHE_PARAPHRASE_ENABLED=false.
    """
    if not Config.CACHE_PARAPHRASE_ENABLED:
        return []

    from openai import OpenAI, RateLimitError

    client = OpenAI(base_url=Config.LLM_BASE_URL, api_key=Config.GROQ_API_KEY)
    prompt = (
        f"Generate {n} paraphrases of this banking query. "
        f"Preserve any customer/account/deal IDs exactly as-is. "
        f"Cover DIFFERENT linguistic styles — mix of:\n"
        f"  - imperative forms (Check..., Find..., Show..., Calculate..., Get...)\n"
        f"  - question forms (What is..., How much..., Can you show...)\n"
        f"  - short fragments (CUST001 RWA, margin for CUST001)\n"
        f"  - different prepositions (for / regarding / on / related to / of)\n"
        f"  - passive/formal (Provide the..., Please display...)\n"
        f"Do NOT just rephrase with similar words — vary the sentence structure.\n"
        f"Return only the paraphrases, one per line, no numbering, no explanation.\n\n"
        f"Query: {query}"
    )

    backoff = 10.0  # seconds; doubles on each retry
    for attempt in range(3):
        try:
            resp = client.chat.completions.create(
                model=Config.GROQ_MODEL,
                max_tokens=256,
                temperature=1.0,
                messages=[{"role": "user", "content": prompt}],
            )
            text = resp.choices[0].message.content or ""
            lines = text.strip().splitlines()
            return [line.strip() for line in lines if line.strip() and line.strip() != query][:n]
        except RateLimitError:
            _log.warning(
                "paraphrase: 429 rate limit for %r — sleeping %.0fs before retry %d/3",
                query[:50], backoff, attempt + 1,
            )
            time.sleep(backoff)
            backoff *= 2
        except Exception as exc:
            _log.warning("paraphrase generation failed for %r: %s", query[:60], exc)
            return []

    _log.warning("paraphrase: gave up after 3 retries for %r", query[:50])
    return []


# ---------------------------------------------------------------------------
# Core ingest logic
# ---------------------------------------------------------------------------

def _notify_server_reload(base_url: str = "http://127.0.0.1:8000") -> None:
    """POST /api/cache/reload so the running API server reloads its ChromaDB HNSW index.

    Swallows all errors — the server may not be running when ingest is called from CLI.
    """
    try:
        import urllib.request
        req = urllib.request.Request(
            f"{base_url}/api/cache/reload",
            method="POST",
            headers={"Content-Type": "application/json"},
            data=b"{}",
        )
        with urllib.request.urlopen(req, timeout=5) as resp:
            data = json.loads(resp.read())
            _log.info(
                "ingest: server reload OK — %d entries now visible in API server",
                data.get("entries", "?"),
            )
    except Exception as exc:
        _log.info(
            "ingest: server reload skipped (%s) — restart the API server to pick up new entries",
            exc,
        )


def run_ingest_sync(
    source_dir: str = "",
    dry_run: bool = False,
    overwrite: bool = False,
    role_filter: Optional[str] = None,
    max_age_hours: Optional[float] = None,
) -> IngestReport:
    """Blocking worker. Runs inside asyncio.to_thread from the async entry point.

    Reads all *.jsonl files in source_dir, extracts user→assistant pairs,
    skips pairs already in ChromaDB (idempotent via SHA256 doc ID),
    and embeds + stores the rest.
    """
    from src.cache.semantic_cache import get_cache_store, SemanticCacheStore

    report = IngestReport()
    t0 = time.perf_counter()

    if not Config.ENABLE_RESPONSE_CACHE:
        _log.info("ingest: ENABLE_RESPONSE_CACHE=false — nothing to do")
        report.elapsed_ms = (time.perf_counter() - t0) * 1000
        return report

    store = get_cache_store()
    store._warmup()

    conv_dir = pathlib.Path(source_dir or Config.CACHE_INGEST_SOURCE_DIR)
    if not conv_dir.exists():
        _log.info("ingest: source dir %s not found — nothing to index", conv_dir)
        report.elapsed_ms = (time.perf_counter() - t0) * 1000
        return report

    jsonl_files = sorted(conv_dir.glob("*.jsonl"))
    _log.info("ingest: scanning %d JSONL files in %s", len(jsonl_files), conv_dir)

    for jsonl_path in jsonl_files:
        try:
            _ingest_file(store, jsonl_path, report, dry_run, overwrite, role_filter, max_age_hours)
        except Exception as exc:
            msg = f"{jsonl_path.name}: {exc}"
            _log.warning("ingest: failed to process %s", msg)
            report.errors.append(msg)

    report.elapsed_ms = (time.perf_counter() - t0) * 1000
    _log.info(
        "ingest: done scanned=%d present=%d stored=%d paraphrases=%d stale=%d empty=%d cache_hit=%d errors=%d elapsed=%.0fms",
        report.total_scanned, report.already_present, report.newly_stored,
        report.paraphrases_stored, report.skipped_stale, report.skipped_empty,
        report.skipped_cache_hit, len(report.errors), report.elapsed_ms,
    )
    if not dry_run:
        _notify_server_reload()
    return report


def _ingest_file(
    store,
    path: pathlib.Path,
    report: IngestReport,
    dry_run: bool,
    overwrite: bool,
    role_filter: Optional[str],
    max_age_hours: Optional[float] = None,
) -> None:
    """Parse one JSONL session file and ingest all valid Q/A pairs."""
    records: list[dict] = []
    with path.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            # Filters to user/assistant turns only — rolling_summary records (no 'role' key) are excluded
            if isinstance(rec, dict) and rec.get("role") in ("user", "assistant"):
                records.append(rec)

    i = 0
    while i < len(records) - 1:
        user_rec = records[i]
        asst_rec = records[i + 1]
        if user_rec.get("role") == "user" and asst_rec.get("role") == "assistant":
            query = (user_rec.get("content") or "").strip()
            answer = (asst_rec.get("content") or "").strip()
            role = asst_rec.get("role_at_time") or _infer_role_from_filename(path.stem)
            route = asst_rec.get("route") or "unknown"
            session_id = path.stem
            request_id = asst_rec.get("request_id") or ""
            ts_str = asst_rec.get("ts") or ""
            blocked = bool(asst_rec.get("blocked", False))
            is_cache_hit = bool(asst_rec.get("cache_hit", False))

            try:
                ts = datetime.fromisoformat(ts_str) if ts_str else datetime.now(timezone.utc)
            except ValueError:
                ts = datetime.now(timezone.utc)

            report.total_scanned += 1
            i += 2

            if not query or not answer or not role:
                report.skipped_empty += 1
                continue

            if blocked:
                report.skipped_empty += 1
                continue

            if is_cache_hit:
                report.skipped_cache_hit += 1
                continue

            if role_filter and role != role_filter:
                report.skipped_empty += 1
                continue

            # Check staleness (max_age_hours overrides Config for bulk historical ingest)
            age_hours = (time.time() - ts.timestamp()) / 3600.0
            effective_max_age = max_age_hours if max_age_hours is not None else Config.CACHE_MAX_AGE_HOURS
            if age_hours > effective_max_age:
                report.skipped_stale += 1
                continue

            # Check if already present (by deterministic doc ID)
            doc_id = store._doc_id(role, query)
            if not overwrite:
                try:
                    existing = store._collection.get(ids=[doc_id])
                    if existing and existing.get("ids"):
                        report.already_present += 1
                        continue
                except Exception:
                    pass

            if dry_run:
                _log.info("ingest [dry-run]: would store role=%s session=%s query_preview=%r",
                          role, session_id, query[:60])
                report.newly_stored += 1
                continue

            # reasoning may already be a list (cleaned_conversations) or a JSON string (legacy)
            raw_reasoning = asst_rec.get("reasoning") or []
            if isinstance(raw_reasoning, str):
                try:
                    reasoning = json.loads(raw_reasoning) or []
                    if not isinstance(reasoning, list):
                        reasoning = []
                except (json.JSONDecodeError, TypeError):
                    reasoning = []
            elif isinstance(raw_reasoning, list):
                reasoning = raw_reasoning
            else:
                reasoning = []

            # Extract the entity signature so the gate can match on entities at lookup.
            _entities = None
            if Config.CACHE_ENTITY_GATING_ENABLED:
                from src.cache.entity_extractor import extract_entities_sync, signature_to_str
                _entities = signature_to_str(extract_entities_sync(query))

            store.store(
                query=query,
                answer=answer,
                role=role,
                route=route,
                session_id=session_id,
                request_id=request_id,
                ts=ts,
                reasoning=reasoning,
                entities=_entities,
            )
            report.newly_stored += 1

            # Paraphrase augmentation — store variants pointing to same answer.
            # Sleep after the LLM call to stay within provider RPM limits.
            paraphrases = _generate_paraphrases(query, Config.CACHE_PARAPHRASE_N)
            if paraphrases and Config.CACHE_PARAPHRASE_DELAY_S > 0:
                time.sleep(Config.CACHE_PARAPHRASE_DELAY_S)
            for para in paraphrases:
                para_id = store._doc_id(role, para)
                try:
                    existing = store._collection.get(ids=[para_id])
                    if existing and existing.get("ids") and not overwrite:
                        continue
                except Exception:
                    pass
                store.store(
                    query=para,
                    answer=answer,
                    role=role,
                    route=route,
                    session_id=session_id,
                    request_id=request_id,
                    ts=ts,
                    reasoning=reasoning,
                    entities=_entities,
                )
                report.paraphrases_stored += 1
        else:
            i += 1


def backfill_entities_sync(dry_run: bool = False, role_filter: Optional[str] = None) -> dict:
    """Extract + store entity signatures for existing ChromaDB entries missing them.

    Iterates the collection, and for every entry whose metadata lacks the
    ``entities`` key, extracts the signature from its stored query text and
    updates the metadata in place (no re-embed). Idempotent — re-running only
    touches entries that are still missing the key.
    """
    from src.cache.semantic_cache import get_cache_store
    from src.cache.entity_extractor import extract_entities_sync, signature_to_str

    result = {"scanned": 0, "backfilled": 0, "already_present": 0, "skipped_role": 0, "errors": 0}
    if not Config.ENABLE_RESPONSE_CACHE:
        _log.info("backfill: ENABLE_RESPONSE_CACHE=false — nothing to do")
        return result

    store = get_cache_store()
    store._ensure_initialized()
    col = store._collection
    got = col.get(include=["documents", "metadatas"])
    ids = got.get("ids", []) or []
    docs = got.get("documents", []) or []
    metas = got.get("metadatas", []) or []

    for doc_id, doc, meta in zip(ids, docs, metas):
        result["scanned"] += 1
        meta = meta or {}
        if role_filter and meta.get("role") != role_filter:
            result["skipped_role"] += 1
            continue
        if "entities" in meta:
            result["already_present"] += 1
            continue
        try:
            sig = signature_to_str(extract_entities_sync(doc or ""))
            if dry_run:
                _log.info("backfill [dry-run]: would set entities=%r for id=%s query=%r",
                          sig, doc_id, (doc or "")[:60])
                result["backfilled"] += 1
                continue
            new_meta = dict(meta)
            new_meta["entities"] = sig
            with store._write_lock:
                col.update(ids=[doc_id], metadatas=[new_meta])
            result["backfilled"] += 1
        except Exception as exc:
            _log.warning("backfill: failed for id=%s: %s", doc_id, exc)
            result["errors"] += 1

    _log.info("backfill: done %s", result)
    return result


def _infer_role_from_filename(stem: str) -> str:
    try:
        from src.auth.identity_provider import login
        username = stem.rsplit("_", 1)[0] if "_" in stem else stem
        user = login(username)
        return user.role.value if user else ""
    except Exception:
        return ""


# ---------------------------------------------------------------------------
# Async entry point (used by API endpoint)
# ---------------------------------------------------------------------------

async def run_ingest(
    source_dir: str = "",
    dry_run: bool = False,
    overwrite: bool = False,
    role_filter: Optional[str] = None,
) -> IngestReport:
    """Async wrapper — delegates blocking work to a thread pool."""
    return await asyncio.to_thread(
        run_ingest_sync, source_dir, dry_run, overwrite, role_filter
    )


async def run_ingest_job(
    job_id: str,
    *,
    source_dir: str = "",
    dry_run: bool = False,
    overwrite: bool = False,
    role_filter: Optional[str] = None,
    entity_mode: str = "llm",
) -> None:
    """Run ingest as a background task and update the job record."""
    job = _jobs.get(job_id)
    if job is None:
        return
    try:
        report = await run_ingest(
            source_dir=source_dir, dry_run=dry_run,
            overwrite=overwrite, role_filter=role_filter,
        )
        job.report = report
        job.status = "done"
    except Exception as exc:
        job.status = "error"
        job.error = str(exc)
        _log.warning("ingest job %s failed: %s", job_id, exc)
    finally:
        job.finished_at = time.time()


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

def _cli() -> None:
    parser = argparse.ArgumentParser(
        description="Batch-ingest JSONL conversation files into the semantic cache.",
    )
    parser.add_argument("--source-dir", default="",
                        help="Path to JSONL directory (default: CACHE_INGEST_SOURCE_DIR → data/conversations/cleaned_conversations)")
    parser.add_argument("--dry-run", action="store_true",
                        help="Log what would be stored without writing to ChromaDB")
    parser.add_argument("--overwrite", action="store_true",
                        help="Re-embed and overwrite existing entries")
    parser.add_argument("--role", default="", help="Only ingest turns for this role")
    parser.add_argument("--max-age-hours", type=float, default=None,
                        help="Override CACHE_MAX_AGE_HOURS for this run (e.g. 99999 to ingest all history)")
    parser.add_argument("--backfill-entities", action="store_true",
                        help="Extract + store entity signatures for existing entries missing them, then exit")
    args = parser.parse_args()

    import logging as _logging
    _logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")

    if args.backfill_entities:
        result = backfill_entities_sync(dry_run=args.dry_run, role_filter=args.role or None)
        print("\n=== Entity Backfill Report ===")
        for k, v in result.items():
            print(f"  {k}: {v}")
        return

    report = run_ingest_sync(
        source_dir=args.source_dir,
        dry_run=args.dry_run,
        overwrite=args.overwrite,
        role_filter=args.role or None,
        max_age_hours=args.max_age_hours,
    )
    print("\n=== Ingest Report ===")
    for k, v in report.as_dict().items():
        print(f"  {k}: {v}")


if __name__ == "__main__":
    _cli()
