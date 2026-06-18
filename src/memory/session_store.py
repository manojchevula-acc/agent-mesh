import os
import json
from typing import List, Dict, Any

class FileSessionStore:
    """
    A simple, file-based thread memory store that saves and loads
    conversation history for specific session IDs to preserve context across turns.
    """
    def __init__(self, storage_dir: str = "data/conversations"):
        self.storage_dir = storage_dir
        os.makedirs(self.storage_dir, exist_ok=True)

    def _get_path(self, session_id: str) -> str:
        # Sanitize session ID to avoid directory traversal risks
        safe_id = "".join([c for c in session_id if c.isalnum() or c in ("-", "_")])
        if not safe_id:
            safe_id = "default"
        return os.path.join(self.storage_dir, f"{safe_id}.json")

    def load_session(self, session_id: str) -> List[Dict[str, Any]]:
        """Loads message history for a given session ID."""
        path = self._get_path(session_id)
        if not os.path.exists(path):
            return []
        try:
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return []

    def save_session(self, session_id: str, history: List[Dict[str, Any]]) -> None:
        """Saves message history for a given session ID."""
        path = self._get_path(session_id)
        try:
            # Ensure the directory exists
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "w", encoding="utf-8") as f:
                json.dump(history, f, indent=2, ensure_ascii=False)
        except Exception:
            pass

    def append_message(self, session_id: str, role: str, content: str, sender: str = "") -> None:
        """Appends a new message to the session's history."""
        history = self.load_session(session_id)
        history.append({
            "role": role,
            "content": content,
            "sender": sender
        })
        self.save_session(session_id, history)

    def get_context_summary(self, session_id: str) -> str:
        """Helper to generate a clean string representation of conversation history."""
        history = self.load_session(session_id)
        if not history:
            return "No previous conversation context."
        
        summary = "Previous turns:\n"
        for msg in history:
            role_label = msg.get("sender") or msg.get("role", "user")
            content = msg.get("content", "")
            summary += f"- {role_label}: {content}\n"
        return summary

    def clear_session(self, session_id: str) -> None:
        """Deletes the session file."""
        path = self._get_path(session_id)
        if os.path.exists(path):
            try:
                os.remove(path)
            except Exception:
                pass
