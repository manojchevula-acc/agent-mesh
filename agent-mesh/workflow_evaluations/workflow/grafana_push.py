"""Shared Grafana Cloud OTLP metrics push helper.

Used by results_reporter.py, red_team_runner.py, and benchmark_report.py.
Reads GRAFANA_INSTANCE_ID and GRAFANA_API_TOKEN from the environment.
Fails silently — never raises or blocks the caller.
"""
from __future__ import annotations

import base64
import json
import os
import time
import urllib.request
import urllib.error
from typing import Dict, Optional

# Maps internal score keys to the metric names the Grafana dashboard queries.
# Any key not in this map falls back to fab_eval_<key>.
_METRIC_ALIASES: Dict[str, str] = {
    "pii_clean":           "fab_eval_pii_score",
    "rbac_scope":          "fab_eval_rbac_score",
    "compliance_decision": "fab_eval_compliance_score",
    "citation":            "fab_eval_citation_rate",
    "task_adherence":      "fab_eval_task_adherence_score",
    "intent_resolution":   "fab_eval_intent_resolution_score",
}


def push_metrics(
    metrics: Dict[str, float],
    run_ts: str = "",
    case_count: int = 0,
) -> None:
    """Push a flat dict of metric_name → float to Grafana Cloud via OTLP/HTTP.

    Metric names are passed through the alias map before sending so they match
    what the Grafana dashboard queries. Unknown keys are prefixed with `fab_eval_`.

    Args:
        metrics: {score_key: value} — e.g. {"pii_clean": 1.0, "fab_redteam_blocked_rate": 0.95}
        run_ts:  ISO-ish timestamp string for the `eval.run_ts` attribute label.
        case_count: number of cases in the run, attached as a label.
    """
    instance_id = os.getenv("GRAFANA_INSTANCE_ID", "")
    api_token = os.getenv("GRAFANA_API_TOKEN", "")
    if not instance_id or not api_token:
        return

    otlp_url = os.getenv("GRAFANA_OTLP_URL", "") or _detect_otlp_url(api_token)
    if not otlp_url:
        return

    # Resolve aliases
    resolved: Dict[str, float] = {}
    for key, val in metrics.items():
        resolved_name = _METRIC_ALIASES.get(key, f"fab_eval_{key.replace('.', '_')}")
        resolved[resolved_name] = val

    try:
        _do_push(resolved, run_ts, case_count, instance_id, api_token, otlp_url)
        print(f"Grafana metrics pushed ({len(resolved)} metrics) → {otlp_url}")
    except Exception as exc:
        print(f"[warn] Grafana metrics push failed (non-fatal): {exc}")


def _detect_otlp_url(api_token: str) -> str:
    """Extract the OTLP gateway URL from the Grafana token's embedded region.

    Grafana tokens are `glc_<base64-json>` where the JSON contains
    {"m": {"r": "<region>"}} e.g. "prod-ap-south-1".
    """
    try:
        b64_part = api_token.split("_", 1)[-1]
        b64_part += "=" * (-len(b64_part) % 4)
        payload = json.loads(base64.b64decode(b64_part).decode())
        region = payload.get("m", {}).get("r", "")
        if region:
            return f"https://otlp-gateway-{region}.grafana.net/otlp"
    except Exception:
        pass
    return ""


def _do_push(
    metrics: Dict[str, float],
    run_ts: str,
    case_count: int,
    instance_id: str,
    api_token: str,
    otlp_url: str,
) -> None:
    now_ns = str(int(time.time() * 1_000_000_000))
    attributes = [
        {"key": "eval.run_ts", "value": {"stringValue": run_ts or now_ns}},
        {"key": "eval.case_count", "value": {"intValue": case_count}},
    ]

    metrics_payload = [
        {
            "name": name,
            "description": f"FAB AgentMesh eval metric: {name}",
            "gauge": {
                "dataPoints": [{"asDouble": float(val), "timeUnixNano": now_ns, "attributes": attributes}]
            },
        }
        for name, val in metrics.items()
    ]

    payload = {
        "resourceMetrics": [
            {
                "resource": {
                    "attributes": [
                        {"key": "service.name", "value": {"stringValue": "fab-agentmesh-eval"}},
                        {"key": "service.version", "value": {"stringValue": "1.0.0"}},
                    ]
                },
                "scopeMetrics": [
                    {
                        "scope": {"name": "workflow_evaluations", "version": "1.0.0"},
                        "metrics": metrics_payload,
                    }
                ],
            }
        ]
    }

    auth = base64.b64encode(f"{instance_id}:{api_token}".encode()).decode()
    body = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        f"{otlp_url}/v1/metrics",
        data=body,
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Basic {auth}",
        },
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=10) as resp:
        if resp.status not in (200, 202):
            raise RuntimeError(f"HTTP {resp.status}")
