"""Artifact storage — content-addressed image bytes for the multimodal path."""

from .artifact_store import (
    BaseArtifactStore,
    LocalArtifactStore,
    get_artifact_store,
)

__all__ = ["BaseArtifactStore", "LocalArtifactStore", "get_artifact_store"]
