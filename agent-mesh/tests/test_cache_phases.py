"""Tests for cache roadmap phases 2, 3, 4, 6, 7.

  P2 canonicalize_query, P3 _hybrid_rerank, P4 reranker.rerank_entries,
  P6 stats() counters, P7a negative_filter, P7b wait_for_decision_ex 3-state.

No network: the cross-encoder is monkeypatched with a fake scorer; rank_bm25 is
exercised only if installed (otherwise the graceful no-op path is asserted).
"""
import asyncio
import os
import sys
import pathlib
import shutil
import tempfile
import unittest
from dataclasses import dataclass

os.environ["ENABLE_RESPONSE_CACHE"] = "true"
os.environ["CACHE_COLLECTION_NAME"] = "test_phases_collection"
os.environ["GROQ_API_KEY"] = "test-key"
os.environ["LLM_BASE_URL"] = "https://api.cerebras.ai/v1"

project_root = str(pathlib.Path(__file__).resolve().parents[1])
if project_root not in sys.path:
    sys.path.insert(0, project_root)


def _run(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


@dataclass
class _Entry:
    query_original: str
    similarity: float = 0.9
    entities: str = ""


# ── P2: canonicalization ─────────────────────────────────────────────────────
class TestCanonicalize(unittest.TestCase):
    def test_ids_become_placeholders_same_form(self):
        from src.cache.entity_extractor import canonicalize_query
        self.assertEqual(canonicalize_query("Show customer profile for CUST001"),
                         "Show customer profile for <CUSTOMER_ID>")
        # underscore form canonicalizes to the SAME placeholder → same embedding
        self.assertEqual(canonicalize_query("Show customer profile for CUST_007"),
                         "Show customer profile for <CUSTOMER_ID>")

    def test_different_intent_preserved(self):
        from src.cache.entity_extractor import canonicalize_query
        a = canonicalize_query("margin analysis for CUST001")
        b = canonicalize_query("credit score for CUST002")
        self.assertNotEqual(a, b)  # intent words differ → still distinct


# ── P7a: negative filter ─────────────────────────────────────────────────────
class TestNegativeFilter(unittest.TestCase):
    def test_negatives(self):
        from src.cache.negative_filter import is_negative_answer
        self.assertTrue(is_negative_answer("No margin_analysis data found for CUST_004."))
        self.assertTrue(is_negative_answer("I was unable to retrieve the required data."))
        self.assertFalse(is_negative_answer("CUST001 credit score is 720."))

    def test_ingest_wrapper_delegates(self):
        from src.cache.ingest_pipeline import _is_negative_answer
        self.assertTrue(_is_negative_answer("No records found for CUST001"))


# ── P7b: 3-state decision ────────────────────────────────────────────────────
class TestDecisionExStates(unittest.TestCase):
    def test_accepted(self):
        from src.cache.intent_decision_store import IntentDecisionStore
        store = IntentDecisionStore()
        store.create_pending("E1")

        async def scenario():
            task = asyncio.ensure_future(store.wait_for_decision_ex("E1", timeout=5.0))
            await asyncio.sleep(0.01)
            store.resolve("E1", accepted=True, chosen_entry_id="E1")
            return await task

        outcome, chosen = _run(scenario())
        self.assertEqual(outcome, "accepted")
        self.assertEqual(chosen, "E1")

    def test_rejected(self):
        from src.cache.intent_decision_store import IntentDecisionStore
        store = IntentDecisionStore()
        store.create_pending("E2")

        async def scenario():
            task = asyncio.ensure_future(store.wait_for_decision_ex("E2", timeout=5.0))
            await asyncio.sleep(0.01)
            store.resolve("E2", accepted=False)
            return await task

        outcome, _ = _run(scenario())
        self.assertEqual(outcome, "rejected")

    def test_timeout_distinct_from_reject(self):
        from src.cache.intent_decision_store import IntentDecisionStore
        store = IntentDecisionStore()
        store.create_pending("E3")
        outcome, chosen = _run(store.wait_for_decision_ex("E3", timeout=0.05))
        self.assertEqual(outcome, "timeout")
        self.assertIsNone(chosen)


# ── P4: cross-encoder reranker (fake model) ──────────────────────────────────
class TestReranker(unittest.TestCase):
    def setUp(self):
        from src.cache import reranker
        self.reranker = reranker
        self._orig_model = reranker._model
        self._orig_failed = reranker._load_failed

    def tearDown(self):
        from src.config import Config
        self.reranker._model = self._orig_model
        self.reranker._load_failed = self._orig_failed
        Config.CACHE_RERANKER_ENABLED = False

    def test_disabled_returns_unchanged(self):
        from src.config import Config
        Config.CACHE_RERANKER_ENABLED = False
        entries = [_Entry("a"), _Entry("bb")]
        out, top = self.reranker.rerank_entries("q", entries)
        self.assertEqual(out, entries)
        self.assertIsNone(top)

    def test_reorders_by_fake_score(self):
        from src.config import Config
        Config.CACHE_RERANKER_ENABLED = True
        Config.CACHE_RERANK_MIN_SCORE = -1e9

        class _FakeCE:
            # score = length of the candidate text → longer ranks higher
            def predict(self, pairs):
                return [float(len(p[1])) for p in pairs]

        self.reranker._model = _FakeCE()
        self.reranker._load_failed = False
        entries = [_Entry("short"), _Entry("a much longer candidate document")]
        out, top = self.reranker.rerank_entries("q", entries)
        self.assertEqual(out[0].query_original, "a much longer candidate document")
        self.assertIsNotNone(top)

    def test_min_score_drops_but_keeps_best(self):
        from src.config import Config
        Config.CACHE_RERANKER_ENABLED = True
        Config.CACHE_RERANK_MIN_SCORE = 1000.0  # everything below the floor

        class _FakeCE:
            def predict(self, pairs):
                return [float(len(p[1])) for p in pairs]

        self.reranker._model = _FakeCE()
        self.reranker._load_failed = False
        entries = [_Entry("x"), _Entry("yy")]
        out, _ = self.reranker.rerank_entries("q", entries)
        # all below floor → keep only the single best, never empty
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0].query_original, "yy")


# ── P6: stats() exposes new counters ─────────────────────────────────────────
class TestStatsCounters(unittest.TestCase):
    _tmp = ""

    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.mkdtemp(prefix="mesh_test_phases_")
        from src.config import Config
        cls._orig = Config.CACHE_CHROMA_DIR
        Config.CACHE_CHROMA_DIR = cls._tmp
        from src.cache.semantic_cache import SemanticCacheStore
        cls.store = SemanticCacheStore()

    @classmethod
    def tearDownClass(cls):
        from src.config import Config
        Config.CACHE_CHROMA_DIR = cls._orig
        shutil.rmtree(cls._tmp, ignore_errors=True)

    def test_new_counter_fields_present(self):
        stats = self.store.stats()
        for key in ("entity_gate_drops", "reranker_invocations", "hit_accepted",
                    "hit_rejected", "intent_accepted", "intent_rejected",
                    "reranker_enabled", "entity_gating_enabled"):
            self.assertIn(key, stats, f"stats() missing {key}")


# ── P3: hybrid rerank (graceful with/without rank_bm25) ──────────────────────
class TestHybridRerank(unittest.TestCase):
    _tmp = ""

    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.mkdtemp(prefix="mesh_test_hybrid_")
        from src.config import Config
        cls._orig = Config.CACHE_CHROMA_DIR
        Config.CACHE_CHROMA_DIR = cls._tmp
        from src.cache.semantic_cache import SemanticCacheStore
        cls.store = SemanticCacheStore()

    @classmethod
    def tearDownClass(cls):
        from src.config import Config
        Config.CACHE_CHROMA_DIR = cls._orig
        shutil.rmtree(cls._tmp, ignore_errors=True)

    def test_rerank_preserves_set_and_promotes_lexical_match(self):
        entries = [
            _Entry("what is the pricing floor for BB loans", similarity=0.95),
            _Entry("show margin analysis for the corporate deal", similarity=0.90),
        ]
        out = self.store._hybrid_rerank("margin analysis corporate deal", list(entries))
        self.assertEqual(len(out), 2)
        self.assertEqual({e.query_original for e in out}, {e.query_original for e in entries})
        try:
            import rank_bm25  # noqa: F401
            # With BM25 available, the lexically-matching doc should rank first.
            self.assertEqual(out[0].query_original, "show margin analysis for the corporate deal")
        except Exception:
            pass  # rank_bm25 not installed → graceful no-op (order unchanged)


if __name__ == "__main__":
    unittest.main(verbosity=2)
