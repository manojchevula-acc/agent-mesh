"""GernasConfig — every SDK setting comes from ``GERNAS_*`` environment variables.

Mandatory identity (Blueprint §2 business + infrastructure context) is validated:
the SDK never invents a service, use case, application, business domain or
environment. Missing values raise :class:`GernasConfigError` naming every missing
variable. Telemetry is sent to the OTel Collector (Blueprint §1 "centralized
telemetry gateway/collectors"); ``console`` and ``off`` exist for development and
for switching telemetry off.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from opentelemetry.sdk.resources import SERVICE_NAME, SERVICE_VERSION, Resource

from gernas._version import __version__
from gernas.attributes import Ctx

EXPORTERS = ("otlp", "console", "off")
TRANSPORTS = ("grpc", "http")

_REQUIRED = {
    "service_name": "GERNAS_SERVICE_NAME",
    "use_case_id": "GERNAS_USE_CASE_ID",
    "application_id": "GERNAS_APPLICATION_ID",
    "business_domain": "GERNAS_BUSINESS_DOMAIN",
    "environment": "GERNAS_ENVIRONMENT",
}


class GernasConfigError(ValueError):
    """Mandatory configuration is missing or invalid."""


def _num(env: Mapping[str, str], name: str, default: float, cast: type = float):
    raw = (env.get(name) or "").strip()
    if not raw:
        return default
    try:
        return cast(raw)
    except ValueError as exc:
        raise GernasConfigError(f"{name}={raw!r} is not a valid {cast.__name__}") from exc


@dataclass
class GernasConfig:
    # Identity — mandatory (Blueprint §2 business / infrastructure)
    service_name: str
    use_case_id: str
    application_id: str
    business_domain: str
    environment: str
    service_version: str = "0.0.0"
    agent_version: str = ""
    # Infrastructure context (Blueprint §2) — optional, pod derived inside Kubernetes
    cluster: str = ""
    namespace: str = ""
    pod: str = ""
    region: str = ""
    # Export to the Collector
    exporter: str = "otlp"
    otlp_endpoint: str = ""
    otlp_transport: str = "grpc"
    otlp_api_key: str = ""
    otlp_timeout_s: float = 10.0
    sampling_rate: float = 1.0
    metric_interval_ms: int = 30_000
    log_namespaces: tuple[str, ...] = ("gernas",)
    # Agent runtime KPIs (Blueprint §5 loop rate, maximum step violation)
    loop_threshold: int = 3
    max_steps: int = 50
    # Cost attribution (Blueprint §11)
    rate_card_path: str = ""
    # Identity tokenization (Blueprint §2 tokenized_user_id)
    user_token_key: str = ""
    # Instrumentation scopes whose spans duplicate SDK spans and are not exported
    # (e.g. "fastmcp": its tools/call span duplicates the SDK's server tool.call)
    suppressed_scopes: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        missing = [env for attr, env in _REQUIRED.items() if not str(getattr(self, attr) or "").strip()]
        if missing:
            raise GernasConfigError(
                "Missing required Gernas configuration: "
                + ", ".join(missing)
                + ". Set these environment variables (see docs/getting-started.md)."
            )
        if self.exporter not in EXPORTERS:
            raise GernasConfigError(f"GERNAS_EXPORTER={self.exporter!r} must be one of {EXPORTERS}")
        if self.otlp_transport not in TRANSPORTS:
            raise GernasConfigError(f"GERNAS_OTLP_TRANSPORT={self.otlp_transport!r} must be one of {TRANSPORTS}")
        if self.exporter == "otlp" and not self.otlp_endpoint:
            raise GernasConfigError(
                "GERNAS_OTLP_ENDPOINT is required when GERNAS_EXPORTER=otlp "
                "(the OTel Collector, e.g. http://127.0.0.1:4317)."
            )
        if not 0.0 <= self.sampling_rate <= 1.0:
            raise GernasConfigError("GERNAS_SAMPLING_RATE must be between 0.0 and 1.0")
        if not self.agent_version:
            self.agent_version = self.service_version

    @classmethod
    def from_env(cls, service_name: str | None = None, *, environ: Mapping[str, str] | None = None) -> GernasConfig:
        """Build the config from ``GERNAS_*`` variables (``service_name`` overrides GERNAS_SERVICE_NAME)."""
        env = os.environ if environ is None else environ
        pod = env.get("GERNAS_POD", "") or (env.get("HOSTNAME", "") if env.get("KUBERNETES_SERVICE_HOST") else "")
        return cls(
            service_name=service_name or env.get("GERNAS_SERVICE_NAME", ""),
            use_case_id=env.get("GERNAS_USE_CASE_ID", ""),
            application_id=env.get("GERNAS_APPLICATION_ID", ""),
            business_domain=env.get("GERNAS_BUSINESS_DOMAIN", ""),
            environment=env.get("GERNAS_ENVIRONMENT", ""),
            service_version=env.get("GERNAS_SERVICE_VERSION", "0.0.0"),
            agent_version=env.get("GERNAS_AGENT_VERSION", ""),
            cluster=env.get("GERNAS_CLUSTER", ""),
            namespace=env.get("GERNAS_NAMESPACE", ""),
            pod=pod,
            region=env.get("GERNAS_REGION", ""),
            exporter=(env.get("GERNAS_EXPORTER") or "otlp").strip().lower(),
            otlp_endpoint=(env.get("GERNAS_OTLP_ENDPOINT") or "").strip(),
            otlp_transport=(env.get("GERNAS_OTLP_TRANSPORT") or "grpc").strip().lower(),
            otlp_api_key=env.get("GERNAS_OTLP_API_KEY", ""),
            otlp_timeout_s=_num(env, "GERNAS_OTLP_TIMEOUT_S", 10.0),
            sampling_rate=_num(env, "GERNAS_SAMPLING_RATE", 1.0),
            metric_interval_ms=_num(env, "GERNAS_METRIC_INTERVAL_MS", 30_000, int),
            log_namespaces=tuple(
                p.strip() for p in (env.get("GERNAS_LOG_NAMESPACES") or "gernas").split(",") if p.strip()
            ),
            loop_threshold=_num(env, "GERNAS_LOOP_THRESHOLD", 3, int),
            max_steps=_num(env, "GERNAS_MAX_STEPS", 50, int),
            rate_card_path=env.get("GERNAS_RATE_CARD_PATH", ""),
            user_token_key=env.get("GERNAS_USER_TOKEN_KEY", ""),
            suppressed_scopes=tuple(
                p.strip() for p in (env.get("GERNAS_SUPPRESSED_SCOPES") or "").split(",") if p.strip()
            ),
        )

    @property
    def envelope(self) -> dict[str, str]:
        """Business + environment context stamped first on every span."""
        return {
            Ctx.BUSINESS_DOMAIN: self.business_domain,
            Ctx.USE_CASE_ID: self.use_case_id,
            Ctx.APPLICATION_ID: self.application_id,
            Ctx.ENVIRONMENT: self.environment,
        }

    @property
    def resource(self) -> Resource:
        attrs: dict[str, Any] = {
            SERVICE_NAME: self.service_name,
            SERVICE_VERSION: self.service_version,
            Ctx.SDK_VERSION: __version__,
            **self.envelope,
        }
        for key, value in (
            (Ctx.CLUSTER, self.cluster),
            (Ctx.NAMESPACE, self.namespace),
            (Ctx.POD, self.pod),
            (Ctx.REGION, self.region),
        ):
            if value:
                attrs[key] = value
        return Resource.create(attrs)
