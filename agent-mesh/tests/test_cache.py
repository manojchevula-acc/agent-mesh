"""Semantic cache tests — SemanticCacheStore store/lookup/role-isolation/age-expiry.

Uses ChromaDB DefaultEmbeddingFunction (all-MiniLM-L6-v2 via onnxruntime).
First run downloads ~80 MB model to ~/.cache/chroma — subsequent runs are fast.

Run:
    python -m pytest tests/test_cache.py -v
    python -m unittest tests.test_cache -v
"""
import os
import sys
import pathlib
import shutil
import tempfile
import unittest
from datetime import datetime, timezone, timedelta

# Env vars MUST be set before any project import
os.environ["ENABLE_RESPONSE_CACHE"] = "true"
os.environ["CACHE_SIMILARITY_THRESHOLD"] = "0.85"
os.environ["CACHE_MAX_AGE_HOURS"] = "24.0"
os.environ["CACHE_EMBED_MODEL"] = "chromadb-default"
os.environ["CACHE_COLLECTION_NAME"] = "test_cache_collection"

project_root = str(pathlib.Path(__file__).resolve().parents[1])
if project_root not in sys.path:
    sys.path.insert(0, project_root)


class TestSemanticCacheStore(unittest.TestCase):
    _tmp_dir: str = ""

    @classmethod
    def setUpClass(cls):
        cls._tmp_dir = tempfile.mkdtemp(prefix="mesh_test_chroma_")
        os.environ["CACHE_CHROMA_DIR"] = cls._tmp_dir
        from src.cache.semantic_cache import SemanticCacheStore
        cls.store = SemanticCacheStore()

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls._tmp_dir, ignore_errors=True)

    def test_01_miss_on_empty_collection(self):
        result = self.store.lookup("Show customer profile for CUST001", "platform_administrator")
        self.assertIsNone(result, "Empty collection should return MISS (None)")

    def test_02_store_writes_entry(self):
        self.store.store(
            query="Show customer profile for CUST001",
            answer="Customer: Al Noor Trading LLC, Segment: SME, Credit Score: 690",
            role="platform_administrator",
            route="Data Layer Service",
            session_id="test_session_001",
            request_id="TEST0001",
        )
        count = self.store._collection.count()
        self.assertEqual(count, 1, f"Expected 1 entry after store, got {count}")

    def test_03_identical_query_hit(self):
        result = self.store.lookup("Show customer profile for CUST001", "platform_administrator")
        self.assertIsNotNone(result, "Identical query should return a HIT")
        self.assertGreaterEqual(result.similarity, 0.99,
                                f"Identical query similarity should be >=0.99, got {result.similarity:.4f}")
        self.assertIn("Al Noor", result.answer)

    def test_04_paraphrase_hit(self):
        result = self.store.lookup("Get me the profile of customer CUST001", "platform_administrator")
        self.assertIsNotNone(result, "Paraphrase should return a HIT above threshold 0.85")
        self.assertGreaterEqual(result.similarity, 0.85,
                                f"Paraphrase similarity too low: {result.similarity:.4f}")

    def test_05_role_isolation_miss(self):
        result = self.store.lookup("Show customer profile for CUST001", "credit_officer")
        self.assertIsNone(result, "Different role should return MISS — role isolation broken")

    def test_06_age_expiry_miss(self):
        from src.cache.semantic_cache import SemanticCacheStore
        old_dir = tempfile.mkdtemp(prefix="mesh_test_chroma_old_")
        try:
            os.environ["CACHE_CHROMA_DIR"] = old_dir
            os.environ["CACHE_MAX_AGE_HOURS"] = "1.0"
            store2 = SemanticCacheStore()
            old_ts = datetime.now(timezone.utc) - timedelta(hours=200)
            store2.store(
                query="What is the pricing floor for BB loans?",
                answer="The pricing floor is 5.25%.",
                role="relationship_manager",
                route="RAG",
                session_id="old_session",
                request_id="OLD001",
                ts=old_ts,
            )
            result = store2.lookup("What is the pricing floor for BB loans?", "relationship_manager")
            self.assertIsNone(result, "Entry older than max_age_hours should return MISS")
        finally:
            shutil.rmtree(old_dir, ignore_errors=True)
            os.environ["CACHE_CHROMA_DIR"] = self._tmp_dir
            os.environ["CACHE_MAX_AGE_HOURS"] = "24.0"

    def test_07_stats_count(self):
        stats = self.store.stats()
        self.assertTrue(stats["enabled"])
        self.assertGreaterEqual(stats["total_entries"], 1)
        self.assertIn("similarity_threshold", stats)
        self.assertIn("max_age_hours", stats)

    def test_08_doc_id_deterministic(self):
        from src.cache.semantic_cache import SemanticCacheStore
        id1 = SemanticCacheStore._doc_id("credit_officer", "show margin for CUST001")
        id2 = SemanticCacheStore._doc_id("credit_officer", "show margin for CUST001")
        id3 = SemanticCacheStore._doc_id("platform_administrator", "show margin for CUST001")
        self.assertEqual(id1, id2, "_doc_id must be deterministic for same inputs")
        self.assertNotEqual(id1, id3, "_doc_id must differ when role differs")

    def test_09_upsert_idempotent(self):
        before = self.store._collection.count()
        self.store.store(
            query="Show customer profile for CUST001",
            answer="Customer: Al Noor Trading LLC, Segment: SME, Credit Score: 690",
            role="platform_administrator",
            route="Data Layer Service",
            session_id="test_session_001",
            request_id="TEST0001",
        )
        after = self.store._collection.count()
        self.assertEqual(before, after,
                         "Re-upserting same entry should not increase collection count")


if __name__ == "__main__":
    unittest.main(verbosity=2)
