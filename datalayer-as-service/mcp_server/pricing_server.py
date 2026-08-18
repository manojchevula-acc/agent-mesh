"""
mcp_server/pricing_server.py
------------------------------
FAB Pricing Engine MCP Server  (port 9200)

Exposes pricing-decision fab_semantic views as MCP tools, reusable prompt
templates, and reference resource documents.

Tools (9):
    pricing_recommendation   — recommended price per deal with compliance flags
    new_customer_pricing     — price for a new / prospect customer
    competitor_price_analysis — FAB offer vs competitor offer + suggested action
    pricing_trace            — step-by-step price build-up for a deal
    segment_pricing_benchmark — target margin / floor / discount limits by segment
    operations_cost_impact   — operational cost margin per product x segment
    policy_exception         — policy compliance per deal (margin, floor, discount)
    non_compliant_deals      — all deals that breach at least one policy rule
    compare_fab_vs_competitor — direct FAB vs competitor comparison with action

Prompts (3):
    analyze_deal_pricing         — structured 5-step pricing analysis workflow
    review_policy_exceptions     — compliance exception review workflow (admin)
    pricing_competitor_strategy  — competitive pricing decision workflow

Resources (3):
    pricing://policy/rules           — FAB pricing policy (margins, floors, approvals)
    pricing://guide/competitor-actions — MATCH/COUNTER/ESCALATE/REJECT action guide
    pricing://benchmarks/segments    — live segment benchmarks from fab_semantic

Authentication:
    Tokens are RS256 JWTs issued by the hub, audience = MCP_SERVER_ID.
    FastMCP JWTVerifier validates tokens (RS256 via hub JWKS endpoint).
    ClaimsExtractorMiddleware reads the validated claims into a ContextVar
    for per-tool RBAC via require_role() — no second JWKS call.

Run as a network service (streamable HTTP):
    MCP_SERVER_ID=fab-pricing-server MCP_TRANSPORT=http \\
        MCP_HOST=127.0.0.1 MCP_PORT=9200 python -m mcp_server.pricing_server
"""

import logging
import os
import pathlib

# Load .env before importing auth — auth.py reads env vars at module level.
try:
    from dotenv import load_dotenv as _load_dotenv
    _here = pathlib.Path(__file__).resolve().parent
    _load_dotenv(_here.parent.parent / ".env")          # project root .env
    _load_dotenv(_here.parent / ".env", override=True)  # datalayer-as-service/.env (MySQL)
except ImportError:
    pass

import uvicorn
from fastmcp import FastMCP

from mcp_server.auth import build_jwt_verifier, claims_middleware, MCP_AUTH_ENABLED, require_role, audit_log

from mcp_server.tools import (
    _to_json,
    query_pricing_recommendation,
    query_new_customer_pricing,
    query_competitor_price_analysis,
    query_pricing_trace,
    query_segment_pricing_benchmark,
    query_operations_cost_impact,
    query_policy_exception,
    query_non_compliant_deals,
    query_compare_fab_vs_competitor,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)

# FastMCP JWTVerifier validates RS256 tokens issued by the hub.
# audience = MCP_SERVER_ID ensures tokens cannot be reused across servers.
mcp = FastMCP(
    name="FAB Pricing Engine MCP Server",
    instructions=(
        "This server answers questions about how to price a deal at FAB — "
        "pricing recommendations, step-by-step price build (treasury + margin + "
        "risk premium + ops cost + discount), competitor comparison with a suggested "
        "action (MATCH/COUNTER/ESCALATE/REJECT), segment pricing benchmarks, "
        "operational cost impact, and policy compliance / exception detection. "
        "Pass an empty string to a filter to retrieve all records (max 15 rows)."
    ),
    auth=build_jwt_verifier(),
)


# ---------------------------------------------------------------------------
# Pricing recommendation tools
# ---------------------------------------------------------------------------

@mcp.tool()
def pricing_recommendation(customer_id: str = "") -> str:
    """Pricing recommendation per deal: rebuilt system recommended price,
    approved price, expected margin, policy benchmarks and compliance flags
    (price_below_policy_floor, margin_below_min, discount_exceeds_policy).
    Filter by customer_id (e.g. 'CUST001') or pass empty string for all deals."""
    require_role("admin", "agent")
    audit_log("pricing_recommendation", {"customer_id": customer_id})
    logger.info("[tool] pricing_recommendation | customer_id=%r", customer_id)
    return _to_json(query_pricing_recommendation(customer_id))


@mcp.tool()
def new_customer_pricing(customer_id: str = "", segment: str = "",
                         product_id: str = "", risk_rating: str = "") -> str:
    """Recommended price for a NEW customer with no relationship history.
    Builds price from segment benchmark + treasury rate + ops cost + new-customer
    buffer. Filter by customer_id, segment (e.g. 'SME'), product_id or
    risk_rating (e.g. 'Low', 'Medium', 'High')."""
    require_role("admin", "agent")
    audit_log("new_customer_pricing", {"customer_id": customer_id, "segment": segment,
                                       "product_id": product_id, "risk_rating": risk_rating})
    logger.info("[tool] new_customer_pricing | %r %r %r %r",
                customer_id, segment, product_id, risk_rating)
    return _to_json(query_new_customer_pricing(customer_id, segment, product_id, risk_rating))


@mcp.tool()
def competitor_price_analysis(customer_id: str = "", deal_id: str = "") -> str:
    """Compare FAB offer vs competitor offer: competitor_gap_bps,
    suggested action (MATCH / COUNTER / ESCALATE / REJECT) and reasoning.
    Filter by customer_id and/or deal_id or pass empty strings for all."""
    require_role("admin", "agent")
    audit_log("competitor_price_analysis", {"customer_id": customer_id, "deal_id": deal_id})
    logger.info("[tool] competitor_price_analysis | %r %r", customer_id, deal_id)
    return _to_json(query_competitor_price_analysis(customer_id, deal_id))


@mcp.tool()
def pricing_trace(customer_id: str = "", deal_id: str = "") -> str:
    """Step-by-step price build-up for a deal: treasury rate → target margin →
    risk premium → operations cost → relationship discount = final recommended
    price. Includes an explanation sentence for each component.
    Filter by customer_id and/or deal_id or pass empty strings for all."""
    require_role("admin", "agent")
    audit_log("pricing_trace", {"customer_id": customer_id, "deal_id": deal_id})
    logger.info("[tool] pricing_trace | %r %r", customer_id, deal_id)
    return _to_json(query_pricing_trace(customer_id, deal_id))


@mcp.tool()
def segment_pricing_benchmark(segment: str = "", product_id: str = "") -> str:
    """Segment-level pricing guideline: target margin pct, base margin floor,
    new-customer buffer, max relationship discount allowed and min profitability
    margin. Filter by segment (e.g. 'SME', 'Corporate') and/or product_id."""
    require_role("admin", "agent")
    audit_log("segment_pricing_benchmark", {"segment": segment, "product_id": product_id})
    logger.info("[tool] segment_pricing_benchmark | %r %r", segment, product_id)
    return _to_json(query_segment_pricing_benchmark(segment, product_id))


@mcp.tool()
def operations_cost_impact(product_id: str = "", customer_segment: str = "") -> str:
    """Operational cost impact on pricing: ops_cost_margin_pct and cost
    breakdown (onboarding, monthly servicing, exception handling) per
    product x customer segment combination."""
    require_role("admin", "agent")
    audit_log("operations_cost_impact", {"product_id": product_id, "customer_segment": customer_segment})
    logger.info("[tool] operations_cost_impact | %r %r", product_id, customer_segment)
    return _to_json(query_operations_cost_impact(product_id, customer_segment))


@mcp.tool()
def policy_exception(customer_id: str = "", deal_id: str = "") -> str:
    """Policy compliance per deal: lists exceptions with reasons —
    margin_below_min, price_below_floor, discount_exceeds_policy,
    competitor_match_requires_approval, high_rwa. Also shows whether the
    deal is overall policy_compliant. Filter by customer_id and/or deal_id."""
    require_role("admin")
    audit_log("policy_exception", {"customer_id": customer_id, "deal_id": deal_id})
    logger.info("[tool] policy_exception | %r %r", customer_id, deal_id)
    return _to_json(query_policy_exception(customer_id, deal_id))


@mcp.tool()
def non_compliant_deals(customer_id: str = "") -> str:
    """Only deals that breach at least one pricing policy rule
    (is_exception = true). Returns deal_id, customer_id, exception reasons
    and the policy fields that were violated.
    Filter by customer_id or pass empty string for all non-compliant deals."""
    require_role("admin")
    audit_log("non_compliant_deals", {"customer_id": customer_id})
    logger.info("[tool] non_compliant_deals | customer_id=%r", customer_id)
    return _to_json(query_non_compliant_deals(customer_id))


@mcp.tool()
def compare_fab_vs_competitor(customer_id: str = "", deal_id: str = "") -> str:
    """Direct comparison of FAB recommended/approved price vs competitor offer,
    with competitor_gap_bps and a suggested pricing action
    (MATCH / COUNTER / ESCALATE / REJECT).
    Filter by customer_id and/or deal_id or pass empty strings for all."""
    require_role("admin", "agent")
    audit_log("compare_fab_vs_competitor", {"customer_id": customer_id, "deal_id": deal_id})
    logger.info("[tool] compare_fab_vs_competitor | %r %r", customer_id, deal_id)
    return _to_json(query_compare_fab_vs_competitor(customer_id, deal_id))


# ---------------------------------------------------------------------------
# Prompts — reusable analysis workflow templates
# Agents call session.list_prompts() to discover these, then
# session.get_prompt(name, args) to get a structured message list that
# defines the analysis task. The agent's ReAct loop then uses the available
# tools to fill in each step.
# ---------------------------------------------------------------------------

@mcp.prompt()
def analyze_deal_pricing(customer_id: str, deal_id: str = "") -> str:
    """Comprehensive pricing analysis for a FAB customer or deal.

    Returns a structured 5-step workflow covering recommendation, step-by-step
    price build, competitor positioning, policy compliance, and a final
    pricing recommendation with justification.

    Args:
        customer_id: FAB customer ID (e.g. 'CUST001')
        deal_id:     Optional deal ID to focus on a specific deal (e.g. 'DEAL003')
    """
    focus = f" and deal {deal_id}" if deal_id else ""
    return (
        f"Perform a comprehensive pricing analysis for customer {customer_id}{focus}.\n\n"
        "Work through ALL of the following steps in order, calling the relevant tools "
        "for each step before moving to the next:\n\n"
        "**Step 1 — Pricing Recommendation**\n"
        "Call pricing_recommendation to get the current recommended price, approved "
        "price, expected margin, and compliance flags "
        "(price_below_policy_floor, margin_below_min, discount_exceeds_policy).\n\n"
        "**Step 2 — Pricing Trace**\n"
        "Call pricing_trace to show the step-by-step build-up: "
        "treasury rate → target margin → risk premium → ops cost → "
        "relationship discount → final recommended price. Include the explanation "
        "text for each component.\n\n"
        "**Step 3 — Competitor Comparison**\n"
        "Call competitor_price_analysis to compare FAB's price with the competitor "
        "offer. Report the competitor_gap_bps and the suggested action "
        "(MATCH / COUNTER / ESCALATE / REJECT) with its reasoning.\n\n"
        "**Step 4 — Policy Compliance**\n"
        "Call policy_exception to check for any policy breaches on this deal. "
        "List each exception reason and whether senior approval is required.\n\n"
        "**Step 5 — Summary & Recommendation**\n"
        "Synthesise the findings from steps 1–4 into a clear pricing recommendation: "
        "state the suggested final price, the justification, and any escalation actions "
        "required. Flag any risks."
    )


@mcp.prompt()
def review_policy_exceptions(customer_id: str = "") -> str:
    """Policy exception review workflow for compliance officers.

    Returns a structured 4-step workflow to audit non-compliant deals,
    understand exception reasons, assess risk, and recommend remediation.
    Requires admin role.

    Args:
        customer_id: Optional — scope the review to one customer. Leave empty for all.
    """
    scope = f"customer {customer_id}" if customer_id else "all customers"
    return (
        f"Conduct a policy exception review for {scope}.\n\n"
        "Work through each step using the available tools:\n\n"
        "**Step 1 — Non-Compliant Deals**\n"
        "Call non_compliant_deals to list all deals that breach at least one pricing "
        "policy rule. Record the deal IDs and exception counts.\n\n"
        "**Step 2 — Exception Details**\n"
        "For each non-compliant deal, call policy_exception to retrieve the specific "
        "exception reasons (margin_below_min, price_below_floor, "
        "discount_exceeds_policy, competitor_match_requires_approval, high_rwa). "
        "Note which combinations of exceptions appear most frequently.\n\n"
        "**Step 3 — Risk Assessment**\n"
        "Rank the exceptions by severity. Highest risk = multiple simultaneous breaches "
        "OR deals with high_rwa flag. Identify which deals require immediate VP "
        "or Capital Committee review.\n\n"
        "**Step 4 — Recommended Actions**\n"
        "For each exception cluster, recommend one of:\n"
        "- ESCALATE TO VP: price breaches policy floor but deal is strategically important\n"
        "- RENEGOTIATE: margin gap is small and customer may accept a higher price\n"
        "- REJECT: cost of funds breach — deal cannot be booked at current terms\n"
        "Provide the exception summary table and action plan."
    )


@mcp.prompt()
def pricing_competitor_strategy(customer_id: str = "", deal_id: str = "") -> str:
    """Competitive pricing strategy analysis for deal negotiation.

    Returns a structured 5-step workflow to determine FAB's optimal pricing
    response to a competitive situation, including a concrete price suggestion.

    Args:
        customer_id: FAB customer ID (e.g. 'CUST003')
        deal_id:     Specific deal under competitive pressure (e.g. 'DEAL007')
    """
    parts = []
    if customer_id:
        parts.append(f"customer {customer_id}")
    if deal_id:
        parts.append(f"deal {deal_id}")
    scope = " and ".join(parts) if parts else "all available deals"
    return (
        f"Analyse FAB's competitive pricing position for {scope} "
        "and determine the optimal pricing response.\n\n"
        "Follow these steps using the available tools:\n\n"
        "**Step 1 — Competitor Gap Analysis**\n"
        "Call competitor_price_analysis to get the competitor_gap_bps and the "
        "system-suggested action (MATCH / COUNTER / ESCALATE / REJECT). "
        "A positive gap means FAB is more expensive than the competitor.\n\n"
        "**Step 2 — Segment Benchmark Check**\n"
        "Call segment_pricing_benchmark to verify what the target margin and "
        "policy floor are for this customer's segment and product. "
        "This defines the lowest price FAB can offer without a policy breach.\n\n"
        "**Step 3 — Relationship Discount Eligibility**\n"
        "Call relationship_discount to check if this customer qualifies for a "
        "relationship discount and how much headroom is available. "
        "Applying a discount may close the competitive gap without breaching the floor.\n\n"
        "**Step 4 — Operations Cost Validation**\n"
        "Call operations_cost_impact to confirm the ops cost loading for this "
        "product x segment combination. This is part of the minimum price calculation.\n\n"
        "**Step 5 — Final Pricing Strategy**\n"
        "Based on steps 1–4, state:\n"
        "- The recommended competitive response (MATCH / COUNTER / ESCALATE / REJECT)\n"
        "- A concrete suggested price (in basis points or percentage)\n"
        "- Whether any policy approval is required\n"
        "- The expected margin at the suggested price vs the policy floor"
    )


# ---------------------------------------------------------------------------
# Resources — reference documents exposed as MCP-addressable URIs
# Agents call session.list_resources() to discover these, then
# session.read_resource(uri) to fetch the content. Static resources return
# policy and guide text; dynamic resources query fab_semantic at read time.
# ---------------------------------------------------------------------------

@mcp.resource("pricing://policy/rules")
def get_pricing_policy_rules() -> str:
    """FAB pricing policy rules — margin floors, discount caps, approval thresholds.

    Static reference document. No database access. Read by agents to interpret
    tool results and validate whether a deal requires escalation.
    """
    return """\
# FAB Pricing Policy Rules

## 1. Margin Floors
- **Minimum net margin**: 1.5% for all product types
- `price_below_policy_floor = true` → VP approval required before booking
- `margin_below_min = true` → pricing review required before booking

## 2. Relationship Discounts
- Maximum discount varies by segment — use the segment_pricing_benchmark tool
- `discount_exceeds_policy = true` → Senior Relationship Manager sign-off required
- Discount caps are enforced per-product (not per-relationship overall)

## 3. Competitor Response Thresholds
- **MATCH**:   competitor price is above policy floor → no additional approval
- **COUNTER**: counter-offer between recommended and competitor → RM approval
- **ESCALATE**: matching would breach the policy floor → VP approval required
- **REJECT**:  competitor price is below FAB's cost of funds → cannot match

## 4. RWA (Risk-Weighted Asset) Rules
- `high_rwa = true` → Capital Committee review required
- Minimum Return on RWA (RORWA): 8% for standard deals, 6% with VP waiver

## 5. Exception Handling Timeline
- `is_exception = true` deals must be resolved within 48 h of deal creation
- Combined exceptions (margin_below_min + price_below_floor) require both RM and VP
- Exceptions are tracked in the policy_exception_view

## 6. New Customer Pricing
- No relationship history → new_customer_pricing model applies
- Additional buffer: +0.5% to +1.0% above segment target margin
- Relationship discount not available until 12 months of booking history

*Reference: FAB Commercial Banking Pricing Policy v4.2 (2026-Q2)*
"""


@mcp.resource("pricing://guide/competitor-actions")
def get_competitor_action_guide() -> str:
    """Guide to FAB's four competitor pricing actions with decision criteria.

    Use this to interpret the suggested_action field returned by the
    competitor_price_analysis tool. Each action has specific approval requirements.
    """
    return """\
# FAB Competitor Pricing Action Guide

## Reading competitor_gap_bps
- **Positive** value (e.g. +150 bps): FAB is 1.5% MORE EXPENSIVE than the competitor
  → higher risk of losing the deal; consider MATCH or COUNTER
- **Negative** value (e.g. -60 bps): FAB is 0.6% CHEAPER than the competitor
  → FAB is competitive; maintain pricing unless margin headroom allows improvement
- **Zero**: FAB and competitor are identically priced

## MATCH — Accept competitor price
- **When**: competitor price ≥ FAB policy floor AND ≥ cost of funds
- **Approval**: Standard Relationship Manager approval
- **Risk**: margin compression vs FAB recommended; monitor portfolio impact
- **Example**: FAB recommended 3.8%, competitor 3.2%, policy floor 3.0% → MATCH at 3.2%

## COUNTER — Offer an intermediate price
- **When**: competitor is below recommended but above the policy floor
- **Approval**: RM approval; note the counter price rationale in the deal record
- **Strategy**: aim for mid-point between recommended and competitor prices
- **Example**: Recommended 4.2%, competitor 3.5%, floor 3.0% → COUNTER at 3.85%

## ESCALATE — Request a policy exception
- **When**: matching the competitor requires going below the policy floor
- **Approval**: VP (VP Commercial Banking) + Capital Committee if high_rwa = true
- **Process**: submit policy exception request via the exception workflow
- **Example**: Policy floor 3.5%, competitor 2.8% → ESCALATE to VP

## REJECT — Decline at competitor's terms
- **When**: competitor price is below FAB's cost of funds
- **Rationale**: booking at this price creates a guaranteed loss
- **Alternatives**: negotiate non-price terms (tenor, covenants, fee structure)
- **Example**: Cost of funds 4.2%, competitor offers 3.8% → REJECT; explore fees

*Reference: FAB Commercial Pricing Committee — Competitor Response Framework 2025-v3*
"""


@mcp.resource("pricing://benchmarks/segments")
def get_segment_benchmarks() -> str:
    """Live segment pricing benchmarks from fab_semantic.segment_pricing_benchmark.

    Queries the database at read time. Returns a markdown table of target margin,
    policy floor, new-customer buffer, max discount, and minimum profitability
    threshold for each segment x product combination.
    """
    audit_log("resource_segment_benchmarks", {})
    try:
        rows = query_segment_pricing_benchmark()
        if not rows or (len(rows) == 1 and "message" in rows[0]):
            return (
                "# FAB Segment Pricing Benchmarks\n\n"
                "No benchmark data available. Ensure fab_semantic is seeded.\n"
            )
        lines = [
            "# FAB Segment Pricing Benchmarks (Live)",
            f"*{len(rows)} benchmark(s) loaded from fab_semantic.segment_pricing_benchmark*",
            "",
            "| Segment | Product | Target Margin % | Floor % | New-Cust Buffer % | Max Discount % | Min Profit % |",
            "|---------|---------|----------------|---------|-------------------|----------------|--------------|",
        ]
        for row in rows:
            lines.append(
                f"| {row.get('customer_segment', '')} "
                f"| {row.get('product_id', '')} "
                f"| {row.get('target_margin_pct', 'N/A')} "
                f"| {row.get('base_margin_floor_pct', 'N/A')} "
                f"| {row.get('new_customer_buffer_pct', 'N/A')} "
                f"| {row.get('max_relationship_discount_pct', 'N/A')} "
                f"| {row.get('min_profitability_margin_pct', 'N/A')} |"
            )
        return "\n".join(lines)
    except Exception as exc:
        logger.error("Resource segment_benchmarks error: %s", exc)
        return f"# FAB Segment Pricing Benchmarks\n\nError loading data: {exc}\n"


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    transport = os.getenv("MCP_TRANSPORT", "stdio").lower()
    if transport in ("http", "streamable-http"):
        host      = os.getenv("MCP_HOST", "127.0.0.1")
        port      = int(os.getenv("MCP_PORT", "9200"))
        server_id = os.getenv("MCP_SERVER_ID", "fab-pricing-server")
        auth_msg  = f"JWTVerifier (aud={server_id})" if MCP_AUTH_ENABLED else "disabled"
        logger.info(
            "Starting FAB Pricing Engine MCP Server on %s:%s  auth=%s",
            host, port, auth_msg,
        )
        # claims_middleware() extracts JWT claims into ContextVar for require_role()
        app = mcp.http_app(middleware=claims_middleware())
        uvicorn.run(app, host=host, port=port, log_level="warning")
    else:
        logger.info("Starting FAB Pricing Engine MCP Server (stdio)")
        mcp.run()
