"""Tests for the audit_trail.jsonl ingest adapter (src/cache/ingest_pipeline).

Covers the parsing helpers and the full run_ingest_audit_sync() reconstruction:
  - trace grouping + last-PriceAssist-per-trace (retry de-dup)
  - role recovery from the "[User: x | Role: y]" prefix; invalid roles dropped
  - query unwrapping (role prefix + [Current question] block)
  - answer cleaning: <llm_reasoning> stripped, full PII (credit card) redacted
  - negative/error answers skipped
  - route inference (Data / RAG / Hybrid)
  - idempotent re-run

Uses a temp ChromaDB collection + real (deterministic) redact_pii/extract_reasoning.
Entity gating is disabled for the ingest test so no LLM/network call is made.
"""
import json
import os
import sys
import pathlib
import shutil
import tempfile
import unittest
from datetime import datetime, timezone

os.environ["ENABLE_RESPONSE_CACHE"] = "true"
os.environ["CACHE_SIMILARITY_THRESHOLD"] = "0.85"
os.environ["CACHE_MISS_THRESHOLD"] = "0.60"
os.environ["CACHE_MAX_AGE_HOURS"] = "99999.0"
os.environ["CACHE_COLLECTION_NAME"] = "test_audit_collection"
os.environ["GROQ_API_KEY"] = "test-key"
os.environ["LLM_BASE_URL"] = "https://api.cerebras.ai/v1"

project_root = str(pathlib.Path(__file__).resolve().parents[1])
if project_root not in sys.path:
    sys.path.insert(0, project_root)


def _now_iso(offset_sec: int = 0) -> str:
    from datetime import timedelta
    return (datetime.now(timezone.utc) + timedelta(seconds=offset_sec)).isoformat()


# ---------------------------------------------------------------------------
# Pure helper tests (no store, no network)
# ---------------------------------------------------------------------------

class TestAuditHelpers(unittest.TestCase):
    def test_extract_role_and_query_simple(self):
        from src.cache.ingest_pipeline import _extract_role_and_query
        role, query = _extract_role_and_query("[User: bob | Role: credit_officer]\nWhat is the margin for CUST001?")
        self.assertEqual(role, "credit_officer")
        self.assertEqual(query, "What is the margin for CUST001?")

    def test_extract_role_and_query_with_current_question_block(self):
        from src.cache.ingest_pipeline import _extract_role_and_query
        raw = ("[User: alice | Role: relationship_manager]\n"
               "[Conversation Summary]\nEarlier the user asked about pricing.\n\n"
               "[Current question]\n\nShow customer profile for CUST002")
        role, query = _extract_role_and_query(raw)
        self.assertEqual(role, "relationship_manager")
        self.assertEqual(query, "Show customer profile for CUST002")

    def test_extract_role_and_query_missing_prefix(self):
        from src.cache.ingest_pipeline import _extract_role_and_query
        role, query = _extract_role_and_query("just a bare query")
        self.assertEqual(role, "")
        self.assertEqual(query, "just a bare query")

    def test_is_negative_answer(self):
        from src.cache.ingest_pipeline import _is_negative_answer
        self.assertTrue(_is_negative_answer("No margin_analysis data found for CUST_004."))
        self.assertTrue(_is_negative_answer("I was unable to retrieve the required data."))
        self.assertFalse(_is_negative_answer("CUST001 credit score is 720."))

    def test_infer_route(self):
        from src.cache.ingest_pipeline import _infer_route
        self.assertEqual(_infer_route(True, False), "Data Layer Service")
        self.assertEqual(_infer_route(False, True), "RAG")
        self.assertEqual(_infer_route(True, True), "Hybrid")
        self.assertEqual(_infer_route(False, False), "unknown")


# ---------------------------------------------------------------------------
# Full run_ingest_audit_sync() over a fixture audit file
# ---------------------------------------------------------------------------

class TestAuditIngest(unittest.TestCase):
    _tmp_dir = ""
    _orig_chroma_dir = ""
    _orig_gating = None
    _audit_path = ""

    @classmethod
    def setUpClass(cls):
        cls._tmp_dir = tempfile.mkdtemp(prefix="mesh_test_audit_")
        from src.config import Config
        cls._orig_chroma_dir = Config.CACHE_CHROMA_DIR
        cls._orig_gating = Config.CACHE_ENTITY_GATING_ENABLED
        Config.CACHE_CHROMA_DIR = cls._tmp_dir
        Config.CACHE_ENTITY_GATING_ENABLED = False  # avoid LLM/network during ingest

        # Reset the module singleton so it picks up the temp chroma dir.
        import src.cache.semantic_cache as sc
        sc._store_singleton = None

        cls._audit_path = os.path.join(cls._tmp_dir, "audit_trail.jsonl")
        records = []

        # ── Trace T1: retry (two PriceAssist spans) + DataAgent, valid role,
        #    reasoning + a credit-card number to prove redaction. ──
        records.append({"timestamp": _now_iso(0), "trace_id": "T1", "span_id": "s1",
                        "session_id": "default_session", "agent_name": "ComplianceAgent",
                        "inputs": ["Review this request for safety: 'x'"], "output": "COMPLIANCE_PASSED", "status": "SUCCESS"})
        records.append({"timestamp": _now_iso(1), "trace_id": "T1", "span_id": "s2",
                        "session_id": "default_session", "agent_name": "DataAgent",
                        "inputs": ["margin for CUST001"], "output": "rows", "status": "SUCCESS"})
        # earlier (retry) PriceAssist span — should NOT be chosen
        records.append({"timestamp": _now_iso(2), "trace_id": "T1", "span_id": "s3",
                        "session_id": "default_session", "agent_name": "PriceAssistAgent",
                        "inputs": ["[User: bob | Role: credit_officer]\nWhat is the margin for CUST001?"],
                        "output": "<llm_reasoning>{\"phase\":\"retry\"}</llm_reasoning>partial", "status": "SUCCESS"})
        # later PriceAssist span — the final answer
        records.append({"timestamp": _now_iso(3), "trace_id": "T1", "span_id": "s4",
                        "session_id": "default_session", "agent_name": "PriceAssistAgent",
                        "inputs": ["[User: bob | Role: credit_officer]\nWhat is the margin for CUST001?"],
                        "output": ("<llm_reasoning>{\"phase\":\"synthesis\"}</llm_reasoning>\n"
                                   "CUST001 margin is 3.2%. Card 4111 1111 1111 1111 on file."),
                        "status": "SUCCESS"})

        # ── Trace T2: invalid role 'banker' → dropped ──
        records.append({"timestamp": _now_iso(4), "trace_id": "T2", "span_id": "s5",
                        "session_id": "default_session", "agent_name": "PriceAssistAgent",
                        "inputs": ["[User: joe | Role: banker]\nSome question"],
                        "output": "some answer", "status": "SUCCESS"})

        # ── Trace T3: negative answer → skipped ──
        records.append({"timestamp": _now_iso(5), "trace_id": "T3", "span_id": "s6",
                        "session_id": "default_session", "agent_name": "PriceAssistAgent",
                        "inputs": ["[User: bob | Role: credit_officer]\nmargin for CUST_004?"],
                        "output": "No margin_analysis data found for CUST_004.", "status": "SUCCESS"})

        # ── Trace T4: RAGAgent present → route RAG, valid role ──
        records.append({"timestamp": _now_iso(6), "trace_id": "T4", "span_id": "s7",
                        "session_id": "sess_x", "agent_name": "RAGAgent",
                        "inputs": ["basel iii"], "output": "docs", "status": "SUCCESS"})
        records.append({"timestamp": _now_iso(7), "trace_id": "T4", "span_id": "s8",
                        "session_id": "sess_x", "agent_name": "PriceAssistAgent",
                        "inputs": ["[User: alice | Role: relationship_manager]\nWhat are Basel III Tier 1 limits?"],
                        "output": "Tier 1 corporate limit is 14%.", "status": "SUCCESS"})

        with open(cls._audit_path, "w", encoding="utf-8") as f:
            for r in records:
                f.write(json.dumps(r) + "\n")

    @classmethod
    def tearDownClass(cls):
        from src.config import Config
        Config.CACHE_CHROMA_DIR = cls._orig_chroma_dir
        Config.CACHE_ENTITY_GATING_ENABLED = cls._orig_gating
        import src.cache.semantic_cache as sc
        sc._store_singleton = None
        shutil.rmtree(cls._tmp_dir, ignore_errors=True)

    def test_a01_report_counts(self):
        from src.cache.ingest_pipeline import run_ingest_audit_sync
        report = run_ingest_audit_sync(audit_file=self._audit_path)
        # T1 + T4 stored; T2 invalid role; T3 negative.
        self.assertEqual(report.newly_stored, 2, report.as_dict())
        self.assertEqual(report.skipped_role_invalid, 1, report.as_dict())
        self.assertEqual(report.skipped_negative, 1, report.as_dict())

    def test_a02_answer_is_clean_and_redacted(self):
        from src.cache.semantic_cache import get_cache_store
        store = get_cache_store()
        entry = store.lookup("What is the margin for CUST001?", "credit_officer")
        self.assertIsNotNone(entry)
        # reasoning stripped
        self.assertNotIn("<llm_reasoning>", entry.answer)
        # credit card redacted (audit middleware would NOT have done this)
        self.assertNotIn("4111 1111 1111 1111", entry.answer)
        self.assertIn("[REDACTED_CREDIT_CARD]", entry.answer)
        # chose the LATER PriceAssist span (final answer, not the 'partial' retry)
        self.assertIn("3.2%", entry.answer)
        self.assertNotIn("partial", entry.answer)

    def test_a03_route_inference(self):
        from src.cache.semantic_cache import get_cache_store
        store = get_cache_store()
        rag_entry = store.lookup("What are Basel III Tier 1 limits?", "relationship_manager")
        self.assertIsNotNone(rag_entry)
        self.assertEqual(rag_entry.route, "RAG")
        data_entry = store.lookup("What is the margin for CUST001?", "credit_officer")
        self.assertEqual(data_entry.route, "Data Layer Service")

    def test_a04_idempotent_rerun(self):
        from src.cache.ingest_pipeline import run_ingest_audit_sync
        report = run_ingest_audit_sync(audit_file=self._audit_path)
        self.assertEqual(report.newly_stored, 0, report.as_dict())
        self.assertGreaterEqual(report.already_present, 2, report.as_dict())


if __name__ == "__main__":
    unittest.main(verbosity=2)
