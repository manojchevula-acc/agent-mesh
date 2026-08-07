"""
datalayer-as-service/mcp_server/external_service.py
-----------------------------------------------------
Mock external services demonstrating independent per-service authentication.

These represent third-party or internal APIs that MCP tools call. Each service
has its own credential system — completely independent from:
  - MCP_API_KEY / MCP_JWT_SECRET  (agent ↔ MCP server layer)
  - JWT_SECRET                    (user ↔ chat/hub layer)
  - HUB_API_KEY                   (agent ↔ hub layer)

Services (all on port 8010):

  POST /check             Credit bureau credit check
                          Auth: Authorization: Bearer <CREDIT_BUREAU_VALID_TOKEN>
                          Pattern: Bearer JWT (own token, not forwarded agent JWT)

  GET  /fx/{pair}         FX spot rate lookup
                          Auth: X-API-Key: <FX_RATE_API_KEY>
                          Pattern: API Key in custom header (not Authorization)

  POST /sanctions         Sanctions / AML screening (admin-only MCP tool)
                          Auth: Authorization: Bearer <SANCTIONS_VALID_TOKEN>
                          Pattern: Bearer JWT with separate secret from credit bureau

Environment variables (matching credentials in tool_registry.py default seed):
  CREDIT_BUREAU_VALID_TOKEN   accepted token for /check     (default: credit-bureau-dev-token)
  FX_RATE_API_KEY             accepted key for /fx          (default: fx-rate-dev-key)
  SANCTIONS_VALID_TOKEN       accepted token for /sanctions (default: sanctions-dev-token)

Run:
  python datalayer-as-service/mcp_server/external_service.py [port]
  → http://localhost:8010

Seed matching credentials in the tool registry:
  python datalayer-as-service/mcp_server/tool_registry.py --seed
"""

import os
import pathlib
import random
import sys

try:
    from dotenv import load_dotenv as _load
    _root = pathlib.Path(__file__).resolve().parent.parent.parent
    _load(_root / ".env")
    _load(_root / "datalayer-as-service" / ".env", override=True)
except ImportError:
    pass

import uvicorn
from fastapi import FastAPI, Header, HTTPException
from pydantic import BaseModel

PORT = int(sys.argv[1]) if len(sys.argv) > 1 else 8010

# ── Per-service accepted credentials ─────────────────────────────────────────
# These are the INBOUND tokens this service validates.
# The OUTBOUND tokens (what the MCP server sends) are stored in tool_registry.py.
# In production, these come from a secrets vault and rotate on a schedule.

_CREDIT_TOKEN    = os.environ.get("CREDIT_BUREAU_VALID_TOKEN", "credit-bureau-dev-token")
_FX_KEY          = os.environ.get("FX_RATE_API_KEY",           "fx-rate-dev-key")
_SANCTIONS_TOKEN = os.environ.get("SANCTIONS_VALID_TOKEN",     "sanctions-dev-token")

app = FastAPI(
    title="Mock External Services",
    description=(
        "Mock third-party APIs for FAB MCP demo. Each endpoint uses its own "
        "independent authentication — unrelated to MCP_API_KEY or JWT_SECRET."
    ),
    version="1.0.0",
)


def _require_bearer(authorization: str, expected: str, service: str) -> None:
    """Validate Authorization: Bearer <token>. Raises 401 on mismatch."""
    if not authorization.startswith("Bearer "):
        raise HTTPException(
            status_code=401,
            detail=f"{service}: expected 'Authorization: Bearer <token>'",
            headers={"WWW-Authenticate": "Bearer"},
        )
    token = authorization.removeprefix("Bearer ").strip()
    if token != expected:
        raise HTTPException(
            status_code=401,
            detail=f"{service}: invalid or expired token — rotate via tool_registry.py",
            headers={"WWW-Authenticate": "Bearer"},
        )


# ── /check — Credit Bureau ────────────────────────────────────────────────────

class CreditCheckRequest(BaseModel):
    customer_id:  str
    loan_amount:  float = 0.0
    product_type: str   = ""


@app.post("/check")
async def credit_bureau_check(
    body: CreditCheckRequest,
    authorization: str = Header(...),
):
    """Credit bureau check.

    Authenticates with: Authorization: Bearer <CREDIT_BUREAU_VALID_TOKEN>
    This token is stored in tool_registry.py under tool_name='credit_bureau_check'.
    It is NOT the MCP_API_KEY — it is this service's own credential.
    """
    _require_bearer(authorization, _CREDIT_TOKEN, "CreditBureau")
    score = random.randint(550, 850)
    return {
        "service":               "credit-bureau",
        "customer_id":           body.customer_id,
        "credit_score":          score,
        "risk_band":             "LOW" if score > 750 else ("MEDIUM" if score > 650 else "HIGH"),
        "default_probability_pct": round((900 - score) / 10, 2),
        "recommendation":        "APPROVE" if score > 700 else "REVIEW",
        "auth_pattern":          "Bearer JWT — independent from MCP_API_KEY",
    }


# ── /fx/{pair} — FX Rate Provider ────────────────────────────────────────────

_FX_RATES = {
    "USDAED": 3.6725, "EURAED": 3.96,  "GBPAED": 4.65,
    "USDINR": 83.5,   "USDEUR": 0.92,  "EURUSD": 1.08,
    "USDGBP": 0.78,   "USDCNY": 7.25,  "USDSGD": 1.35,
    "USDCHF": 0.91,   "AEDUSF": 0.272, "AEDINR": 22.7,
}


@app.get("/fx/{pair}")
async def fx_rate_lookup(
    pair: str,
    x_api_key: str = Header(..., alias="X-API-Key"),
):
    """FX spot rate.

    Authenticates with: X-API-Key: <FX_RATE_API_KEY>
    Uses a CUSTOM HEADER (not Authorization: Bearer) — demonstrates that
    different external services can use completely different auth patterns.
    """
    if x_api_key != _FX_KEY:
        raise HTTPException(
            status_code=401,
            detail="FXRate: invalid X-API-Key — rotate via tool_registry.py --rotate fx_rate_lookup",
        )
    pair_upper = pair.upper()
    rate = _FX_RATES.get(pair_upper)
    if rate is None:
        available = ", ".join(_FX_RATES.keys())
        raise HTTPException(
            status_code=404,
            detail=f"FX pair '{pair}' not found. Available: {available}",
        )
    return {
        "service":      "fx-rate-provider",
        "pair":         pair_upper,
        "rate":         rate,
        "source":       "mock",
        "auth_pattern": "X-API-Key header (not Bearer JWT) — different pattern from credit bureau",
    }


# ── /sanctions — Compliance Screening ────────────────────────────────────────

class SanctionsRequest(BaseModel):
    customer_id:   str
    customer_name: str = ""
    country:       str = ""


_WATCHLIST = {"CUST999", "WATCH001", "WATCH002"}


@app.post("/sanctions")
async def sanctions_screen(
    body: SanctionsRequest,
    authorization: str = Header(...),
):
    """Sanctions / AML screening.

    Authenticates with: Authorization: Bearer <SANCTIONS_VALID_TOKEN>
    Uses Bearer JWT like credit bureau but with a DIFFERENT token —
    demonstrates per-service credential isolation.
    If CUST999 or WATCH001/WATCH002 are checked, returns a BLOCK result.
    """
    _require_bearer(authorization, _SANCTIONS_TOKEN, "SanctionsScreen")
    hit = body.customer_id in _WATCHLIST
    return {
        "service":         "sanctions-screening",
        "customer_id":     body.customer_id,
        "customer_name":   body.customer_name,
        "sanctions_hit":   hit,
        "risk_level":      "CRITICAL" if hit else "CLEAR",
        "action_required": "BLOCK_AND_REPORT" if hit else "PROCEED",
        "auth_pattern":    "Bearer JWT — same header as credit bureau but different token",
    }


# ── /tokens — Dev helper ──────────────────────────────────────────────────────

@app.get("/tokens")
async def get_dev_tokens():
    """Return the expected dev tokens. Used by tool_registry.py --seed to verify alignment."""
    return {
        "credit_bureau": {
            "tool_name":   "credit_bureau_check",
            "header":      "Authorization",
            "value":       f"Bearer {_CREDIT_TOKEN}",
            "auth_type":   "bearer_jwt",
            "endpoint":    f"http://localhost:{PORT}/check",
        },
        "fx_rate": {
            "tool_name":   "fx_rate_lookup",
            "header":      "X-API-Key",
            "value":       _FX_KEY,
            "auth_type":   "api_key_header",
            "endpoint":    f"http://localhost:{PORT}/fx/{{pair}}",
        },
        "sanctions": {
            "tool_name":   "sanctions_screen",
            "header":      "Authorization",
            "value":       f"Bearer {_SANCTIONS_TOKEN}",
            "auth_type":   "bearer_jwt",
            "endpoint":    f"http://localhost:{PORT}/sanctions",
        },
    }


@app.get("/health")
async def health():
    return {"service": "mock-external-services", "status": "ok", "port": PORT}


if __name__ == "__main__":
    print(f"Starting Mock External Services on port {PORT}...")
    print(f"  POST http://localhost:{PORT}/check        (credit bureau  — Bearer JWT)")
    print(f"  GET  http://localhost:{PORT}/fx/USDAED   (FX rate        — X-API-Key)")
    print(f"  POST http://localhost:{PORT}/sanctions    (sanctions      — Bearer JWT)")
    print(f"  GET  http://localhost:{PORT}/tokens       (dev: show expected tokens)")
    print(f"\nCredentials (set in .env to override):")
    print(f"  CREDIT_BUREAU_VALID_TOKEN = {_CREDIT_TOKEN}")
    print(f"  FX_RATE_API_KEY           = {_FX_KEY}")
    print(f"  SANCTIONS_VALID_TOKEN     = {_SANCTIONS_TOKEN}")
    print(f"\nSeed matching tool registry:")
    print(f"  python datalayer-as-service/mcp_server/tool_registry.py --seed")
    uvicorn.run(app, host="0.0.0.0", port=PORT, log_level="warning")
