import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

sys.path.insert(0, str(ROOT / "datalayer-as-service"))

from hub_service import auth as hub_auth
from mcp_server import auth as mcp_auth


def test_hub_auth_issues_and_verifies_rs256_jwt():
    token = hub_auth.generate_token(
        sub="agent",
        roles=["agent"],
        audience="fab-mcp-hub",
        expires_hours=1,
    )

    assert token
    valid, claims = hub_auth.verify_token(token, expected_audience="fab-mcp-hub")
    assert valid is True
    assert claims["sub"] == "agent"
    assert "agent" in claims["roles"]


def test_mcp_auth_accepts_server_specific_jwt(monkeypatch):
    token = hub_auth.generate_token(
        sub="agent",
        roles=["agent"],
        audience="fab-customer-server",
        server_id="fab-customer-server",
        expires_hours=1,
    )

    monkeypatch.setenv("MCP_JWKS_URL", hub_auth.get_jwks_url())
    monkeypatch.setenv("MCP_AUTH_PROVIDER", "jwt")
    monkeypatch.setenv("MCP_JWT_ISSUER", "fab-mcp-hub")
    monkeypatch.setenv("MCP_JWT_AUDIENCE", "fab-customer-server")

    valid, claims = mcp_auth.verify_mcp_token(token)
    assert valid is True
    assert claims["sub"] == "agent"
    assert claims["server_id"] == "fab-customer-server"
