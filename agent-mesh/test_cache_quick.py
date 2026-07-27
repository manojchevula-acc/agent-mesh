"""Quick smoke test for the semantic cache — run before starting api_server.

Usage (from agent-mesh/):
    python test_cache_quick.py

Passes if you see:  ALL TESTS PASSED
Fails  if you see:  FAILED
"""
import os, sys, pathlib, shutil, time

# Point at a throw-away chroma dir so we don't touch production data
TEST_CHROMA_DIR = "data/cache/_test_chroma"
os.environ["ENABLE_RESPONSE_CACHE"] = "true"
os.environ["CACHE_CHROMA_DIR"]      = TEST_CHROMA_DIR
os.environ["CACHE_SIMILARITY_THRESHOLD"] = "0.90"
os.environ["CACHE_MAX_AGE_HOURS"]   = "24.0"
os.environ["CACHE_EMBED_MODEL"]     = "chromadb-default"
os.environ["CACHE_COLLECTION_NAME"] = "test_collection"

# Ensure project root is on path
sys.path.insert(0, str(pathlib.Path(__file__).parent))

# Clean slate
if pathlib.Path(TEST_CHROMA_DIR).exists():
    shutil.rmtree(TEST_CHROMA_DIR)

from src.cache.semantic_cache import SemanticCacheStore

def ok(msg):  print(f"  [PASS] {msg}")
def fail(msg): print(f"  [FAIL] {msg}"); sys.exit(1)

print("\n=== Semantic Cache Smoke Test ===\n")

store = SemanticCacheStore()

# ── Test 1: empty collection → lookup returns None ───────────────────────────
print("Test 1: lookup on empty collection → expect MISS")
result = store.lookup("Show customer profile for CUST001", "platform_administrator")
if result is None:
    ok("MISS returned correctly on empty collection")
else:
    fail(f"Expected None, got {result}")

# ── Test 2: store an entry ────────────────────────────────────────────────────
print("\nTest 2: store an entry")
try:
    store.store(
        query="Show customer profile for CUST001",
        answer="Customer: Al Noor Trading LLC, Segment: SME, Credit Score: 690",
        role="platform_administrator",
        route="Data Layer Service",
        session_id="test_session_001",
        request_id="TEST0001",
    )
    count = store._collection.count()
    ok(f"Entry stored. Collection now has {count} entry/entries.")
    if count != 1:
        fail(f"Expected 1 entry, got {count}")
except Exception as e:
    fail(f"store() raised: {e}")

# ── Test 3: identical query → HIT ────────────────────────────────────────────
print("\nTest 3: identical query → expect HIT")
result = store.lookup("Show customer profile for CUST001", "platform_administrator")
if result is None:
    fail("Expected a cache HIT but got None (MISS)")
else:
    ok(f"HIT  similarity={result.similarity:.4f}  age={result.age_hours:.4f}h")
    ok(f"     answer preview: {result.answer[:60]}...")

# ── Test 4: paraphrase → HIT (semantic match) ────────────────────────────────
print("\nTest 4: paraphrase → expect HIT")
result = store.lookup("Get the profile of customer CUST001", "platform_administrator")
if result is None:
    fail("Expected a HIT on paraphrase but got MISS — similarity too low or embed broken")
else:
    ok(f"HIT  similarity={result.similarity:.4f}")

# ── Test 5: different role → MISS (role isolation) ───────────────────────────
print("\nTest 5: same query, different role → expect MISS (role isolation)")
result = store.lookup("Show customer profile for CUST001", "credit_officer")
if result is None:
    ok("MISS for different role — role isolation works")
else:
    fail(f"Expected MISS for different role, got HIT sim={result.similarity:.4f}")

# ── Cleanup ───────────────────────────────────────────────────────────────────
shutil.rmtree(TEST_CHROMA_DIR, ignore_errors=True)

print("\n=== ALL TESTS PASSED ===\n")
