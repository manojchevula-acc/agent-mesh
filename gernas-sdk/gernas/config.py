"""GernasConfig — every SDK setting comes from ``GERNAS_*`` environment variables.

No endpoint, key, model name or deployment id is hard-coded anywhere in the SDK
(engineering principle 7). The only required variable is ``GERNAS_OTLP_ENDPOINT``
when the default ``otlp`` exporter is used; telemetry always goes to the
Collector, never directly to a backend (principle 5).

Local developer sinks (``console``, ``pretty``, ``file``, ``memory``, ``off``) can be
selected with ``GERNAS_EXPORTER`` — they are sinks for a developer's terminal or
disk, not backends, and need no endpoint.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Mapping

from opentelemetry.sdk.resources import Resource, SERVICE_NAME, SERVICE_VERSION

from gernas._version import __version__
from gernas.attributes import Ctx

EXPORTERS = ("otlp", "console", "pretty", "file", "memory", "off")
TRANSPORTS = ("grpc", "http")

_TRUE = {"1", "true", "yes", "on"}


def _flag(env: Mapping[str, str], name: str, default: bool) -> bool:
    raw = env.get(name)
    return default if raw is None or raw == "" else raw.strip().lower() in _TRUE


def _num(env: Mapping[str, str], name: str, default: float, cast=float):
    raw = env.get(name)
    if raw is None or raw.strip() == "":
        return default
    try:
        return cast(raw)
    except ValueError as exc:
        raise ValueError(f"{name}={raw!r} is not a valid {cast.__name__}") from exc


def _csv(env: Mapping[str, str], name: str, default: tuple[str, ...]) -> tuple[str, ...]:
    raw = env.get(name)
    if raw is None or raw.strip() == "":
        return default
    return tuple(p.strip() for p in raw.split(",") if p.strip())


# Baggage keys the BaggageSpanProcessor may copy onto spans. User identity is
# deliberately absent (PDPL); add keys with GERNAS_BAGGAGE_ATTRIBUTES.
DEFAULT_BAGGAGE_ATTRIBUTES = (Ctx.SESSION_ID, Ctx.REQUEST_ID, Ctx.USER_ROLE, Ctx.PARENT_RUN_ID,
                              Ctx.CHANNEL, Ctx.TASK_ID)


@dataclass
class GernasConfig:
    # Export (SDK -> Collector)
    otlp_endpoint: str = ""
    exporter: str = "otlp"
    otlp_transport: str = "grpc"
    otlp_api_key: str = ""
    otlp_timeout_s: float = 10.0
    # Identity / canonical envelope
    service_name: str = "gernas-service"
    service_version: str = "0.0.0"
    agent_version: str = ""
    use_case_id: str = "unassigned"
    application_id: str = ""
    business_domain: str = "unassigned"
    environment: str = "dev"
    # Join keys / infrastructure (WP1-CTX-04, T3)
    provenance_id: str = ""
    work_unit_id: str = ""
    commit_sha: str = ""
    cluster: str = ""
    namespace: str = ""
    pod: str = ""
    region: str = ""
    # Sampling and destination kill switches (WP1-CORE-06)
    sampling_rate: float = 1.0
    emit_opik: bool = True
    emit_dataplane: bool = True
    # Behavioural detection (WP1-CORE-03)
    loop_threshold: int = 3
    max_steps: int = 50
    # Model calls: default provider label + cost (WP1-CORE-02a step 4)
    llm_provider: str = ""
    rate_card_path: str = ""
    # PDPL — redacted payloads are only emitted under an explicit capture grant
    capture_payloads: bool = False
    baggage_attributes: tuple[str, ...] = DEFAULT_BAGGAGE_ATTRIBUTES
    # Local developer sinks
    file_output_dir: str = ""
    file_session_id: str = ""
    metric_interval_ms: int = 30_000
    log_namespaces: tuple[str, ...] = ("gernas",)
    # Compliance (Layer 3 audit chain, carried over from Beacon Kit)
    enable_compliance: bool = False
    compliance_store: str = "compliance_events.jsonl"
    compliance_hmac_key: str = "dev-insecure-key-change-in-prod"
    role_baggage_keys: tuple[str, ...] = (Ctx.USER_ROLE,)

    def __post_init__(self) -> None:
        if self.exporter not in EXPORTERS:
            raise ValueError(f"GERNAS_EXPORTER={self.exporter!r} must be one of {EXPORTERS}")
        if self.otlp_transport not in TRANSPORTS:
            raise ValueError(f"GERNAS_OTLP_TRANSPORT={self.otlp_transport!r} must be one of {TRANSPORTS}")
        if not 0.0 <= self.sampling_rate <= 1.0:
            raise ValueError("GERNAS_SAMPLING_RATE must be between 0.0 and 1.0")
        if not self.application_id:
            self.application_id = self.service_name
        if not self.agent_version:
            self.agent_version = self.service_version
        if (self.environment not in ("dev", "development") and self.enable_compliance
                and self.compliance_hmac_key == "dev-insecure-key-change-in-prod"):
            raise ValueError("GERNAS_COMPLIANCE_HMAC_KEY must be set to a secure value outside dev.")

    # ── construction ─────────────────────────────────────────────────────────
    @classmethod
    def from_env(cls, service_name: str | None = None, *, environ: Mapping[str, str] | None = None,
                 require_endpoint: bool = True) -> "GernasConfig":
        """Build the config from ``GERNAS_*`` environment variables.

        Raises ``KeyError`` naming ``GERNAS_OTLP_ENDPOINT`` when the ``otlp`` exporter
        is selected and no endpoint is set.
        """
        env = os.environ if environ is None else environ
        exporter = (env.get("GERNAS_EXPORTER") or "otlp").strip().lower()
        endpoint = (env.get("GERNAS_OTLP_ENDPOINT") or "").strip()
        if exporter == "otlp" and not endpoint and require_endpoint:
            raise KeyError(
                "GERNAS_OTLP_ENDPOINT is required: set it to the OTel Collector endpoint "
                "(e.g. http://localhost:4317), or select a local sink with "
                "GERNAS_EXPORTER=console|pretty|file|memory|off."
            )
        svc = service_name or env.get("GERNAS_SERVICE_NAME") or "gernas-service"
        return cls(
            otlp_endpoint=endpoint,
            exporter=exporter,
            otlp_transport=(env.get("GERNAS_OTLP_TRANSPORT") or "grpc").strip().lower(),
            otlp_api_key=env.get("GERNAS_OTLP_API_KEY", ""),
            otlp_timeout_s=_num(env, "GERNAS_OTLP_TIMEOUT_S", 10.0),
            service_name=svc,
            service_version=env.get("GERNAS_SERVICE_VERSION", "0.0.0"),
            agent_version=env.get("GERNAS_AGENT_VERSION", ""),
            use_case_id=env.get("GERNAS_USE_CASE_ID", "unassigned"),
            application_id=env.get("GERNAS_APPLICATION_ID", ""),
            business_domain=env.get("GERNAS_BUSINESS_DOMAIN", "unassigned"),
            environment=env.get("GERNAS_ENVIRONMENT", "dev"),
            provenance_id=env.get("GERNAS_PROVENANCE_ID", ""),
            work_unit_id=env.get("GERNAS_WORK_UNIT_ID", ""),
            commit_sha=env.get("GERNAS_COMMIT_SHA", ""),
            cluster=env.get("GERNAS_CLUSTER", ""),
            namespace=env.get("GERNAS_NAMESPACE", ""),
            pod=env.get("GERNAS_POD", ""),
            region=env.get("GERNAS_REGION", ""),
            sampling_rate=_num(env, "GERNAS_SAMPLING_RATE", 1.0),
            emit_opik=_flag(env, "GERNAS_EMIT_OPIK", True),
            emit_dataplane=_flag(env, "GERNAS_EMIT_DATAPLANE", True),
            loop_threshold=_num(env, "GERNAS_LOOP_THRESHOLD", 3, int),
            max_steps=_num(env, "GERNAS_MAX_STEPS", 50, int),
            llm_provider=env.get("GERNAS_LLM_PROVIDER", ""),
            rate_card_path=env.get("GERNAS_RATE_CARD_PATH", ""),
            capture_payloads=_flag(env, "GERNAS_CAPTURE_PAYLOADS", False),
            baggage_attributes=_csv(env, "GERNAS_BAGGAGE_ATTRIBUTES", DEFAULT_BAGGAGE_ATTRIBUTES),
            file_output_dir=env.get("GERNAS_FILE_OUTPUT_DIR", ""),
            file_session_id=env.get("GERNAS_FILE_SESSION_ID", ""),
            metric_interval_ms=_num(env, "GERNAS_METRIC_INTERVAL_MS", 30_000, int),
            log_namespaces=_csv(env, "GERNAS_LOG_NAMESPACES", ("gernas",)),
            enable_compliance=_flag(env, "GERNAS_ENABLE_COMPLIANCE", False),
            compliance_store=env.get("GERNAS_COMPLIANCE_STORE", "compliance_events.jsonl"),
            compliance_hmac_key=env.get("GERNAS_COMPLIANCE_HMAC_KEY", "dev-insecure-key-change-in-prod"),
            role_baggage_keys=_csv(env, "GERNAS_ROLE_BAGGAGE_KEYS", (Ctx.USER_ROLE,)),
        )

    # ── derived values ───────────────────────────────────────────────────────
    @property
    def envelope(self) -> dict[str, str]:
        """Canonical context envelope stamped first on every span."""
        return {
            Ctx.BUSINESS_DOMAIN: self.business_domain,
            Ctx.USE_CASE_ID: self.use_case_id,
            Ctx.APPLICATION_ID: self.application_id,
            Ctx.ENVIRONMENT: self.environment,
        }

    @property
    def resource(self) -> Resource:
        attrs: dict[str, object] = {
            SERVICE_NAME: self.service_name,
            SERVICE_VERSION: self.service_version,
            "deployment.environment.name": self.environment,
            Ctx.SDK_VERSION: __version__,
            Ctx.ROUTE_OPIK: self.emit_opik,
            Ctx.ROUTE_DATAPLANE: self.emit_dataplane,
            **self.envelope,
        }
        for key, value in ((Ctx.COMMIT_SHA, self.commit_sha), (Ctx.CLUSTER, self.cluster),
                           (Ctx.NAMESPACE, self.namespace), (Ctx.POD, self.pod), (Ctx.REGION, self.region)):
            if value:
                attrs[key] = value
        return Resource.create(attrs)

    def _stamp(self) -> str:
        if not hasattr(self, "_file_ts"):
            object.__setattr__(self, "_file_ts", datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S"))
        return self._file_ts  # type: ignore[attr-defined]

    def _artefact_path(self, folder: str, ext: str) -> str:
        safe = self.service_name.replace(" ", "_").replace("/", "_")
        suffix = f"_{self.file_session_id}" if self.file_session_id else ""
        base = os.path.abspath(self.file_output_dir or ".")
        return os.path.join(base, folder, f"{self._stamp()}_{safe}{suffix}.{ext}")

    def file_path(self, signal: str) -> str:
        """JSONL path for a signal: ``{dir}/{signal}/{ts}_{service}[_{session}].jsonl``."""
        return self._artefact_path(signal, "jsonl")

    def report_path(self) -> str:
        """HTML report path sharing the JSONL timestamp prefix."""
        return self._artefact_path("reports", "html")

    def audit_report_path(self, ext: str = "pdf") -> str:
        """Audit/regulatory report path sharing the JSONL timestamp prefix."""
        return self._artefact_path("audit_regulatory", ext)
