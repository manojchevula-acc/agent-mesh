"""TelemetryConfig — all settings loaded from environment variables.

Reads both beacon_kit-native env vars and the existing agent-mesh / rag-as-a-service
env vars so services need zero .env changes to switch to beacon_kit:

  OBS_PROFILE              → exporter_type  (grafana | grafana_and_file | file | dev | prod | off | console | pretty)
  GRAFANA_OTLP_ENDPOINT    → otel_endpoint  (Grafana Cloud OTLP/HTTP gateway)
  GRAFANA_INSTANCE_ID      → grafana_instance_id
  GRAFANA_API_TOKEN        → grafana_api_key
  OTEL_SERVICE_NAME        → service_name fallback
  OTEL_EXPORTER_OTLP_ENDPOINT → otel_endpoint fallback (dev / local OTLP)
  OTEL_FILE_OUTPUT_DIR     → file_output_dir  (root dir for local JSONL files)
  OTEL_FILE_SESSION_ID     → file_session_id  (optional label added to filenames)
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Literal


def _resolve_exporter_type() -> str:
    """Map OBS_PROFILE / EXPORTER_TYPE to beacon_kit exporter names."""
    raw = (
        os.getenv("EXPORTER_TYPE")
        or os.getenv("OBS_PROFILE")
        or "console"
    ).lower()
    _mapping = {
        "dev": "otel_collector",
        "prod": "azure_monitor",
        "grafana": "grafana",
        "grafana_and_file": "grafana_and_file",
        "file": "file",
        "pretty": "pretty",
        "console": "console",
        "otel_collector": "otel_collector",
        "azure_monitor": "azure_monitor",
        "off": "off",
    }
    return _mapping.get(raw, "console")


def _resolve_otel_endpoint() -> str:
    """Prefer GRAFANA_OTLP_ENDPOINT, fall back to OTEL_EXPORTER_OTLP_ENDPOINT."""
    return (
        os.getenv("GRAFANA_OTLP_ENDPOINT")
        or os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT")
        or "http://localhost:4317"
    )


def _resolve_service_name() -> str:
    return os.getenv("OTEL_SERVICE_NAME") or os.getenv("SERVICE_NAME") or "unnamed-service"


@dataclass
class TelemetryConfig:
    # Identity
    service_name: str = field(default_factory=_resolve_service_name)
    service_version: str = field(default_factory=lambda: os.getenv("SERVICE_VERSION", "0.0.0"))
    environment: str = field(default_factory=lambda: os.getenv("ENVIRONMENT", "development"))
    pod: str = field(default_factory=lambda: os.getenv("POD_NAME", ""))

    # Export target — switching OBS_PROFILE or EXPORTER_TYPE env var swaps backends
    exporter_type: Literal["console", "otel_collector", "azure_monitor", "grafana", "grafana_and_file", "file", "pretty", "off"] = field(
        default_factory=_resolve_exporter_type  # type: ignore[return-value]
    )

    # OTLP endpoint — supports both Grafana Cloud (HTTP) and local collector (gRPC)
    otel_endpoint: str = field(default_factory=_resolve_otel_endpoint)

    # Grafana Cloud Basic-auth credentials (used when exporter_type == "grafana")
    grafana_instance_id: str = field(
        default_factory=lambda: os.getenv("GRAFANA_INSTANCE_ID", "")
    )
    grafana_api_key: str = field(
        default_factory=lambda: os.getenv("GRAFANA_API_TOKEN") or os.getenv("GRAFANA_API_KEY", "")
    )

    # Azure Monitor (used when exporter_type == "azure_monitor")
    azure_connection_string: str = field(
        default_factory=lambda: os.getenv("APPLICATIONINSIGHTS_CONNECTION_STRING", "")
    )

    # Sampling (0.0–1.0); tail sampling enforced at the Collector, this is head-based
    sampling_rate: float = field(
        default_factory=lambda: float(os.getenv("OTEL_SAMPLING_RATE", "1.0"))
    )

    # Feature flags
    enable_compliance: bool = field(
        default_factory=lambda: os.getenv("ENABLE_COMPLIANCE", "false").lower() == "true"
    )
    enable_baggage_propagation: bool = field(
        default_factory=lambda: os.getenv("ENABLE_BAGGAGE_PROPAGATION", "true").lower() == "true"
    )

    # Compliance event store path (dev) or Event Hub connection string (prod)
    compliance_store: str = field(
        default_factory=lambda: os.getenv("COMPLIANCE_STORE", "compliance_events.jsonl")
    )

    # HMAC key for compliance event signing (hex string in dev, Key Vault ref in prod)
    compliance_hmac_key: str = field(
        default_factory=lambda: os.getenv("COMPLIANCE_HMAC_KEY", "dev-insecure-key-change-in-prod")
    )

    # Local file output — set OTEL_FILE_OUTPUT_DIR to enable JSONL export to disk.
    # Subdirectories traces/, metrics/, logs/ are created automatically.
    # When non-empty, a secondary file processor is added alongside the primary exporter.
    file_output_dir: str = field(
        default_factory=lambda: os.getenv("OTEL_FILE_OUTPUT_DIR", "")
    )
    # Optional label appended to filenames: YYYYMMDD_HHMMSS_{service_name}[_{session_id}].jsonl
    file_session_id: str = field(
        default_factory=lambda: os.getenv("OTEL_FILE_SESSION_ID", "")
    )

    def __post_init__(self) -> None:
        if (
            self.environment != "development"
            and self.enable_compliance
            and self.compliance_hmac_key == "dev-insecure-key-change-in-prod"
        ):
            raise ValueError(
                "COMPLIANCE_HMAC_KEY must be set to a secure value when running outside "
                "the development environment. Set the COMPLIANCE_HMAC_KEY environment variable."
            )

    def file_path(self, signal: str) -> str:
        """Return the absolute JSONL path for a given signal (traces/metrics/logs/compliance).

        Pattern: {file_output_dir}/{signal}/{YYYYMMDD}_{HHMMSS}_{service_name}[_{session_id}].jsonl
        The timestamp is fixed at the time of first call so all signals from one process
        share the same timestamp prefix.
        """
        if not hasattr(self, "_file_ts"):
            object.__setattr__(
                self, "_file_ts",
                datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S"),
            )
        safe_name = self.service_name.replace(" ", "_").replace("/", "_")
        suffix = f"_{self.file_session_id}" if self.file_session_id else ""
        filename = f"{self._file_ts}_{safe_name}{suffix}.jsonl"
        base = os.path.abspath(self.file_output_dir)
        return os.path.join(base, signal, filename)

    def report_path(self) -> str:
        """Return the output path for the HTML observability report.

        Pattern: {file_output_dir}/reports/{YYYYMMDD}_{HHMMSS}_{service_name}[_{session_id}].html
        Reuses the same cached timestamp as file_path() so the report filename
        matches the JSONL filenames produced by the same process run.
        """
        if not hasattr(self, "_file_ts"):
            object.__setattr__(
                self, "_file_ts",
                datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S"),
            )
        safe_name = self.service_name.replace(" ", "_").replace("/", "_")
        suffix = f"_{self.file_session_id}" if self.file_session_id else ""
        filename = f"{self._file_ts}_{safe_name}{suffix}.html"
        base = os.path.abspath(self.file_output_dir)
        return os.path.join(base, "reports", filename)

    @classmethod
    def from_env(cls, service_name: str | None = None) -> "TelemetryConfig":
        """Create config from environment. Optionally override service_name per-process."""
        cfg = cls()
        if service_name:
            cfg.service_name = service_name
        return cfg
