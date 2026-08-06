"""Image store factory."""

from typing import Any

from ..config.vectordb import VectorDBConfig, VectorDBProvider
from .base import BaseVectorDB
from .image_store import BaseImageStore


def get_image_store(
    config: VectorDBConfig,
    collection_name: str,
    vectordb: BaseVectorDB | None = None,
) -> BaseImageStore:
    """Build the image collection client.

    Pass the existing ``vectordb`` so the underlying connection is shared. This
    is REQUIRED in Qdrant embedded mode (``qdrant_path`` set), where the local
    engine holds an exclusive lock on the storage directory and a second client
    on the same path fails to open.
    """
    match config.provider:
        case VectorDBProvider.QDRANT:
            from .qdrant_image_store import QdrantImageStore

            return QdrantImageStore(
                config, collection_name, client=_client_of(vectordb, config)
            )
        case _:
            # Milvus/Chroma image stores are follow-on work; the interface is
            # narrow enough that either is a single class.
            raise ValueError(
                f"Image collections are only implemented for Qdrant, not "
                f"'{config.provider}'. Set vectordb.provider=qdrant or disable "
                "multimodal.enabled."
            )


def _client_of(vectordb: BaseVectorDB | None, config: VectorDBConfig) -> Any | None:
    if vectordb is None:
        if config.qdrant_path:
            raise ValueError(
                "Qdrant embedded mode (qdrant_path) requires sharing the text "
                "collection's client: pass vectordb=... to get_image_store()."
            )
        return None
    return getattr(vectordb, "_client", None)
