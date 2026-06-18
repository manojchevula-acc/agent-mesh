"""Mock identity provider.

Simulates corporate SSO: maps usernames to roles so the mesh can enforce
role-based access (e.g. the Finance agent is restricted to the leadership team).
In production this would be replaced by a real identity provider / OIDC.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Dict, Optional


class Role(str, Enum):
    EMPLOYEE = "employee"
    HR = "hr"
    LEADERSHIP = "leadership"


@dataclass(frozen=True)
class User:
    username: str
    display_name: str
    role: Role


# Mock corporate directory. Username -> User.
_USERS: Dict[str, User] = {
    "alice":   User("alice", "Alice (CFO)", Role.LEADERSHIP),
    "bob":     User("bob", "Bob (Engineer)", Role.EMPLOYEE),
    "carol":   User("carol", "Carol (HR Partner)", Role.HR),
    "dave":    User("dave", "Dave (Analyst)", Role.EMPLOYEE),
}

# Used when an unknown username logs in (treated as a basic employee).
_GUEST = User("guest", "Guest (Employee)", Role.EMPLOYEE)


def login(username: str) -> User:
    """Resolves a username to a User. Unknown users default to a guest employee."""
    key = (username or "").strip().lower()
    return _USERS.get(key, User(key or "guest", _GUEST.display_name, Role.EMPLOYEE))


def list_users() -> list[User]:
    """Returns the mock directory (for the CLI login prompt)."""
    return list(_USERS.values())
