"""The examples in examples/ (referenced by docs/GERNAS_SDK_INSTRUMENTATION_GUIDE.md) run and emit
the documented spans. Each runs in a fresh process with the console exporter."""

from __future__ import annotations

import importlib.util
import json
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

from conftest import IDENTITY

EXAMPLES = Path(__file__).resolve().parent.parent / "examples"

CASES = {
    "custom_agent.py": (
        None,
        {
            "use_case.request",
            "agent.run",
            "agent.step",
            "agent.delegate",
            "llm.call",
            "tool.call",
            "retrieval.search",
            "guardrail.check",
            "evaluation.score",
        },
    ),
    "maf_agent.py": ("agent_framework", {"use_case.request", "agent.step", "agent.run", "llm.call", "tool.call"}),
    "langchain_agent.py": ("langchain_core", {"agent.run", "llm.call", "tool.call", "retrieval.search"}),
    "langgraph_agent.py": ("langgraph", {"agent.run", "agent.step", "llm.call", "guardrail.check"}),
}


def _span_names(stdout: str) -> set[str]:
    names = set()
    for m in re.finditer(r'^\{\n\s+"name": "([^"]+)",\n\s+"context"', stdout, re.M):
        names.add(m.group(1))
    return names


@pytest.mark.parametrize("example", sorted(CASES))
def test_example_runs_and_emits_expected_spans(example):
    needs, expected = CASES[example]
    if needs and importlib.util.find_spec(needs) is None:
        pytest.skip(f"{needs} not installed")
    env = {
        **IDENTITY,
        "GERNAS_EXPORTER": "console",
        "GERNAS_USER_TOKEN_KEY": "example-key",
        "ENABLE_INSTRUMENTATION": "false",
        "PYTHONPATH": str(EXAMPLES.parent),
    }
    full_env = {k: v for k, v in os.environ.items() if not k.startswith("GERNAS_")}
    full_env.update(env)
    res = subprocess.run(
        [sys.executable, str(EXAMPLES / example)],
        capture_output=True,
        text=True,
        env=full_env,
        timeout=300,
        encoding="utf-8",
    )
    assert res.returncode == 0, res.stderr[-3000:]
    names = _span_names(res.stdout)
    assert expected <= names, f"missing {expected - names}"
    # every printed span carries the business envelope (spot-check the first span)
    first = res.stdout[res.stdout.index("{\n") :]
    doc = json.JSONDecoder().raw_decode(first)[0]
    assert doc["attributes"]["gernas.use_case_id"] == "UC-1"
