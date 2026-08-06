"""Content-addressed asset store.

Layout:  {root}/{sha[:2]}/{sha}.{ext}   and   {root}/{sha[:2]}/{sha}_thumb.{ext}

Sharding by the first byte keeps directory sizes sane. Because the filename IS
the content hash, ``put`` is idempotent and cross-document dedup is free.
"""

import re
from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path

from ..config.multimodal import AssetStorageConfig
from ..utils.hashing import make_asset_id
from ..utils.logging import get_logger

logger = get_logger(__name__)

# Asset ids arrive from an HTTP path parameter, so this is a security control,
# not a formatting nicety: without it the id is a directory-traversal primitive.
_ID_RE = re.compile(r"^[a-f0-9]{32}$")


@dataclass
class StoredAsset:
    asset_id: str
    uri: str
    path: str
    thumb_uri: str | None = None
    byte_size: int = 0


class BaseAssetStore(ABC):
    @abstractmethod
    def put(self, data: bytes, thumbnail: bytes | None = None) -> StoredAsset: ...

    @abstractmethod
    def get(self, asset_id: str) -> bytes: ...

    @abstractmethod
    def get_thumbnail(self, asset_id: str) -> bytes: ...

    @abstractmethod
    def exists(self, asset_id: str) -> bool: ...

    @abstractmethod
    def delete(self, asset_id: str) -> None: ...


class LocalAssetStore(BaseAssetStore):
    def __init__(self, config: AssetStorageConfig) -> None:
        self._c = config
        self._root = Path(config.root)
        self._ext = config.image_format.lower()
        self._root.mkdir(parents=True, exist_ok=True)

    # ── Paths ─────────────────────────────────────────────────────────
    @staticmethod
    def _validate(asset_id: str) -> str:
        if not _ID_RE.match(asset_id):
            raise ValueError(f"Invalid asset id: {asset_id!r}")
        return asset_id

    def _path(self, asset_id: str, thumb: bool = False) -> Path:
        self._validate(asset_id)
        suffix = "_thumb" if thumb else ""
        return self._root / asset_id[:2] / f"{asset_id}{suffix}.{self._ext}"

    def uri_for(self, asset_id: str, thumb: bool = False) -> str:
        base = self._c.serve_base_url.rstrip("/")
        return f"{base}/{asset_id}/thumb" if thumb else f"{base}/{asset_id}"

    # ── Operations ────────────────────────────────────────────────────
    def put(self, data: bytes, thumbnail: bytes | None = None) -> StoredAsset:
        asset_id = make_asset_id(data)
        path = self._path(asset_id)
        if not path.exists():  # Idempotent — same bytes, same file.
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp = path.with_suffix(f".{self._ext}.tmp")
            tmp.write_bytes(data)
            tmp.replace(path)  # Atomic: no partially-written asset is observable.

        thumb_uri = None
        if thumbnail is not None:
            thumb_path = self._path(asset_id, thumb=True)
            if not thumb_path.exists():
                tmp = thumb_path.with_suffix(f".{self._ext}.tmp")
                tmp.write_bytes(thumbnail)
                tmp.replace(thumb_path)
            thumb_uri = self.uri_for(asset_id, thumb=True)

        return StoredAsset(
            asset_id=asset_id,
            uri=self.uri_for(asset_id),
            path=str(path),
            thumb_uri=thumb_uri,
            byte_size=len(data),
        )

    def get(self, asset_id: str) -> bytes:
        return self._path(asset_id).read_bytes()

    def get_thumbnail(self, asset_id: str) -> bytes:
        path = self._path(asset_id, thumb=True)
        return path.read_bytes() if path.exists() else self.get(asset_id)

    def exists(self, asset_id: str) -> bool:
        try:
            return self._path(asset_id).exists()
        except ValueError:
            return False

    def delete(self, asset_id: str) -> None:
        for thumb in (False, True):
            path = self._path(asset_id, thumb=thumb)
            path.unlink(missing_ok=True)


def get_asset_store(config: AssetStorageConfig) -> BaseAssetStore:
    match config.backend:
        case "local":
            return LocalAssetStore(config)
        case _:
            # The S3 seam exists in config; implement when the corpus outgrows
            # local disk (design doc §9.6).
            raise ValueError(f"Unsupported asset storage backend: {config.backend}")
