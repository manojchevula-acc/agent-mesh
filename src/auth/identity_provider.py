"""FAB Banking identity provider.

Simulates corporate SSO for First Abu Dhabi Bank (FAB): maps usernames to
FAB banking roles. The role is stamped on each request and enforced by the
RBACValidationExecutor in the workflow pipeline. In production this would be
replaced by a real OIDC / Azure AD identity provider with JWT claims.

Banking RBAC Model (AgentMesh 15.0.6.2026)
-------------------------------------------
Role                       | Access scope
---------------------------|---------------------------------------------------
customer                   | Own accounts, own transactions, public knowledge
relationship_manager       | Customer portfolio, products, support workflows
branch_operations_officer  | Operational transactions, service requests, procedures
credit_officer             | Credit products, loan workflows, risk documentation
compliance_officer         | Regulatory docs, compliance reports, audit information
operations_manager         | Dashboards, operational reporting, performance metrics
platform_administrator     | Full platform access — agents, workflows, MCP, monitoring
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Dict, Optional


class BankingRole(str, Enum):
    CUSTOMER = "customer"
    RELATIONSHIP_MANAGER = "relationship_manager"
    BRANCH_OPERATIONS_OFFICER = "branch_operations_officer"
    CREDIT_OFFICER = "credit_officer"
    COMPLIANCE_OFFICER = "compliance_officer"
    OPERATIONS_MANAGER = "operations_manager"
    PLATFORM_ADMINISTRATOR = "platform_administrator"


@dataclass(frozen=True)
class User:
    username: str
    display_name: str
    role: BankingRole


# Mock FAB corporate directory. Username -> User.
_USERS: Dict[str, User] = {
    # Relationship Managers — customer portfolio, pricing support
    "alice":   User("alice",   "Alice Mansouri (Relationship Manager)", BankingRole.RELATIONSHIP_MANAGER),
    # Credit Officers — credit products, loan workflows, risk
    "bob":     User("bob",     "Bob Al-Rashid (Credit Officer)",         BankingRole.CREDIT_OFFICER),
    # Compliance Officers — regulatory, audit, compliance reports
    "carol":   User("carol",   "Carol Nasser (Compliance Officer)",      BankingRole.COMPLIANCE_OFFICER),
    # Branch Operations Officers — service requests, operational procedures
    "dave":    User("dave",    "Dave Ibrahim (Branch Operations)",       BankingRole.BRANCH_OPERATIONS_OFFICER),
    # Operations Managers — dashboards, reporting, performance
    "eve":     User("eve",     "Eve Khalifa (Operations Manager)",       BankingRole.OPERATIONS_MANAGER),
    # Platform Administrators — full access
    "farida":  User("farida",  "Farida Al-Zaabi (Platform Admin)",       BankingRole.PLATFORM_ADMINISTRATOR),
    # Customer — own accounts and public knowledge only
    "cust001": User("cust001", "Customer CUST001",                       BankingRole.CUSTOMER),
}

# Unknown users default to the most restrictive role.
_GUEST_ROLE = BankingRole.CUSTOMER


def login(username: str) -> User:
    """Resolves a username to a User. Unknown users default to the Customer role."""
    key = (username or "").strip().lower()
    return _USERS.get(key, User(key or "guest", f"Guest ({key})", _GUEST_ROLE))


def list_users() -> list[User]:
    """Returns the mock FAB directory (for the CLI login prompt)."""
    return list(_USERS.values())


# ---------------------------------------------------------------------------
# Backward-compatibility alias so existing code referencing ``Role`` still
# works without modification during the transition period.
# ---------------------------------------------------------------------------
Role = BankingRole
