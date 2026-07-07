"""Database access package — pooled, SELECT-only credential.

Exposes the module-level ``db`` singleton used by every tool: ``db.execute(sql, params)``.
"""

from .executor import db

__all__ = ["db"]
