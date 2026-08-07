"""Integration test suite for the MCP Hub agent."""
import asyncio
import sys

import httpx
import pytest

from agent import run_agent


# ---------------------------------------------------------------------------
# Guard: skip all tests when the Hub Server is not running
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def hub_server_running():
    """Skip every test in this module when the Hub Server is not reachable."""
    try:
        r = httpx.get("http://localhost:8090/health", timeout=5)
        if r.status_code != 200 or r.json().get("status") != "ok":
            pytest.skip(
                "Hub Server not running on port 8090 — start with: python hub_server.py"
            )
    except Exception:
        pytest.skip(
            "Hub Server not running on port 8090 — start with: python hub_server.py"
        )

TEST_CASES = [
    # (query, expected_keyword_in_result)
    ("What is the current weather in Tokyo?",          "Tokyo"),
    ("Get a 3-day forecast for London",                "London"),
    ("Calculate the factorial of 10",                  "628800"),   # matches 3,628,800 and 3628800
    ("What is sqrt(225)?",                             "15"),
    ("Convert 100 km to miles",                        "miles"),
    ("Find the mean of 10, 20, 30, 40, 50",           "30"),
    ("What is the capital of Japan?",                  "Tokyo"),
    ("What currency does Brazil use?",                  "BRL"),
    ("What timezone is Sydney in?",                    "UTC+10"),
]

async def run_tests():
    passed = 0
    failed = 0
    for query, expected in TEST_CASES:
        print(f"\nTEST: {query}")
        try:
            result = await run_agent(query)
            if expected.lower() in result.lower():
                print(f"  PASS -- found '{expected}' in result")
                passed += 1
            else:
                print(f"  FAIL -- expected '{expected}' not found")
                print(f"  Got: {result[:200]}")
                failed += 1
        except Exception as e:
            print(f"  ERROR -- {e}")
            failed += 1

    print(f"\n{'='*50}")
    print(f"Results: {passed} passed, {failed} failed out of {len(TEST_CASES)} tests")
    return failed == 0

if __name__ == "__main__":
    success = asyncio.run(run_tests())
    sys.exit(0 if success else 1)


# ---------------------------------------------------------------------------
# Hub Server endpoint tests (pytest)
# ---------------------------------------------------------------------------

class TestHubServer:
    def test_health(self):
        r = httpx.get("http://localhost:8090/health", timeout=5)
        assert r.status_code == 200
        data = r.json()
        assert data["status"] == "ok"
        assert "server_count" in data
        assert data["server_count"] > 0

    def test_list_servers(self):
        r = httpx.get("http://localhost:8090/servers", timeout=5)
        assert r.status_code == 200
        data = r.json()
        assert "servers" in data
        assert len(data["servers"]) > 0

    def test_discover_demo_query(self):
        r = httpx.post(
            "http://localhost:8090/discover",
            json={"intent": "Calculate the factorial of 5"},
            timeout=10,
        )
        assert r.status_code == 200
        data = r.json()
        assert "servers" in data
        assert len(data["servers"]) > 0
        assert "method" in data
        assert "reason" in data
        assert "hub_metadata" in data

    def test_discover_fab_customer_query(self):
        r = httpx.post(
            "http://localhost:8090/discover",
            json={"intent": "Show me the 360 profile for CUST001"},
            timeout=10,
        )
        assert r.status_code == 200
        data = r.json()
        assert any(s["id"] == "fab-customer-server" for s in data["servers"])
        assert data["method"] == "fast_path"

    def test_discover_fab_pricing_query(self):
        r = httpx.post(
            "http://localhost:8090/discover",
            json={"intent": "Which deals are non-compliant?"},
            timeout=10,
        )
        assert r.status_code == 200
        data = r.json()
        assert any(s["id"] == "fab-pricing-server" for s in data["servers"])

    def test_discover_multi_server_query(self):
        r = httpx.post(
            "http://localhost:8090/discover",
            json={"intent": "Give me a comprehensive analysis of CUST001"},
            timeout=10,
        )
        assert r.status_code == 200
        data = r.json()
        assert len(data["servers"]) > 1
        assert data["method"] == "multi_fast_path"

    def test_get_server_by_id(self):
        r = httpx.get("http://localhost:8090/servers/fab-customer-server", timeout=5)
        assert r.status_code == 200
        data = r.json()
        assert data["id"] == "fab-customer-server"

    def test_get_server_not_found(self):
        r = httpx.get("http://localhost:8090/servers/nonexistent", timeout=5)
        assert r.status_code == 404
