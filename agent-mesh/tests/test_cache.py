"""Semantic cache tests — SemanticCacheStore + LLM judge integration.

Covers:
  - Existing: store/lookup/role-isolation/age-expiry/stats/determinism/upsert
  - New:  three-zone lookup logic (definitive MISS / gray zone / definitive HIT),
          CacheEntry.confidence field, llm_cache_judge() with mocked HTTP,
          judge counters in stats(), CACHE_JUDGE_ENABLED=false fallback

Uses ChromaDB DefaultEmbeddingFunction (all-MiniLM-L6-v2 via onnxruntime).
First run downloads ~80 MB model to ~/.cache/chroma — subsequent runs are fast.

Run:
    python -m pytest tests/test_cache.py -v
    python -m unittest tests.test_cache -v
"""
import asyncio
import os
import sys
import pathlib
import shutil
import tempfile
import unittest
from datetime import datetime, timezone, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

# Env vars MUST be set before any project import so Config class-vars pick them up.
os.environ["ENABLE_RESPONSE_CACHE"] = "true"
os.environ["CACHE_SIMILARITY_THRESHOLD"] = "0.85"   # definitive HIT floor (test value)
os.environ["CACHE_MISS_THRESHOLD"] = "0.60"          # definitive MISS ceiling (test value)
os.environ["CACHE_MAX_AGE_HOURS"] = "24.0"
os.environ["CACHE_EMBED_MODEL"] = "chromadb-default"
os.environ["CACHE_COLLECTION_NAME"] = "test_cache_collection"
os.environ["CACHE_JUDGE_ENABLED"] = "true"
os.environ["CACHE_JUDGE_MODEL"] = "gemma-4-31b"
os.environ["GROQ_API_KEY"] = "test-key"
os.environ["LLM_BASE_URL"] = "https://api.cerebras.ai/v1"
os.environ["GROQ_MODEL"] = "gemma-4-31b"

project_root = str(pathlib.Path(__file__).resolve().parents[1])
if project_root not in sys.path:
    sys.path.insert(0, project_root)


def _run(coro):
    """Run an async coroutine in a test — creates a fresh loop to avoid deprecation warning."""
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


# ---------------------------------------------------------------------------
# Existing cache store tests (updated for new fields/zones)
# ---------------------------------------------------------------------------

class TestSemanticCacheStore(unittest.TestCase):
    _tmp_dir: str = ""
    _orig_chroma_dir: str = ""

    @classmethod
    def setUpClass(cls):
        cls._tmp_dir = tempfile.mkdtemp(prefix="mesh_test_chroma_")
        # Config class-vars are evaluated at import time, so os.environ alone is too late.
        # Patch the Config attribute directly so SemanticCacheStore.__init__ picks it up.
        from src.config import Config
        cls._orig_chroma_dir = Config.CACHE_CHROMA_DIR
        Config.CACHE_CHROMA_DIR = cls._tmp_dir
        from src.cache.semantic_cache import SemanticCacheStore
        cls.store = SemanticCacheStore()

    @classmethod
    def tearDownClass(cls):
        from src.config import Config
        Config.CACHE_CHROMA_DIR = cls._orig_chroma_dir
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

    def test_03_identical_query_hit_high_confidence(self):
        """Identical query must return confidence='high' (≥ CACHE_SIMILARITY_THRESHOLD=0.85)."""
        result = self.store.lookup("Show customer profile for CUST001", "platform_administrator")
        self.assertIsNotNone(result, "Identical query should return a HIT")
        self.assertGreaterEqual(result.similarity, 0.99,
                                f"Identical query similarity should be >=0.99, got {result.similarity:.4f}")
        self.assertEqual(result.confidence, "high",
                         f"Identical query should have confidence='high', got '{result.confidence}'")
        self.assertIn("Al Noor", result.answer)

    def test_04_paraphrase_hit(self):
        """Paraphrase above CACHE_MISS_THRESHOLD must return a CacheEntry (high or pending_judge).

        confidence='high' means similarity ≥ 0.85 (definitive hit, no judge needed).
        confidence='pending_judge' means 0.60 ≤ similarity < 0.85 (gray zone, judge will decide).
        Both are valid non-None returns from lookup() — the caller (CacheCheckExecutor) handles
        the judge step. What must NOT happen is returning None for a reasonable paraphrase.
        """
        result = self.store.lookup("Get me the profile of customer CUST001", "platform_administrator")
        self.assertIsNotNone(result, "Paraphrase should return a CacheEntry (not None) — similarity above miss_threshold 0.60")
        self.assertGreaterEqual(result.similarity, 0.60,
                                f"Paraphrase similarity below miss_threshold: {result.similarity:.4f}")
        self.assertIn(result.confidence, ("high", "pending_judge"),
                      f"Unexpected confidence value: '{result.confidence}'")

    def test_05_role_isolation_miss(self):
        """Different role must never receive another role's cached answer."""
        result = self.store.lookup("Show customer profile for CUST001", "credit_officer")
        self.assertIsNone(result, "Different role should return MISS — role isolation broken")

    def test_06_age_expiry_miss(self):
        """Entries older than CACHE_MAX_AGE_HOURS must return MISS."""
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

    def test_07_stats_includes_judge_fields(self):
        """stats() must include all new judge-related fields and counters."""
        stats = self.store.stats()
        self.assertTrue(stats["enabled"])
        self.assertGreaterEqual(stats["total_entries"], 1)
        self.assertIn("similarity_threshold", stats)
        self.assertIn("miss_threshold", stats)
        self.assertIn("judge_enabled", stats)
        self.assertIn("judge_model", stats)
        self.assertIn("judge_invocations", stats)
        self.assertIn("judge_hits", stats)
        self.assertIn("judge_misses", stats)
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


# ---------------------------------------------------------------------------
# Three-zone lookup logic tests
# ---------------------------------------------------------------------------

class TestThreeZoneLookup(unittest.TestCase):
    """Verify that lookup() returns the right confidence value for each zone.

    We force similarity into each zone by manipulating _threshold and
    Config.CACHE_MISS_THRESHOLD on a real store backed by an isolated temp
    ChromaDB collection. The embedding is real (onnxruntime) so similarity
    values are genuine cosine scores.
    """

    _tmp_dir: str = ""
    _orig_chroma_dir: str = ""

    @classmethod
    def setUpClass(cls):
        cls._tmp_dir = tempfile.mkdtemp(prefix="mesh_test_zones_")
        from src.config import Config
        cls._orig_chroma_dir = Config.CACHE_CHROMA_DIR
        Config.CACHE_CHROMA_DIR = cls._tmp_dir
        from src.cache.semantic_cache import SemanticCacheStore
        cls.store = SemanticCacheStore()
        # Populate with one known entry
        cls.store.store(
            query="What is the credit score of CUST002?",
            answer="CUST002 credit score: 720",
            role="credit_officer",
            route="Data Layer Service",
            session_id="zone_session",
            request_id="ZONE001",
        )

    @classmethod
    def tearDownClass(cls):
        from src.config import Config
        Config.CACHE_CHROMA_DIR = cls._orig_chroma_dir
        shutil.rmtree(cls._tmp_dir, ignore_errors=True)

    def _lookup_with_thresholds(self, query, hit_thresh, miss_thresh):
        """Temporarily set thresholds on the store and run lookup."""
        original_hit = self.store._threshold
        original_miss = getattr(self.store, '_miss_threshold', 0.75)
        from src.config import Config
        original_cfg_miss = Config.CACHE_MISS_THRESHOLD
        try:
            self.store._threshold = hit_thresh
            Config.CACHE_MISS_THRESHOLD = miss_thresh
            return self.store.lookup(query, "credit_officer")
        finally:
            self.store._threshold = original_hit
            Config.CACHE_MISS_THRESHOLD = original_cfg_miss

    def test_10_definitive_hit_zone(self):
        """similarity ≥ hit_threshold → confidence='high', no judge needed."""
        # Set threshold very low so the identical query is a definitive hit
        result = self._lookup_with_thresholds(
            "What is the credit score of CUST002?",
            hit_thresh=0.50,
            miss_thresh=0.30,
        )
        self.assertIsNotNone(result)
        self.assertEqual(result.confidence, "high",
                         f"Expected confidence='high', got '{result.confidence}'")

    def test_11_gray_zone_returns_pending_judge(self):
        """miss_threshold ≤ similarity < hit_threshold → confidence='pending_judge'."""
        # Identical query will have similarity ~1.0, so set hit_threshold > 1.0 (impossible)
        # and miss_threshold very low to force gray zone.
        result = self._lookup_with_thresholds(
            "What is the credit score of CUST002?",
            hit_thresh=1.01,   # nothing can reach this → always gray zone
            miss_thresh=0.01,  # nothing can be below this → never definitive MISS
        )
        self.assertIsNotNone(result, "Gray zone entry must be returned (not None)")
        self.assertEqual(result.confidence, "pending_judge",
                         f"Expected confidence='pending_judge', got '{result.confidence}'")

    def test_12_definitive_miss_zone(self):
        """similarity < miss_threshold → None returned (no judge, no entry)."""
        result = self._lookup_with_thresholds(
            "What is the credit score of CUST002?",
            hit_thresh=2.0,    # unreachable
            miss_thresh=2.0,   # everything is below this → definitive MISS
        )
        self.assertIsNone(result, "Below miss_threshold must return None")

    def test_13_confidence_field_present_on_hit(self):
        """CacheEntry returned from a definitive hit must always carry confidence='high'."""
        result = self.store.lookup("What is the credit score of CUST002?", "credit_officer")
        self.assertIsNotNone(result)
        self.assertTrue(hasattr(result, "confidence"),
                        "CacheEntry must have a 'confidence' field")
        self.assertIn(result.confidence, ("high", "pending_judge"),
                      f"confidence must be 'high' or 'pending_judge', got '{result.confidence}'")


# ---------------------------------------------------------------------------
# LLM judge unit tests (HTTP mocked)
# ---------------------------------------------------------------------------

class TestLlmCacheJudge(unittest.TestCase):
    """Tests for llm_cache_judge() — HTTP layer fully mocked."""

    def _make_response(self, content: str, status_code: int = 200):
        mock_resp = MagicMock()
        mock_resp.status_code = status_code
        mock_resp.json.return_value = {
            "choices": [{"message": {"content": content}}]
        }
        mock_resp.raise_for_status = MagicMock()
        if status_code >= 400:
            mock_resp.raise_for_status.side_effect = Exception(f"HTTP {status_code}")
        return mock_resp

    def _patch_httpx(self, response_content: str, status_code: int = 200):
        """Context manager that patches httpx.AsyncClient.post."""
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.post = AsyncMock(
            return_value=self._make_response(response_content, status_code)
        )
        return patch("src.cache.cache_judge.httpx.AsyncClient", return_value=mock_client)

    def test_14_judge_yes_returns_true_with_reason(self):
        """LLM returning 'YES: reason' must yield (True, reason)."""
        from src.cache.cache_judge import llm_cache_judge
        with self._patch_httpx("YES: same customer and intent, only wording differs"):
            decision, reason = _run(llm_cache_judge(
                new_query="List Alice's credit limit",
                cached_query="What is Alice's credit limit?",
                cached_answer="Alice's credit limit is AED 500,000.",
                role="relationship_manager",
            ))
        self.assertTrue(decision, "Judge returning YES should yield decision=True")
        self.assertIn("same customer", reason, f"Reason should be extracted, got: {reason!r}")

    def test_15_judge_no_returns_false_with_reason(self):
        """LLM returning 'NO: reason' must yield (False, reason)."""
        from src.cache.cache_judge import llm_cache_judge
        with self._patch_httpx("NO: asks about a different time period than cached answer"):
            decision, reason = _run(llm_cache_judge(
                new_query="Has Alice's credit limit changed recently?",
                cached_query="What is Alice's credit limit?",
                cached_answer="Alice's credit limit is AED 500,000.",
                role="relationship_manager",
            ))
        self.assertFalse(decision, "Judge returning NO should yield decision=False")
        self.assertIn("different time period", reason, f"Reason should be extracted, got: {reason!r}")

    def test_16_judge_yes_case_insensitive(self):
        """Decision parsing is case-insensitive — 'yes: ...' variants all work."""
        from src.cache.cache_judge import llm_cache_judge
        for variant in ("yes: minor rephrasing", "Yes: same scope", "YES: exact match", " YES: leading space"):
            with self._patch_httpx(variant):
                decision, reason = _run(llm_cache_judge(
                    new_query="q", cached_query="q", cached_answer="a", role="r"
                ))
            self.assertTrue(decision, f"Variant '{variant}' should be treated as YES")
            self.assertIsInstance(reason, str)

    def test_16b_judge_plain_yes_no_without_colon(self):
        """Plain 'YES' or 'NO' without a colon still parses correctly with empty reason."""
        from src.cache.cache_judge import llm_cache_judge
        with self._patch_httpx("YES"):
            decision, reason = _run(llm_cache_judge(
                new_query="q", cached_query="q", cached_answer="a", role="r"
            ))
        self.assertTrue(decision)
        self.assertEqual(reason, "")

        with self._patch_httpx("NO"):
            decision, reason = _run(llm_cache_judge(
                new_query="q", cached_query="q", cached_answer="a", role="r"
            ))
        self.assertFalse(decision)
        self.assertEqual(reason, "")

    def test_17_judge_http_error_degrades_to_false_empty_reason(self):
        """Any HTTP error must degrade gracefully to (False, ''), never raise."""
        from src.cache.cache_judge import llm_cache_judge
        with self._patch_httpx("", status_code=500):
            decision, reason = _run(llm_cache_judge(
                new_query="q", cached_query="q", cached_answer="a", role="r"
            ))
        self.assertFalse(decision, "HTTP error should degrade to False")
        self.assertEqual(reason, "", "Degraded reason must be empty string")

    def test_18_judge_timeout_degrades_to_false_empty_reason(self):
        """Network timeout must degrade gracefully to (False, ''), never raise."""
        from src.cache.cache_judge import llm_cache_judge
        import httpx
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.post = AsyncMock(side_effect=httpx.TimeoutException("timed out"))
        with patch("src.cache.cache_judge.httpx.AsyncClient", return_value=mock_client):
            decision, reason = _run(llm_cache_judge(
                new_query="q", cached_query="q", cached_answer="a", role="r"
            ))
        self.assertFalse(decision, "Timeout should degrade to False")
        self.assertEqual(reason, "")

    def test_19_judge_disabled_returns_false_empty_reason(self):
        """When CACHE_JUDGE_ENABLED=false, must return (False, '') without any HTTP call."""
        from src.config import Config
        original = Config.CACHE_JUDGE_ENABLED
        try:
            Config.CACHE_JUDGE_ENABLED = False
            from src.cache.cache_judge import llm_cache_judge
            with patch("src.cache.cache_judge.httpx.AsyncClient") as mock_cls:
                decision, reason = _run(llm_cache_judge(
                    new_query="q", cached_query="q", cached_answer="a", role="r"
                ))
                mock_cls.assert_not_called()
            self.assertFalse(decision, "Disabled judge must return False")
            self.assertEqual(reason, "")
        finally:
            Config.CACHE_JUDGE_ENABLED = original


# ---------------------------------------------------------------------------
# Judge counter tests
# ---------------------------------------------------------------------------

class TestParseJudgeResponse(unittest.TestCase):
    """Unit tests for the _parse_judge_response helper."""

    def _parse(self, raw):
        from src.cache.cache_judge import _parse_judge_response
        return _parse_judge_response(raw)

    def test_parse_yes_with_reason(self):
        decision, reason = self._parse("YES: same customer and intent, only wording differs")
        self.assertTrue(decision)
        self.assertEqual(reason, "same customer and intent, only wording differs")

    def test_parse_no_with_reason(self):
        decision, reason = self._parse("NO: asks about a different time period")
        self.assertFalse(decision)
        self.assertEqual(reason, "asks about a different time period")

    def test_parse_plain_yes(self):
        decision, reason = self._parse("YES")
        self.assertTrue(decision)
        self.assertEqual(reason, "")

    def test_parse_plain_no(self):
        decision, reason = self._parse("NO")
        self.assertFalse(decision)
        self.assertEqual(reason, "")

    def test_parse_lowercase(self):
        decision, reason = self._parse("yes: minor rephrasing only")
        self.assertTrue(decision)
        self.assertEqual(reason, "minor rephrasing only")

    def test_parse_reason_capped_at_120_chars(self):
        long_reason = "x" * 200
        decision, reason = self._parse(f"YES: {long_reason}")
        self.assertTrue(decision)
        self.assertLessEqual(len(reason), 120)


class TestJudgeCounters(unittest.TestCase):
    """Verify judge invocation counters are incremented correctly."""

    _tmp_dir: str = ""
    _orig_chroma_dir: str = ""

    @classmethod
    def setUpClass(cls):
        cls._tmp_dir = tempfile.mkdtemp(prefix="mesh_test_counters_")
        from src.config import Config
        cls._orig_chroma_dir = Config.CACHE_CHROMA_DIR
        Config.CACHE_CHROMA_DIR = cls._tmp_dir
        from src.cache.semantic_cache import SemanticCacheStore
        cls.store = SemanticCacheStore()

    @classmethod
    def tearDownClass(cls):
        from src.config import Config
        Config.CACHE_CHROMA_DIR = cls._orig_chroma_dir
        shutil.rmtree(cls._tmp_dir, ignore_errors=True)

    def test_20_counters_start_at_zero(self):
        self.assertEqual(self.store._judge_invocations, 0)
        self.assertEqual(self.store._judge_hits, 0)
        self.assertEqual(self.store._judge_misses, 0)

    def test_21_stats_reflects_zero_counters(self):
        stats = self.store.stats()
        self.assertEqual(stats["judge_invocations"], 0)
        self.assertEqual(stats["judge_hits"], 0)
        self.assertEqual(stats["judge_misses"], 0)

    def test_22_counters_increment_correctly(self):
        """Directly increment counters (as CacheCheckExecutor would) and verify stats."""
        self.store._judge_invocations += 3
        self.store._judge_hits += 2
        self.store._judge_misses += 1
        stats = self.store.stats()
        self.assertEqual(stats["judge_invocations"], 3)
        self.assertEqual(stats["judge_hits"], 2)
        self.assertEqual(stats["judge_misses"], 1)


# ---------------------------------------------------------------------------
# Package export test
# ---------------------------------------------------------------------------

class TestCachePackageExports(unittest.TestCase):
    def test_23_llm_cache_judge_exported(self):
        """llm_cache_judge must be importable from src.cache package."""
        from src.cache import llm_cache_judge
        self.assertTrue(callable(llm_cache_judge))

    def test_24_cache_entry_has_confidence_field(self):
        """CacheEntry dataclass must have a 'confidence' field defaulting to 'high'."""
        from src.cache import CacheEntry
        entry = CacheEntry(
            query_original="q",
            answer="a",
            role="r",
            route="Data Layer",
            session_id="s",
            request_id="rid",
            ts_iso="2026-01-01T00:00:00+00:00",
            similarity=0.95,
            age_hours=1.0,
            reasoning=[],
        )
        self.assertEqual(entry.confidence, "high",
                         "confidence field must default to 'high'")

    def test_25_cache_entry_confidence_pending_judge(self):
        """CacheEntry confidence can be set to 'pending_judge'."""
        from src.cache import CacheEntry
        entry = CacheEntry(
            query_original="q",
            answer="a",
            role="r",
            route="RAG",
            session_id="s",
            request_id="rid",
            ts_iso="2026-01-01T00:00:00+00:00",
            similarity=0.78,
            age_hours=0.5,
            reasoning=[],
            confidence="pending_judge",
        )
        self.assertEqual(entry.confidence, "pending_judge")


# ---------------------------------------------------------------------------
# Entity extractor + entity-aware gating tests
# ---------------------------------------------------------------------------

class TestEntityExtractor(unittest.TestCase):
    """Unit tests for src.cache.entity_extractor (HTTP mocked where needed)."""

    def setUp(self):
        # Clear the in-process memoization between tests so mocked responses apply.
        from src.cache import entity_extractor
        entity_extractor._signature_cache.clear()

    def test_e01_regex_extracts_customer_id(self):
        # Regex must use the SAME bucket label + lowercase value as the LLM path,
        # so regex-ingested entries match LLM lookups (plural "customer_ids", lowercase).
        from src.cache.entity_extractor import extract_entities_regex
        sig = extract_entities_regex("show customer profile for cust002")
        self.assertEqual(sig, frozenset({"customer_ids:cust002"}))

    def test_e02_regex_uppercases_and_handles_multiple(self):
        from src.cache.entity_extractor import extract_entities_regex
        sig = extract_entities_regex("compare CUST001 and acc50 for deal7")
        self.assertEqual(sig, frozenset({"customer_ids:cust001", "accounts:acc50", "deals:deal7"}))

    def test_e02b_regex_handles_underscore_id_form(self):
        """FAB data uses both CUST001 and CUST_007 — both must be captured, distinctly."""
        from src.cache.entity_extractor import extract_entities_regex, signatures_match
        self.assertEqual(extract_entities_regex("margin for CUST_007"),
                         frozenset({"customer_ids:cust_007"}))
        # CUST_007 vs CUST_008 must NOT match (the core gate guarantee).
        self.assertFalse(signatures_match(
            extract_entities_regex("margin for CUST_007"),
            extract_entities_regex("margin for CUST_008"),
        ))

    def test_e02c_regex_and_llm_signatures_are_aligned(self):
        """Regression: regex and LLM-parsed signatures must be identical for the same ID
        so a regex-ingested entry matches an LLM lookup (the entity-gate MISS bug)."""
        from src.cache.entity_extractor import extract_entities_regex, _parse_extractor_response
        regex_sig = extract_entities_regex("Show customer profile for CUST001")
        llm_sig = _parse_extractor_response('{"customer_ids": ["CUST001"]}')
        self.assertEqual(regex_sig, llm_sig)

    def test_e03_signatures_match_equality(self):
        from src.cache.entity_extractor import signatures_match
        self.assertTrue(signatures_match(frozenset({"customer_id:cust001"}),
                                         frozenset({"customer_id:cust001"})))
        self.assertFalse(signatures_match(frozenset({"customer_id:cust001"}),
                                          frozenset({"customer_id:cust002"})))
        # Both-empty (entity-free queries) must match so they still cache normally.
        self.assertTrue(signatures_match(frozenset(), frozenset()))
        # Superset is NOT equal.
        self.assertFalse(signatures_match(frozenset({"customer_id:cust001"}),
                                          frozenset({"customer_id:cust001", "time_scope:2025"})))

    def test_e04_signature_str_roundtrip(self):
        from src.cache.entity_extractor import signature_to_str, signature_from_str
        sig = frozenset({"customer_id:cust001", "time_scope:last quarter"})
        self.assertEqual(signature_from_str(signature_to_str(sig)), sig)
        self.assertEqual(signature_from_str(""), frozenset())
        self.assertEqual(signature_from_str(None), frozenset())

    def test_e05_parse_response_json(self):
        from src.cache.entity_extractor import _parse_extractor_response
        raw = '{"customer_ids": ["CUST002"], "accounts": [], "deals": [], "people": [], "products": [], "time_scope": "", "amounts": [], "other": []}'
        self.assertEqual(_parse_extractor_response(raw), frozenset({"customer_ids:cust002"}))

    def test_e06_parse_response_code_fence_and_prose(self):
        from src.cache.entity_extractor import _parse_extractor_response
        raw = 'Here you go:\n```json\n{"customer_ids": ["CUST001"], "time_scope": "last quarter"}\n```'
        self.assertEqual(
            _parse_extractor_response(raw),
            frozenset({"customer_ids:cust001", "time_scope:last quarter"}),
        )

    def test_e07_parse_response_garbage_returns_empty(self):
        from src.cache.entity_extractor import _parse_extractor_response, EMPTY_SIGNATURE
        self.assertEqual(_parse_extractor_response("not json at all"), EMPTY_SIGNATURE)

    def _patch_httpx_async(self, content: str, status_code: int = 200):
        mock_resp = MagicMock()
        mock_resp.status_code = status_code
        mock_resp.json.return_value = {"choices": [{"message": {"content": content}}]}
        mock_resp.raise_for_status = MagicMock()
        if status_code >= 400:
            mock_resp.raise_for_status.side_effect = Exception(f"HTTP {status_code}")
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.post = AsyncMock(return_value=mock_resp)
        return patch("src.cache.entity_extractor.httpx.AsyncClient", return_value=mock_client)

    def test_e08_extract_entities_async_uses_llm(self):
        from src.cache.entity_extractor import extract_entities
        with self._patch_httpx_async('{"customer_ids": ["CUST002"]}'):
            sig = _run(extract_entities("show profile for cust002"))
        self.assertEqual(sig, frozenset({"customer_ids:cust002"}))

    def test_e09_extract_entities_falls_back_to_regex_on_error(self):
        from src.cache.entity_extractor import extract_entities
        with self._patch_httpx_async("", status_code=500):
            sig = _run(extract_entities("show profile for cust002"))
        # LLM failed → regex fallback still catches the structured ID (canonical format).
        self.assertEqual(sig, frozenset({"customer_ids:cust002"}))

    def test_e10_cust001_vs_cust002_do_not_match(self):
        """The core bug: same intent, different entity must produce non-matching signatures."""
        from src.cache.entity_extractor import extract_entities_regex, signatures_match
        s1 = extract_entities_regex("show customer profile for cust001")
        s2 = extract_entities_regex("show customer profile for cust002")
        self.assertFalse(signatures_match(s1, s2),
                         "cust001 and cust002 must NOT match — gate would (correctly) drop the candidate")

    def test_e11_memoization(self):
        from src.cache.entity_extractor import extract_entities, _signature_cache
        with self._patch_httpx_async('{"customer_ids": ["CUST002"]}') as mock_cls:
            _run(extract_entities("show profile for cust002"))
            _run(extract_entities("show profile for cust002"))  # second call served from memo
        # AsyncClient constructed at most once (second lookup hit the memo cache).
        self.assertLessEqual(mock_cls.call_count, 1)


class TestEntityMetadataRoundtrip(unittest.TestCase):
    """store(entities=...) persists the signature; lookup returns it + entities_indexed."""

    _tmp_dir: str = ""
    _orig_chroma_dir: str = ""

    @classmethod
    def setUpClass(cls):
        cls._tmp_dir = tempfile.mkdtemp(prefix="mesh_test_entities_")
        from src.config import Config
        cls._orig_chroma_dir = Config.CACHE_CHROMA_DIR
        Config.CACHE_CHROMA_DIR = cls._tmp_dir
        from src.cache.semantic_cache import SemanticCacheStore
        cls.store = SemanticCacheStore()

    @classmethod
    def tearDownClass(cls):
        from src.config import Config
        Config.CACHE_CHROMA_DIR = cls._orig_chroma_dir
        shutil.rmtree(cls._tmp_dir, ignore_errors=True)

    def test_e12_store_persists_entities_and_lookup_returns_them(self):
        self.store.store(
            query="Show customer profile for CUST001",
            answer="Customer: Al Noor Trading LLC",
            role="platform_administrator",
            route="Data Layer Service",
            session_id="ent_session",
            request_id="ENT001",
            entities="customer_id:CUST001",
        )
        result = self.store.lookup("Show customer profile for CUST001", "platform_administrator")
        self.assertIsNotNone(result)
        self.assertEqual(result.entities, "customer_id:CUST001")
        self.assertTrue(result.entities_indexed, "entities_indexed must be True when the key was stored")

    def test_e13_entry_without_entities_key_is_not_indexed(self):
        self.store.store(
            query="What is the pricing floor for BB loans?",
            answer="5.25%",
            role="relationship_manager",
            route="RAG",
            session_id="ent_session2",
            request_id="ENT002",
            # entities omitted (None) → key not written
        )
        result = self.store.lookup("What is the pricing floor for BB loans?", "relationship_manager")
        self.assertIsNotNone(result)
        self.assertFalse(result.entities_indexed,
                         "entities_indexed must be False when no signature was provided")


if __name__ == "__main__":
    unittest.main(verbosity=2)
