"""The core works without any framework installed; adapters fail with an actionable message."""

from __future__ import annotations

import json

from conftest import IDENTITY, run_python

_BLOCK = r"""
import sys, importlib.abc
BLOCKED = ("agent_framework", "langchain_core", "langchain", "langgraph", "a2a", "mcp", "fastmcp", "httpx",
           "starlette", "yaml")
class Blocker(importlib.abc.MetaPathFinder):
    def find_spec(self, name, path, target=None):
        if name.split(".")[0] in BLOCKED:
            raise ImportError(f"blocked: {name}")
sys.meta_path.insert(0, Blocker())
"""


def test_core_runs_with_every_optional_dependency_absent():
    code = (
        _BLOCK
        + r"""
import asyncio, json, gernas
gernas.init_gernas()

@gernas.trace_tool("t")
def tool(): return 1

@gernas.trace_agent("a", agent_type="worker")
async def run():
    with gernas.request_context(session_id="s"):
        return tool()

print(json.dumps({"result": asyncio.run(run()),
                  "loaded": sorted(m for m in sys.modules if m.split(".")[0] in BLOCKED)}))
"""
    )
    result = run_python(code, env={**IDENTITY, "GERNAS_EXPORTER": "off"})
    assert result.returncode == 0, result.stderr[-2000:]
    out = json.loads(result.stdout.strip().splitlines()[-1])
    assert out == {"result": 1, "loaded": []}


def test_adapters_raise_clear_import_errors_without_their_framework():
    code = (
        _BLOCK
        + r"""
import json
errors = {}
for mod in ("gernas.adapters.langchain", "gernas.adapters.langgraph", "gernas.adapters.agent_framework",
            "gernas.adapters.a2a"):
    try:
        __import__(mod)
        errors[mod] = None
    except ImportError as exc:
        errors[mod] = str(exc)
import gernas.adapters.asgi, gernas.adapters.httpx, gernas.adapters.mcp   # dependency-free adapters
print(json.dumps(errors))
"""
    )
    result = run_python(code)
    assert result.returncode == 0, result.stderr[-2000:]
    errors = json.loads(result.stdout.strip().splitlines()[-1])
    assert all(errors.values()), errors
    assert "pip install gernas-telemetry[langchain]" in errors["gernas.adapters.langchain"]
