"""Shared fixtures: one in-memory Gernas SDK for the whole session.

OpenTelemetry allows a single global TracerProvider per process, so the session
initialises the SDK once with in-memory span and metric readers; each test clears
them. Tests that need a fresh process (real OTLP export, uninitialised SDK,
missing optional dependencies) run in a subprocess via :func:`run_python`.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
from opentelemetry.sdk.metrics.export import InMemoryMetricReader
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

import gernas
from gernas.config import GernasConfig

ROOT = Path(__file__).resolve().parent.parent
EXPORTER = InMemorySpanExporter()
METRICS = InMemoryMetricReader()

IDENTITY = {
    "GERNAS_SERVICE_NAME": "svc",
    "GERNAS_USE_CASE_ID": "UC-1",
    "GERNAS_APPLICATION_ID": "app-1",
    "GERNAS_BUSINESS_DOMAIN": "retail_banking",
    "GERNAS_ENVIRONMENT": "dev",
}


@pytest.fixture(scope="session")
def rate_card(tmp_path_factory) -> Path:
    path = tmp_path_factory.mktemp("rates") / "rate_card.json"
    path.write_text(
        json.dumps(
            {
                "version": "2026-04-01",
                "currency": "AED",
                "rates": [
                    {"model": "gpt-4o", "provider": "azure_openai", "input_per_1k": 0.0092, "output_per_1k": 0.0368},
                    {"model": "claude-x", "provider": "*", "input_per_1k": 0.011, "output_per_1k": 0.055},
                ],
            }
        ),
        encoding="utf-8",
    )
    return path


@pytest.fixture(scope="session", autouse=True)
def sdk(rate_card):
    cfg = GernasConfig(
        service_name="gernas-tests",
        use_case_id="UC-TEST",
        application_id="gernas-tests-app",
        business_domain="testing",
        environment="dev",
        service_version="9.9.9",
        exporter="off",
        loop_threshold=3,
        max_steps=50,
        rate_card_path=str(rate_card),
        user_token_key="test-token-key",
    )
    handle = gernas.init_gernas(cfg, span_exporter=EXPORTER, metric_reader=METRICS)
    yield handle


@pytest.fixture()
def spans():
    """Clear exported spans; call the returned function to read finished spans (optionally by name)."""
    EXPORTER.clear()

    def finished(name: str | None = None):
        return [s for s in EXPORTER.get_finished_spans() if name is None or s.name == name]

    return finished


@pytest.fixture()
def metric_points():
    """Return a function giving ``{metric_name: [(attributes, value), ...]}`` recorded since the test began."""

    def snapshot() -> dict[str, list[tuple[dict, float]]]:
        out: dict[str, list[tuple[dict, float]]] = {}
        data = METRICS.get_metrics_data()
        for rm in data.resource_metrics if data else []:
            for sm in rm.scope_metrics:
                for m in sm.metrics:
                    for p in m.data.data_points:
                        value = getattr(p, "value", None)
                        if value is None:
                            value = getattr(p, "count", None)
                        out.setdefault(m.name, []).append((dict(p.attributes), value))
        return out

    before = snapshot()

    def delta() -> dict[str, list[tuple[dict, float]]]:
        now = snapshot()
        out = {}
        for name, points in now.items():
            old = {tuple(sorted(a.items())): v for a, v in before.get(name, [])}
            changed = [
                (a, v - old.get(tuple(sorted(a.items())), 0))
                for a, v in points
                if v != old.get(tuple(sorted(a.items())), 0)
            ]
            if changed:
                out[name] = changed
        return out

    return delta


def attrs(span) -> dict:
    return dict(span.attributes or {})


def run_python(code: str, env: dict | None = None, timeout: float = 120) -> subprocess.CompletedProcess[str]:
    full_env = {k: v for k, v in os.environ.items() if not k.startswith("GERNAS_")}
    full_env.update(env or {})
    full_env["PYTHONPATH"] = str(ROOT)
    return subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, env=full_env, timeout=timeout)
