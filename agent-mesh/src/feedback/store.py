"""Thread-safe JSONL feedback store.

Each record captures the full Q/A pair, user rating, and a fine_tune_record
in OpenAI/Anthropic messages-array format so the file can be exported directly
to a fine-tuning job without transformation.

Usage
-----
    from src.feedback.store import record_feedback
    feedback_id = record_feedback(
        request_id="A1B2C3D4", session_id="alice_37ce2a8d",
        user="alice", role="relationship_manager",
        rating="up", query="...", answer="...",
        route="Data Layer Service", blocked=False,
        comment="Correct figures",
    )
"""
from __future__ import annotations

import json
import os
import threading
from datetime import datetime, timezone
from typing import Optional

from src.config import Config

_lock = threading.Lock()


def record_feedback(
    request_id: str,
    session_id: str,
    user: str,
    role: str,
    rating: str,
    query: str,
    answer: str,
    route: Optional[str],
    blocked: bool,
    comment: str = "",
) -> str:
    """Append one feedback record to FEEDBACK_LOG_FILE; returns feedback_id."""
    feedback_id = f"fb_{request_id}"
    record = {
        "feedback_id":  feedback_id,
        "ts":           datetime.now(timezone.utc).isoformat(),
        "request_id":   request_id,
        "session_id":   session_id,
        "user":         user,
        "role":         role,
        "rating":       rating,
        "comment":      comment,
        "query":        query,
        "answer":       answer,
        "route":        route,
        "blocked":      blocked,
        "fine_tune_record": {
            "messages": [
                {"role": "user",      "content": query},
                {"role": "assistant", "content": answer},
            ],
            "rating": rating,
        },
    }
    path = Config.FEEDBACK_LOG_FILE
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    with _lock:
        with open(path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(record, ensure_ascii=False) + "\n")
    return feedback_id
