"""Minimal compliance configuration — replaces TelemetryConfig for compliance decorators."""
from __future__ import annotations

import os
from dataclasses import dataclass, field


@dataclass
class ComplianceConfig:
    compliance_store: str = field(
        default_factory=lambda: os.getenv(
            "COMPLIANCE_STORE", "../observability/compliance/compliance_audit.jsonl"
        )
    )
    compliance_hmac_key: str = field(
        default_factory=lambda: os.getenv(
            "COMPLIANCE_HMAC_KEY", "dev-insecure-key-change-in-prod"
        )
    )

    @classmethod
    def from_env(cls) -> "ComplianceConfig":
        return cls()
