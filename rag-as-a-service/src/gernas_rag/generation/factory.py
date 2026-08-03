"""Single place that wires a fully-configured :class:`ResponseGenerator`.

Answer quality depends on whether the artifact store and the vision LLM are
attached: without them ``_should_hydrate`` always returns False and the
multimodal path never runs, silently. That wiring used to be duplicated in the
API lifespan and re-derived (incorrectly) in every offline script, so evaluation
measured a text-only system while production served a multimodal one. Both now
call this function, which makes that class of drift impossible.
"""

from __future__ import annotations

from ..config.settings import Settings
from ..llm.base import BaseLLM
from ..llm.factory import get_llm
from ..storage.artifact_store import BaseArtifactStore, get_artifact_store
from ..utils.logging import get_logger
from .generator import ResponseGenerator

logger = get_logger(__name__)


def resolve_generation_settings(
    settings: Settings, force_hydration: bool | None = None
) -> Settings:
    """Apply a hydration override, returning settings to build the generator from.

    ``force_hydration`` is for A/B evaluation ("what does image hydration buy
    us?"), not for production use:
      ``None``   follow the configured ``hydration.enabled`` (default)
      ``True``   enable hydration regardless of config
      ``False``  disable hydration regardless of config
    """
    if force_hydration is None:
        return settings
    hydration = settings.hydration.model_copy(update={"enabled": force_hydration})
    return settings.model_copy(update={"hydration": hydration})


def build_artifact_store(settings: Settings) -> BaseArtifactStore | None:
    """Artifact store when either ingest-time enrichment or hydration needs it."""
    if not (settings.hydration.enabled or settings.enrichment.enabled):
        return None
    return get_artifact_store(settings.artifact_store)


def build_vision_llm(settings: Settings) -> BaseLLM | None:
    """Vision-capable LLM for hydration, reusing the primary LLM's credentials."""
    if not settings.hydration.enabled:
        return None
    vision_config = settings.llm.model_copy(
        update={
            "provider": settings.hydration.vision_provider,
            "model_name": settings.hydration.vision_model_name,
        }
    )
    return get_llm(vision_config)


def build_generator(
    settings: Settings,
    llm: BaseLLM,
    *,
    force_hydration: bool | None = None,
) -> tuple[ResponseGenerator, Settings]:
    """Build a generator wired for the effective configuration.

    Returns the generator together with the settings it was built from, so a
    caller that passed ``force_hydration`` reports the configuration that
    actually ran rather than the one on disk.
    """
    effective = resolve_generation_settings(settings, force_hydration)
    artifact_store = build_artifact_store(effective)
    vision_llm = build_vision_llm(effective)

    if effective.hydration.enabled and (artifact_store is None or vision_llm is None):
        # Hydration silently degrades to text-only in this state, which is the
        # exact failure the trace exists to surface. Say so at build time too.
        logger.warning(
            "Hydration is enabled but its dependencies are incomplete; answers will be text-only",
            artifact_store=artifact_store is not None,
            vision_llm=vision_llm is not None,
        )

    generator = ResponseGenerator(
        effective, llm, artifact_store=artifact_store, vision_llm=vision_llm
    )
    logger.info(
        "Generator built",
        hydration_enabled=effective.hydration.enabled,
        hydration_mode=effective.hydration.mode,
        vision_model=effective.hydration.vision_model_name if vision_llm else None,
    )
    return generator, effective
