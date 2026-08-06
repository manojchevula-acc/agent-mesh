"""Provider registry + model catalogue resolution.

Two levels of indirection, both config-driven:

  1. MODEL CATALOGUE (config/model_registry.yaml) — alias/hf_id -> ModelSpec
     (provider, dim, image_size, licence, score_floor, ...). Adding a KNOWN
     model is a YAML edit; zero code.
  2. PROVIDER REGISTRY (this module, @register_provider) — provider name ->
     embedder class. Adding a new model FAMILY is one new class + decorator.
"""

from dataclasses import dataclass, field, fields
from pathlib import Path
from typing import Any, Callable, Type

import yaml

from ...utils.logging import get_logger

logger = get_logger(__name__)

_REGISTRY_PATH = Path(__file__).resolve().parents[4] / "config" / "model_registry.yaml"


@dataclass(frozen=True)
class ModelSpec:
    alias: str
    hf_id: str
    provider: str = "hf_dual_encoder"
    dim: int | None = None
    image_size: int | None = None
    max_text_length: int | None = None
    trust_remote_code: bool = False
    licence: str = "unknown"
    commercial_use: bool = True
    score_floor: float = 0.10
    normalize: bool = True
    distance_metric: str = "cosine"
    notes: str = ""
    extra: dict[str, Any] = field(default_factory=dict)


# ── Provider registry ────────────────────────────────────────────────────
_PROVIDERS: dict[str, Type] = {}


def register_provider(name: str) -> Callable[[Type], Type]:
    def _decorator(cls: Type) -> Type:
        existing = _PROVIDERS.get(name)
        if existing is not None and existing is not cls:
            raise ValueError(f"Duplicate multimodal provider: {name}")
        _PROVIDERS[name] = cls
        return cls

    return _decorator


def _load_provider_modules() -> None:
    """Import provider modules for their registration side effects."""
    from . import hf_dual_encoder  # noqa: F401

    for optional in ("open_clip_embedder", "st_embedder", "bge_vl_embedder"):
        try:
            __import__(f"{__package__}.{optional}")
        except ImportError as exc:  # Optional backends — absent deps are fine.
            logger.debug("Optional provider unavailable", module=optional, error=str(exc))


def get_provider_class(name: str) -> Type:
    _load_provider_modules()
    if name not in _PROVIDERS:
        raise ValueError(
            f"Unknown multimodal provider '{name}'. Registered: {sorted(_PROVIDERS)}"
        )
    return _PROVIDERS[name]


def registered_providers() -> list[str]:
    _load_provider_modules()
    return sorted(_PROVIDERS)


# ── Model catalogue ──────────────────────────────────────────────────────
_CATALOGUE: dict[str, ModelSpec] | None = None


def _load_catalogue() -> dict[str, ModelSpec]:
    global _CATALOGUE
    if _CATALOGUE is not None:
        return _CATALOGUE

    if not _REGISTRY_PATH.exists():
        logger.warning(
            "model_registry.yaml not found; catalogue empty", path=str(_REGISTRY_PATH)
        )
        _CATALOGUE = {}
        return _CATALOGUE

    raw = yaml.safe_load(_REGISTRY_PATH.read_text(encoding="utf-8")) or {}
    defaults = raw.get("defaults") or {}
    known = {f.name for f in fields(ModelSpec)}

    catalogue: dict[str, ModelSpec] = {}
    for alias, entry in (raw.get("models") or {}).items():
        merged = {**defaults, **(entry or {})}
        spec = ModelSpec(
            alias=alias,
            extra={k: v for k, v in merged.items() if k not in known},
            **{k: v for k, v in merged.items() if k in known and k != "alias"},
        )
        catalogue[alias] = spec
        catalogue[spec.hf_id] = spec  # Resolvable by either key.

    _CATALOGUE = catalogue
    return catalogue


def reset_catalogue_cache() -> None:
    """Test hook — forces the YAML to be re-read."""
    global _CATALOGUE
    _CATALOGUE = None


def resolve_spec(model_name: str, provider_override: str | None = None) -> ModelSpec:
    """Resolve a config ``model_name`` to a :class:`ModelSpec`.

    Unknown models are NOT an error — they fall back to ``hf_dual_encoder`` with
    a probed dimension, so a brand-new SigLIP/CLIP checkpoint works the day it
    lands on the Hub without a code or registry change.
    """
    catalogue = _load_catalogue()
    spec = catalogue.get(model_name)
    if spec is None:
        logger.info("Model not in registry; using generic defaults", model=model_name)
        spec = ModelSpec(
            alias=model_name,
            hf_id=model_name,
            provider=provider_override or "hf_dual_encoder",
        )
    if provider_override and provider_override != spec.provider:
        logger.info(
            "Provider overridden by config",
            model=model_name,
            registry=spec.provider,
            override=provider_override,
        )
        spec = ModelSpec(
            **{
                **{f.name: getattr(spec, f.name) for f in fields(ModelSpec)},
                "provider": provider_override,
            }
        )
    return spec
