"""Derive implicit feedback from how a turn went (architecture §4.1)."""

from __future__ import annotations

from .store import record


def capture_implicit(*, session_id, user_id, turn_ref, tier, status, rows_returned):
    if status != "success":
        record(session_id=session_id, user_id=user_id, turn_ref=turn_ref,
               signal_type="implicit", polarity="negative",
               stage="generation" if tier == "full_dynamic" else "routing")
    elif rows_returned == 0:
        record(session_id=session_id, user_id=user_id, turn_ref=turn_ref,
               signal_type="implicit", polarity="negative", stage="filter_selection")
