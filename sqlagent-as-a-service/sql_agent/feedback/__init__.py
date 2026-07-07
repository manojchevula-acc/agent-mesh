"""Feedback capture — explicit ratings/corrections + implicit signals."""

from .signals import capture_implicit
from .store import record, review_queue

__all__ = ["record", "review_queue", "capture_implicit"]
