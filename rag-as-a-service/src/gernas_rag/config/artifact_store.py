"""Artifact store configuration (content-addressed image storage)."""

from pydantic import BaseModel


class ArtifactStoreConfig(BaseModel):
    backend: str = "local"  # 'local' | 's3' (s3 interface stubbed, not implemented this phase)
    local_path: str = "./artifacts"
