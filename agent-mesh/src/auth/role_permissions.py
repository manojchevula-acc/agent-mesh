"""Role-based permission matrix for FAB AgentMesh.

Maps each BankingRole to the task categories it is allowed and denied to perform.
This is the single source of truth for authorization decisions made by the
ComplianceAgent. The RBAC executor resolves these at request time and injects
them into the compliance prompt so the LLM can enforce them semantically.
"""
from __future__ import annotations

from typing import Dict, List

# Task category labels used in compliance prompt and llm_reasoning blocks.
# These are descriptive strings — the ComplianceAgent maps user intent to them.
ROLE_PERMISSIONS: Dict[str, Dict] = {
    "customer": {
        "allowed_tasks": [
            "own_account_query",
            "own_transaction_history",
            "product_inquiry",
            "general_banking_info",
            "public_knowledge_query",
        ],
        "denied_tasks": [
            "cross_customer_query",
            "internal_policy_access",
            "credit_assessment",
            "bulk_data_export",
            "audit_trail_review",
            "agent_configuration",
        ],
        "scope": "own account and transaction data only; public banking knowledge",
    },
    "relationship_manager": {
        "allowed_tasks": [
            "customer_portfolio_query",
            "own_customer_360",
            "pricing_tools",
            "product_inquiry",
            "general_banking_info",
            "public_knowledge_query",
        ],
        "denied_tasks": [
            "bulk_data_export",
            "system_configuration",
            "cross_branch_access",
            "agent_configuration",
            "audit_trail_review",
            "regulatory_knowledge",
            "credit_assessment",
            "compliance_data_query",
            "internal_policy_access",
        ],
        "scope": "assigned customer portfolio; pricing and product knowledge only",
    },
    "compliance_officer": {
        "allowed_tasks": [
            "policy_document_access",
            "compliance_data_query",
            "audit_trail_review",
            "regulatory_knowledge",
            "general_banking_info",
        ],
        "denied_tasks": [
            "customer_pii_unrestricted",
            "bulk_data_export",
            "system_configuration",
            "agent_configuration",
            "credit_assessment",
        ],
        "scope": "policy documents, compliance reports, audit and regulatory data",
    },
    "credit_officer": {
        "allowed_tasks": [
            "customer_credit_query",
            "customer_360",
            "policy_document_access",
            "compliance_data_query",
            "regulatory_knowledge",
            "credit_assessment",
            "general_banking_info",
        ],
        "denied_tasks": [
            "bulk_data_export",
            "system_configuration",
            "agent_configuration",
            "audit_trail_review",
        ],
        "scope": "credit products, loan workflows, customer risk data, policy access",
    },
    "branch_operations_officer": {
        "allowed_tasks": [
            "policy_document_access",
            "regulatory_knowledge",
            "own_branch_customer_data",
            "service_request_query",
            "operational_procedures",
            "general_banking_info",
        ],
        "denied_tasks": [
            "cross_branch_access",
            "credit_assessment",
            "bulk_data_export",
            "system_configuration",
            "agent_configuration",
            "audit_trail_review",
        ],
        "scope": "own branch operations, service requests, policy and regulatory knowledge",
    },
    "operations_manager": {
        "allowed_tasks": ["*"],
        "denied_tasks": [],
        "scope": "full operational access — dashboards, reporting, all data domains",
    },
    "platform_administrator": {
        "allowed_tasks": ["*"],
        "denied_tasks": [],
        "scope": "full platform access — agents, workflows, MCP, monitoring, all data",
    },
}


def get_permission_scope(role: str) -> str:
    return ROLE_PERMISSIONS.get(role, {}).get("scope", "unknown role — no permissions granted")


def get_allowed_tasks(role: str) -> List[str]:
    return ROLE_PERMISSIONS.get(role, {}).get("allowed_tasks", [])


def get_denied_tasks(role: str) -> List[str]:
    return ROLE_PERMISSIONS.get(role, {}).get("denied_tasks", [])
