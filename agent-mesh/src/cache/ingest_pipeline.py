"""Batch ingest pipeline — embeds Q/A pairs from JSONL conversation files into ChromaDB.

Replaces the inline per-turn store() call when CACHE_INLINE_STORE_ENABLED=false.
Idempotent: the SHA256 doc ID means re-running never creates duplicates.

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
# Core ingest logic
# ---------------------------------------------------------------------------

def run_ingest_sync(
    source_dir: str = "",
    dry_run: bool = False,
    overwrite: bool = False,
    role_filter: Optional[str] = None,
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

    conv_dir = pathlib.Path(source_dir or Config.CONVERSATION_STORE_DIR)
    if not conv_dir.exists():
        _log.info("ingest: conversation dir %s not found — nothing to index", conv_dir)
        report.elapsed_ms = (time.perf_counter() - t0) * 1000
        return report

    jsonl_files = sorted(conv_dir.glob("*.jsonl"))
    _log.info("ingest: scanning %d JSONL files in %s", len(jsonl_files), conv_dir)

    for jsonl_path in jsonl_files:
        try:
            _ingest_file(store, jsonl_path, report, dry_run, overwrite, role_filter)
        except Exception as exc:
            msg = f"{jsonl_path.name}: {exc}"
            _log.warning("ingest: failed to process %s", msg)
            report.errors.append(msg)

    report.elapsed_ms = (time.perf_counter() - t0) * 1000
    _log.info(
        "ingest: done scanned=%d present=%d stored=%d stale=%d empty=%d cache_hit=%d errors=%d elapsed=%.0fms",
        report.total_scanned, report.already_present, report.newly_stored,
        report.skipped_stale, report.skipped_empty, report.skipped_cache_hit,
        len(report.errors), report.elapsed_ms,
    )
    return report


def _ingest_file(
    store,
    path: pathlib.Path,
    report: IngestReport,
    dry_run: bool,
    overwrite: bool,
    role_filter: Optional[str],
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

            # Check staleness
            age_hours = (time.time() - ts.timestamp()) / 3600.0
            if age_hours > Config.CACHE_MAX_AGE_HOURS:
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

            reasoning: list = []
            try:
                reasoning = json.loads(asst_rec.get("reasoning") or "[]") or []
                if not isinstance(reasoning, list):
                    reasoning = []
            except (json.JSONDecodeError, TypeError):
                reasoning = []

            store.store(
                query=query,
                answer=answer,
                role=role,
                route=route,
                session_id=session_id,
                request_id=request_id,
                ts=ts,
                reasoning=reasoning,
            )
            report.newly_stored += 1
        else:
            i += 1


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


async def run_ingest_job(job_id: str, **kwargs) -> None:
    """Run ingest as a background task and update the job record."""
    job = _jobs.get(job_id)
    if job is None:
        return
    try:
        report = await run_ingest(**kwargs)
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
    parser.add_argument("--source-dir", default="", help="Path to JSONL directory (default: CONVERSATION_STORE_DIR)")
    parser.add_argument("--dry-run", action="store_true", help="Log what would be stored without writing to ChromaDB")
    parser.add_argument("--overwrite", action="store_true", help="Re-embed and overwrite existing entries")
    parser.add_argument("--role", default="", help="Only ingest turns for this role")
    args = parser.parse_args()

    import logging as _logging
    _logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")

    report = run_ingest_sync(
        source_dir=args.source_dir,
        dry_run=args.dry_run,
        overwrite=args.overwrite,
        role_filter=args.role or None,
    )
    print("\n=== Ingest Report ===")
    for k, v in report.as_dict().items():
        print(f"  {k}: {v}")


if __name__ == "__main__":
    _cli()
