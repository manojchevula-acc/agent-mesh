"""Collector gateway contract: destinations, PDPL processors and the AKS manifests stay consistent."""

from __future__ import annotations

from pathlib import Path

import yaml

COLLECTOR = Path(__file__).resolve().parent.parent / "collector"
CONFIG = yaml.safe_load((COLLECTOR / "otel-collector-config.yaml").read_text(encoding="utf-8"))
KUSTOMIZATION = yaml.safe_load((COLLECTOR / "kustomization.yaml").read_text(encoding="utf-8"))


def _manifests() -> dict[str, dict]:
    out = {}
    for rel in KUSTOMIZATION["resources"]:
        for doc in yaml.safe_load_all((COLLECTOR / rel).read_text(encoding="utf-8")):
            out[doc["kind"]] = doc
    return out


def test_traces_go_to_adx_and_opik_metrics_and_logs_to_adx():
    p = CONFIG["service"]["pipelines"]
    assert p["traces/dataplane"]["exporters"] == ["azuredataexplorer"]
    assert p["traces/opik"]["exporters"] == ["otlphttp/opik"]
    assert p["metrics/dataplane"]["exporters"] == ["azuredataexplorer"]
    assert p["logs/dataplane"]["exporters"] == ["azuredataexplorer"]
    assert {e.split("/")[0] for e in CONFIG["exporters"]} == {"azuredataexplorer", "otlphttp"}


def test_every_trace_pipeline_enforces_pdpl_before_export():
    for name in ("traces/dataplane", "traces/opik"):
        procs = CONFIG["service"]["pipelines"][name]["processors"]
        assert procs[0] == "memory_limiter" and procs[-1] == "batch"
        assert procs.index("attributes/pdpl") < procs.index("redaction/deny_by_default") < len(procs) - 1
    assert CONFIG["processors"]["redaction/deny_by_default"]["allow_all_keys"] is False
    assert "attributes/pdpl" in CONFIG["service"]["pipelines"]["logs/dataplane"]["processors"]


def test_no_endpoint_or_secret_is_hard_coded():
    adx = CONFIG["exporters"]["azuredataexplorer"]
    opik = CONFIG["exporters"]["otlphttp/opik"]
    assert adx["cluster_uri"] == "${env:ADX_CLUSTER_URI}" and adx["use_azure_auth"] is True
    assert not {"application_key", "application_id", "tenant_id"} & set(adx)
    assert all(v.startswith("${env:") for v in opik["headers"].values())
    assert CONFIG["receivers"]["otlp"]["protocols"]["grpc"]["endpoint"].startswith("${env:GERNAS_COLLECTOR_BIND")


def test_manifests_match_the_config():
    m = _manifests()
    assert set(m) == {"ServiceAccount", "Deployment", "Service", "PodDisruptionBudget", "NetworkPolicy"}
    gen = {g["name"]: g for g in KUSTOMIZATION["configMapGenerator"]}
    assert gen["gernas-otel-collector-config"]["files"] == ["otel-collector-config.yaml"]
    pod = m["Deployment"]["spec"]["template"]["spec"]
    c = pod["containers"][0]
    assert c["image"] == "otel/opentelemetry-collector-contrib:0.162.0"  # version the config is validated with
    assert c["args"] == ["--config=/conf/otel-collector-config.yaml"]
    ports = {p["name"]: p["containerPort"] for p in c["ports"]}
    assert ports == {"otlp-grpc": 4317, "otlp-http": 4318, "health": 13133, "metrics": 8888}
    assert CONFIG["extensions"]["health_check"]["endpoint"].endswith(":13133")
    assert {p["port"] for p in m["Service"]["spec"]["ports"]} == {4317, 4318, 8888}
    assert c["readinessProbe"]["httpGet"]["port"] == "health" and c["resources"]["limits"]["memory"]
    assert m["Deployment"]["spec"]["template"]["metadata"]["labels"]["azure.workload.identity/use"] == "true"
    assert pod["serviceAccountName"] == m["ServiceAccount"]["metadata"]["name"]
    refs = [r for r in c["envFrom"]]
    assert {"secretRef": {"name": "opik-credentials"}} in refs


def test_no_secret_manifest_is_committed_and_local_overlay_is_not_deployed():
    for path in COLLECTOR.rglob("*.yaml"):
        for doc in yaml.safe_load_all(path.read_text(encoding="utf-8")):
            assert not (isinstance(doc, dict) and doc.get("kind") == "Secret"), path
    assert "local-dataplane-file.yaml" not in str(KUSTOMIZATION)
    literals = dict(
        x.split("=", 1)
        for x in next(g for g in KUSTOMIZATION["configMapGenerator"] if g["name"] == "gernas-otel-collector-env")[
            "literals"
        ]
    )
    assert not {"OPIK_API_KEY", "AZURE_CLIENT_SECRET"} & set(literals)


def test_adx_schema_matches_exporter_tables():
    kql = (COLLECTOR / "adx" / "create-tables.kql").read_text(encoding="utf-8")
    adx = CONFIG["exporters"]["azuredataexplorer"]
    for table in (adx["traces_table_name"], adx["metrics_table_name"], adx["logs_table_name"]):
        assert f".create-merge table {table} (" in kql
