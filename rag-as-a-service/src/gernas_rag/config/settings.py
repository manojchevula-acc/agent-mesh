"""Root configuration — Pydantic Settings v2.

Settings are read from environment variables and/or a ``.env`` file. A YAML config
file (selected by ``CONFIG_FILE`` or by ``environment``) can override defaults, but
environment variables always take precedence over YAML, which takes precedence over
the hardcoded field defaults.

Nested sub-configs are set with the ``RAG__`` prefix and ``__`` nested delimiter,
e.g. ``RAG__EMBEDDING__MODEL_NAME=...`` sets ``embedding.model_name``. Top-level
service fields accept both the bare name (``ENVIRONMENT``) and the prefixed form
(``RAG__ENVIRONMENT``).
"""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml
from pydantic import AliasChoices, Field
from pydantic_settings import BaseSettings, SettingsConfigDict

from .chunking import ChunkingConfig
from .embedding import EmbeddingConfig
from .evaluation import EvaluationConfig
from .ingestion import IngestionConfig
from .llm import LLMConfig
from .retrieval import RetrievalConfig
from .vectordb import VectorDBConfig

_CONFIG_DIR = Path(__file__).resolve().parents[3] / "config"


def _aliases(name: str) -> AliasChoices:
    """Accept both the bare env var and its ``RAG__`` prefixed form."""
    return AliasChoices(name, f"RAG__{name}")


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_nested_delimiter="__",  # RAG__EMBEDDING__MODEL_NAME=... sets embedding.model_name
        env_prefix="RAG__",
        case_sensitive=False,
        extra="ignore",
    )

    # ── Service ──────────────────────────────────────────────────────
    service_name: str = Field(default="gernas-rag", validation_alias=_aliases("SERVICE_NAME"))
    service_version: str = Field(default="1.0.0", validation_alias=_aliases("SERVICE_VERSION"))
    environment: str = Field(
        default="development",
        pattern="^(development|staging|production)$",
        validation_alias=_aliases("ENVIRONMENT"),
    )
    debug: bool = Field(default=False, validation_alias=_aliases("DEBUG"))
    log_level: str = Field(
        default="INFO",
        pattern="^(DEBUG|INFO|WARNING|ERROR|CRITICAL)$",
        validation_alias=_aliases("LOG_LEVEL"),
    )

    # ── API ───────────────────────────────────────────────────────────
    api_host: str = Field(default="0.0.0.0", validation_alias=_aliases("API_HOST"))
    api_port: int = Field(default=8000, validation_alias=_aliases("API_PORT"))
    api_workers: int = Field(default=1, validation_alias=_aliases("API_WORKERS"))
    api_key: str | None = Field(default=None, validation_alias=_aliases("API_KEY"))
    jwt_secret: str | None = Field(default=None, validation_alias=_aliases("JWT_SECRET"))
    jwt_algorithm: str = Field(default="RS256", validation_alias=_aliases("JWT_ALGORITHM"))
    cors_origins: list[str] = Field(
        default_factory=lambda: ["*"], validation_alias=_aliases("CORS_ORIGINS")
    )

    # ── Redis ─────────────────────────────────────────────────────────
    redis_url: str = Field(
        default="redis://localhost:6379", validation_alias=_aliases("REDIS_URL")
    )
    redis_cache_ttl_seconds: int = Field(
        default=900, validation_alias=_aliases("REDIS_CACHE_TTL_SECONDS")
    )
    redis_enabled: bool = Field(default=True, validation_alias=_aliases("REDIS_ENABLED"))

    # ── Sub-configs ───────────────────────────────────────────────────
    embedding: EmbeddingConfig = Field(default_factory=EmbeddingConfig)
    vectordb: VectorDBConfig = Field(default_factory=VectorDBConfig)
    llm: LLMConfig = Field(default_factory=LLMConfig)
    chunking: ChunkingConfig = Field(default_factory=ChunkingConfig)
    retrieval: RetrievalConfig = Field(default_factory=RetrievalConfig)
    ingestion: IngestionConfig = Field(default_factory=IngestionConfig)
    evaluation: EvaluationConfig = Field(default_factory=EvaluationConfig)


def _load_yaml_overrides() -> dict[str, Any]:
    """Load YAML layers: default.yaml, then <environment>.yaml, then local.yaml.

    Later files override earlier ones. ``CONFIG_FILE``, if set, is loaded last.
    """
    env = os.getenv("ENVIRONMENT", os.getenv("RAG__ENVIRONMENT", "development"))
    candidates = [
        _CONFIG_DIR / "default.yaml",
        _CONFIG_DIR / f"{env}.yaml",
        _CONFIG_DIR / "local.yaml",
    ]
    explicit = os.getenv("CONFIG_FILE")
    if explicit:
        candidates.append(Path(explicit))

    merged: dict[str, Any] = {}
    for path in candidates:
        if not path.exists():
            continue
        with path.open("r", encoding="utf-8") as fh:
            data = yaml.safe_load(fh) or {}
        merged = _deep_merge(merged, data)
    return merged


def _deep_merge(base: dict[str, Any], overlay: dict[str, Any]) -> dict[str, Any]:
    result = dict(base)
    for key, value in overlay.items():
        if key in result and isinstance(result[key], dict) and isinstance(value, dict):
            result[key] = _deep_merge(result[key], value)
        else:
            result[key] = value
    return result


@lru_cache
def get_settings() -> Settings:
    """Singleton accessor — import this everywhere.

    YAML provides the base layer; environment variables / ``.env`` override it.
    """
    yaml_overrides = _load_yaml_overrides()
    env_settings = Settings()
    if not yaml_overrides:
        return env_settings

    # Values explicitly provided via env / .env win over YAML.
    merged = _deep_merge(yaml_overrides, env_settings.model_dump(exclude_unset=True))
    return Settings(**merged)
