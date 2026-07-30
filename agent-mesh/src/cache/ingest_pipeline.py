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
import re
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
    skipped_negative: int = 0        # audit source: negative/error answers not worth caching
    skipped_role_invalid: int = 0    # audit source: role not a valid BankingRole
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
            "skipped_negative": self.skipped_negative,
            "skipped_role_invalid": self.skipped_role_invalid,
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

    conv_dir = pathlib.Path(source_dir or Config.CONVERSATION_STORE_DIR)
    if not conv_dir.exists():
        _log.info("ingest: conversation dir %s not found — nothing to index", conv_dir)
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

            reasoning: list = []
            try:
                reasoning = json.loads(asst_rec.get("reasoning") or "[]") or []
                if not isinstance(reasoning, list):
                    reasoning = []
            except (json.JSONDecodeError, TypeError):
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
# Audit-trail ingest source (data/audit_trail.jsonl)
# ---------------------------------------------------------------------------
# The audit trail is a per-agent-span log (one JSON line per agent invocation),
# NOT the alternating user/assistant Q&A pairs the conversation store produces.
# This adapter reconstructs one clean, fully-redacted Q/A pair per request by:
#   * grouping spans by trace_id,
#   * choosing the LAST PriceAssistAgent span (the orchestrator/synthesizer;
#     retries produce several — the last is the final answer),
#   * recovering role + bare query from the "[User: x | Role: y]" input prefix,
#   * stripping <llm_reasoning> blocks and re-running the FULL redact_pii
#     (the audit middleware only redacts EMAIL/SSN — this adds CREDIT_CARD/PHONE),
#   * dropping invalid roles and negative/error answers.
# It then calls the same store.store(...) path as the conversation ingest, so
# dedup, entity signatures, staleness and idempotent upsert are all reused.

# PriceAssistAgent inputs[0] prefix: "[User: <name> | Role: <role>]\n<...query...>"
_ROLE_PREFIX_RE = re.compile(
    r"^\[User:\s*(?P<user>.*?)\s*\|\s*Role:\s*(?P<role>[^\]|]*?)\s*\]\s*",
    re.DOTALL,
)
# Marker that precedes the actual question when conversation memory is injected
# (see ConversationStore.format_summary_block / format_history_block).
_CURRENT_QUESTION_MARKER = "[Current question]"


def _extract_role_and_query(price_assist_input: str) -> tuple[str, str]:
    """Recover (role, bare_query) from a PriceAssistAgent inputs[0] string.

    role is normalized lowercase; "" if the prefix is missing/malformed. The
    query is the tail after the role prefix and any injected conversation block
    (everything after the last [Current question] marker when present).
    """
    text = price_assist_input or ""
    match = _ROLE_PREFIX_RE.match(text)
    if match:
        role = (match.group("role") or "").strip().lower()
        rest = text[match.end():]
    else:
        role = ""
        rest = text
    if _CURRENT_QUESTION_MARKER in rest:
        rest = rest.split(_CURRENT_QUESTION_MARKER, 1)[1]
    return role, rest.strip()


def _is_negative_answer(text: str) -> bool:
    """True for 'no data found' / 'unable to retrieve' style non-answers.

    Thin wrapper around the shared detector (src.cache.negative_filter) kept for
    backward-compat with existing tests/imports.
    """
    from src.cache.negative_filter import is_negative_answer
    return is_negative_answer(text)


def _infer_route(has_data: bool, has_rag: bool) -> str:
    """Derive the domain route from which peer agents ran in the trace."""
    if has_data and has_rag:
        return "Hybrid"
    if has_data:
        return "Data Layer Service"
    if has_rag:
        return "RAG"
    return "unknown"


def run_ingest_audit_sync(
    audit_file: str = "",
    dry_run: bool = False,
    overwrite: bool = False,
    role_filter: Optional[str] = None,
    max_age_hours: Optional[float] = None,
    entity_mode: str = "llm",
) -> IngestReport:
    """Blocking worker: ingest the semantic cache from an audit_trail.jsonl file.

    Mirrors run_ingest_sync() but parses the per-span audit log instead of the
    conversation store. Reuses the same store.store(...) path downstream.

    entity_mode controls how the gate's entity signatures are computed for the
    stored entries (only when CACHE_ENTITY_GATING_ENABLED):
      "llm"   → batched LLM extraction (few calls, high fidelity)  [default]
      "regex" → deterministic regex only (instant, no API — covers structured IDs)
      "none"  → do not compute signatures (lookup-time extraction fills them later)
    """
    from src.cache.semantic_cache import get_cache_store
    from src.tracing.llm_reasoning import extract_reasoning
    from src.guardrails.deterministic_filters import redact_pii
    from src.auth.identity_provider import BankingRole

    valid_roles = {r.value for r in BankingRole}

    report = IngestReport()
    t0 = time.perf_counter()

    if not Config.ENABLE_RESPONSE_CACHE:
        _log.info("audit ingest: ENABLE_RESPONSE_CACHE=false — nothing to do")
        report.elapsed_ms = (time.perf_counter() - t0) * 1000
        return report

    store = get_cache_store()
    store._warmup()

    path = pathlib.Path(audit_file or Config.AUDIT_LOG_FILE)
    if not path.exists():
        _log.info("audit ingest: audit file %s not found — nothing to index", path)
        report.elapsed_ms = (time.perf_counter() - t0) * 1000
        return report

    # ── Pass 1: group spans by trace_id ──────────────────────────────────────
    traces: Dict[str, dict] = {}
    with path.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(rec, dict):
                continue
            tid = rec.get("trace_id") or rec.get("span_id") or ""
            if not tid:
                continue
            tr = traces.setdefault(tid, {"price": [], "has_data": False, "has_rag": False})
            agent = rec.get("agent_name")
            if agent == "PriceAssistAgent":
                tr["price"].append(rec)
            elif agent == "DataAgent":
                tr["has_data"] = True
            elif agent == "RAGAgent":
                tr["has_rag"] = True

    _log.info("audit ingest: %d traces found in %s", len(traces), path)

    # ── Pass 2: reconstruct + filter one Q/A per trace (collect pending) ─────
    # Collect first so entity signatures can be batch-extracted (few LLM calls)
    # instead of one call per entry — the latter trips provider rate limits.
    pending: list[dict] = []
    for tid, tr in traces.items():
        price_spans = tr["price"]
        if not price_spans:
            continue  # no synthesizer answer in this trace → nothing to cache
        report.total_scanned += 1
        try:
            # Last PriceAssist span by timestamp = final answer (handles retries).
            chosen = max(price_spans, key=lambda r: r.get("timestamp") or "")

            if chosen.get("status") == "ERROR":
                report.skipped_negative += 1
                continue

            inputs = chosen.get("inputs") or []
            role, query = _extract_role_and_query(inputs[0] if inputs else "")

            if role not in valid_roles:
                report.skipped_role_invalid += 1
                continue
            if role_filter and role != role_filter:
                report.skipped_empty += 1
                continue

            entries, clean = extract_reasoning(chosen.get("output") or "", "price_assist")
            answer = redact_pii(clean).strip()

            if not query or not answer:
                report.skipped_empty += 1
                continue
            if _is_negative_answer(answer):
                report.skipped_negative += 1
                continue

            ts_str = chosen.get("timestamp") or ""
            try:
                ts = datetime.fromisoformat(ts_str) if ts_str else datetime.now(timezone.utc)
            except ValueError:
                ts = datetime.now(timezone.utc)

            age_hours = (time.time() - ts.timestamp()) / 3600.0
            effective_max_age = max_age_hours if max_age_hours is not None else Config.CACHE_MAX_AGE_HOURS
            if age_hours > effective_max_age:
                report.skipped_stale += 1
                continue

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
                _log.info("audit ingest [dry-run]: would store role=%s query_preview=%r",
                          role, query[:60])
                report.newly_stored += 1
                continue

            session_id = chosen.get("session_id") or ""
            if not session_id or session_id == "default_session":
                session_id = tid
            request_id = chosen.get("request_id") or ""
            if request_id == "-":
                request_id = ""

            pending.append({
                "query": query,
                "answer": answer,
                "role": role,
                "route": _infer_route(tr["has_data"], tr["has_rag"]),
                "session_id": session_id,
                "request_id": request_id,
                "ts": ts,
                "reasoning": [e.to_dict() for e in entries],
            })
        except Exception as exc:
            msg = f"trace {tid}: {exc}"
            _log.warning("audit ingest: failed to process %s", msg)
            report.errors.append(msg)

    # ── Pass 3: batch-compute entity signatures, then store ──────────────────
    if pending and not dry_run:
        entities_list: list = [None] * len(pending)
        if Config.CACHE_ENTITY_GATING_ENABLED and entity_mode != "none":
            queries = [p["query"] for p in pending]
            if entity_mode == "regex":
                from src.cache.entity_extractor import extract_entities_regex, signature_to_str
                entities_list = [signature_to_str(extract_entities_regex(q)) for q in queries]
            else:  # "llm" — batched extraction (few calls, regex fallback per query)
                from src.cache.entity_extractor import extract_entities_batch_sync, signature_to_str
                sigs = extract_entities_batch_sync(queries)
                entities_list = [signature_to_str(s) for s in sigs]

        for p, entities in zip(pending, entities_list):
            try:
                store.store(
                    query=p["query"], answer=p["answer"], role=p["role"], route=p["route"],
                    session_id=p["session_id"], request_id=p["request_id"], ts=p["ts"],
                    reasoning=p["reasoning"], entities=entities,
                )
                report.newly_stored += 1
            except Exception as exc:
                msg = f"store {p['query'][:40]!r}: {exc}"
                _log.warning("audit ingest: %s", msg)
                report.errors.append(msg)

    report.elapsed_ms = (time.perf_counter() - t0) * 1000
    _log.info(
        "audit ingest: done scanned=%d present=%d stored=%d stale=%d empty=%d "
        "negative=%d role_invalid=%d errors=%d elapsed=%.0fms",
        report.total_scanned, report.already_present, report.newly_stored,
        report.skipped_stale, report.skipped_empty, report.skipped_negative,
        report.skipped_role_invalid, len(report.errors), report.elapsed_ms,
    )
    return report


async def run_ingest_audit(
    audit_file: str = "",
    dry_run: bool = False,
    overwrite: bool = False,
    role_filter: Optional[str] = None,
    max_age_hours: Optional[float] = None,
    entity_mode: str = "llm",
) -> IngestReport:
    """Async wrapper for the audit-trail ingest — delegates to a thread pool."""
    return await asyncio.to_thread(
        run_ingest_audit_sync, audit_file, dry_run, overwrite,
        role_filter, max_age_hours, entity_mode,
    )


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
    source: str = "conversations",
    source_dir: str = "",
    audit_file: str = "",
    dry_run: bool = False,
    overwrite: bool = False,
    role_filter: Optional[str] = None,
    entity_mode: str = "llm",
) -> None:
    """Run ingest as a background task and update the job record.

    ``source`` selects the adapter: "conversations" (default) or "audit".
    """
    job = _jobs.get(job_id)
    if job is None:
        return
    try:
        if source == "audit":
            report = await run_ingest_audit(
                audit_file=audit_file, dry_run=dry_run,
                overwrite=overwrite, role_filter=role_filter, entity_mode=entity_mode,
            )
        else:
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
    parser.add_argument("--source", choices=("conversations", "audit"), default="conversations",
                        help="Ingest source: 'conversations' (CONVERSATION_STORE_DIR) or 'audit' (audit_trail.jsonl)")
    parser.add_argument("--audit-file", default="", help="Path to audit_trail.jsonl (default: AUDIT_LOG_FILE); used with --source audit")
    parser.add_argument("--entity-mode", choices=("llm", "regex", "none"), default="llm",
                        help="How to compute entity signatures during ingest: 'llm' (batched, default), "
                             "'regex' (instant, no API — structured IDs only), or 'none'")
    parser.add_argument("--source-dir", default="", help="Path to JSONL directory (default: CONVERSATION_STORE_DIR)")
    parser.add_argument("--dry-run", action="store_true", help="Log what would be stored without writing to ChromaDB")
    parser.add_argument("--overwrite", action="store_true", help="Re-embed and overwrite existing entries")
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

    if args.source == "audit":
        report = run_ingest_audit_sync(
            audit_file=args.audit_file,
            dry_run=args.dry_run,
            overwrite=args.overwrite,
            role_filter=args.role or None,
            max_age_hours=args.max_age_hours,
            entity_mode=args.entity_mode,
        )
    else:
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
