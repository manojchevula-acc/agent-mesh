"""Durable JSON storage for completed UI session snapshots."""

import json
import os
import pathlib
import threading
import uuid


class SessionHistoryStore:
    def __init__(self, directory: pathlib.Path | None = None) -> None:
        self.directory = (directory or pathlib.Path(__file__).resolve().parent / "history").resolve()
        self.directory.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()

    @staticmethod
    def _normalize_id(session_id: str) -> str:
        try:
            return str(uuid.UUID(session_id))
        except (ValueError, TypeError, AttributeError) as exc:
            raise ValueError("Session history ID must be a valid UUID") from exc

    def _record_path(self, session_id: str) -> pathlib.Path:
        return self.directory / f"{self._normalize_id(session_id)}.json"

    def list_records(self) -> list[dict]:
        records: list[dict] = []
        with self._lock:
            for path in self.directory.glob("*.json"):
                try:
                    record = json.loads(path.read_text(encoding="utf-8"))
                    if not isinstance(record, dict) or not isinstance(record.get("timestamp"), (int, float)):
                        continue
                    self._normalize_id(record.get("id"))
                    records.append(record)
                except (OSError, json.JSONDecodeError, ValueError):
                    continue
        return sorted(records, key=lambda record: record["timestamp"])

    def upsert(self, record: dict) -> dict:
        session_id = self._normalize_id(record.get("id"))
        normalized = {**record, "id": session_id}
        path = self._record_path(session_id)
        temp_path = self.directory / f".{session_id}.{uuid.uuid4().hex}.tmp"

        with self._lock:
            try:
                with temp_path.open("w", encoding="utf-8") as handle:
                    json.dump(normalized, handle, ensure_ascii=False, indent=2)
                    handle.flush()
                    os.fsync(handle.fileno())
                os.replace(temp_path, path)
            finally:
                temp_path.unlink(missing_ok=True)
        return normalized

    def clear(self) -> None:
        with self._lock:
            for path in self.directory.glob("*.json"):
                path.unlink(missing_ok=True)