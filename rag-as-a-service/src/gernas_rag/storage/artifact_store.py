"""Artifact store — content-addressed storage for extracted image bytes.

Every extracted figure/table image is written under ``sha256(bytes)`` so the same
image maps to the same key (idempotent writes), and the key travels with the chunk
as ``ChunkMetadata.artifact_ref``. At answer time, hydration resolves that key back
to the exact bytes captured at ingest — the ref *is* the content address, so a chunk
can never resolve to the wrong image.
"""

from abc import ABC, abstractmethod
from pathlib import Path

from ..config.artifact_store import ArtifactStoreConfig
from ..utils.hashing import hash_bytes
from ..utils.logging import get_logger

logger = get_logger(__name__)

_EXT_BY_MIME = {"image/png": "png", "image/jpeg": "jpg"}
_MIME_BY_EXT = {"png": "image/png", "jpg": "image/jpeg", "jpeg": "image/jpeg"}


class BaseArtifactStore(ABC):
    @abstractmethod
    async def put_bytes(self, data: bytes, mime_type: str) -> str:
        """Store bytes, return a content-addressed ref, e.g. ``sha256:<hex>.png``."""
        ...

    @abstractmethod
    async def get_bytes(self, ref: str) -> tuple[bytes, str]:
        """Resolve a ref back to ``(data, mime_type)``. Raises if not found."""
        ...


class LocalArtifactStore(BaseArtifactStore):
    """Local-disk artifact store. In production this would be an S3 bucket sharing
    the same interface; local disk (a mounted volume) is sufficient for the POC.
    """

    def __init__(self, root: Path | str) -> None:
        self._root = Path(root)
        self._root.mkdir(parents=True, exist_ok=True)

    def _path_for(self, digest: str, ext: str) -> Path:
        return self._root / f"{digest}.{ext}"

    async def put_bytes(self, data: bytes, mime_type: str) -> str:
        digest = hash_bytes(data)
        ext = _EXT_BY_MIME.get(mime_type, "bin")
        ref = f"sha256:{digest}.{ext}"
        path = self._path_for(digest, ext)
        if not path.exists():  # Content-addressed => idempotent, skip rewrite.
            path.write_bytes(data)
        return ref

    async def get_bytes(self, ref: str) -> tuple[bytes, str]:
        digest, _, ext = ref.removeprefix("sha256:").partition(".")
        path = self._path_for(digest, ext)
        data = path.read_bytes()
        mime_type = _MIME_BY_EXT.get(ext, "application/octet-stream")
        return data, mime_type


def get_artifact_store(config: ArtifactStoreConfig) -> BaseArtifactStore:
    """Factory — selects an artifact store backend from config."""
    if config.backend == "local":
        return LocalArtifactStore(config.local_path)
    # S3ArtifactStore would share BaseArtifactStore; not implemented this phase.
    raise ValueError(f"Unsupported artifact_store backend: {config.backend}")
