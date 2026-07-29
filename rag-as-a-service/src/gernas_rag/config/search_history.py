"""Search history configuration (local file-based store of past /retrieve calls)."""

from pydantic import BaseModel


class SearchHistoryConfig(BaseModel):
    enabled: bool = True
    backend: str = "local"  # 'local' only for now — matches ArtifactStoreConfig's shape
    local_path: str = "./data/search_history"
    max_list_results: int = 200  # cap per-user list() scans/returns
