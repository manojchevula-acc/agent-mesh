"""Shared fixtures: one in-memory Gernas SDK for the whole session.

OpenTelemetry allows a single global TracerProvider per process, so the session
initialises the SDK once with an in-memory exporter; each test clears it. Tests
that must exercise real OTLP export (collector-down, cross-process propagation)
run in a subprocess.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

import gernas
from gernas.config import GernasConfig

ROOT = Path(__file__).resolve().parent.parent
EXPORTER = InMemorySpanExporter()


@pytest.fixture(scope="session")
def rate_card(tmp_path_factory) -> Path:
    path = tmp_path_factory.mktemp("rates") / "rate_card.json"
    path.write_text(json.dumps({
        "version": "2026-04-01", "currency": "AED",
        "rates": [{"model": "gpt-4o", "provider": "azure_openai", "input_per_1k": 0.0092, "output_per_1k": 0.0368},
                  {"model": "claude-x", "provider": "*", "input_per_1k": 0.011, "output_per_1k": 0.055}],
    }), encoding="utf-8")
    return path


@pytest.fixture(scope="session", autouse=True)
def sdk(rate_card, tmp_path_factory):
    cfg = GernasConfig(
        exporter="memory", service_name="gernas-tests", service_version="9.9.9", use_case_id="UC-TEST",
        application_id="gernas-tests-app", business_domain="testing", environment="dev",
        loop_threshold=3, max_steps=50, rate_card_path=str(rate_card), metric_interval_ms=60_000,
        compliance_store=str(tmp_path_factory.mktemp("compliance") / "events.jsonl"),
    )
    handle = gernas.init_gernas(cfg, span_exporter=EXPORTER)
    yield handle


@pytest.fixture()
def spans():
    """Clear exported spans; call the returned function to read finished spans."""
    EXPORTER.clear()

    def finished(name: str | None = None):
        out = EXPORTER.get_finished_spans()
        return [s for s in out if name is None or s.name == name]
    return finished


@pytest.fixture()
def cfg():
    return gernas.get_config()


def run_python(code: str, env: dict | None = None, timeout: float = 120) -> "subprocess.CompletedProcess[str]":
    import subprocess
    full_env = {k: v for k, v in os.environ.items() if not k.startswith("GERNAS_")}
    full_env.update(env or {})
    full_env["PYTHONPATH"] = str(ROOT)
    return subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, env=full_env, timeout=timeout)
