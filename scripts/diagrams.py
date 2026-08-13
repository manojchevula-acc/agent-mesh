"""
scripts/diagrams.py  —  v2
---------------------------
Generates all architecture diagrams as high-resolution PNG files.
Called by generate_design_doc.py — safe to run standalone too.

v2 fixes applied:
  D1: Layer labels moved inside bands to prevent clipping.
  D2: REWRITTEN — phase bands had swapped ybot/ytop producing near-zero heights;
      rotation=90 labels replaced with horizontal top-of-band labels;
      canvas expanded to 20×16; dead step_label code removed.
  D3: Arrow/text spacing adjusted to avoid crowding.
  D4: Added missing Probe→Active return arrow; bidirectional Disable/Re-enable
      arrows separated above/below centre-line.
  D5: Row header column given light-background styling.
  D6: REWRITTEN — canvas expanded to 16×10; all box widths increased;
      audit_log sublabel shortened; "No" label font now matches "Yes".
"""

import math
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.patches as mpatches
from matplotlib.patches import FancyBboxPatch
import numpy as np

OUT_DIR = Path(__file__).parent / "diagrams_out"
OUT_DIR.mkdir(exist_ok=True)

# ---------------------------------------------------------------------------
# Shared style constants
# ---------------------------------------------------------------------------
FONT = "DejaVu Sans"

P = {
    "navy":    "#1F3864",
    "blue":    "#2672C4",
    "dkblue":  "#1B4F8A",
    "teal":    "#006B6B",
    "green":   "#1E6B30",
    "ltgreen": "#E7F3EC",
    "amber":   "#C47A00",
    "red":     "#9B1C1C",
    "purple":  "#4B0082",
    "ltblue":  "#DDEAF7",
    "ltgrey":  "#F5F5F5",
    "midgrey": "#888888",
    "white":   "#FFFFFF",
    "black":   "#1A1A2E",
    "ph1":     "#1B4F8A",
    "ph2":     "#006B6B",
    "ph3":     "#4B0082",
    "ph4":     "#1E6B30",
    "ph5":     "#C47A00",
}


def _save(fig, name, dpi=180):
    path = OUT_DIR / f"{name}.png"
    fig.savefig(str(path), dpi=dpi, bbox_inches="tight",
                facecolor=fig.get_facecolor())
    plt.close(fig)
    return path


def _box(ax, cx, cy, w, h, label, sublabel="", bg="#2672C4", fg="#FFFFFF",
         fontsize=9, radius=0.04, bold=True, sublabel_size=7.5, zorder=3):
    """Rounded rectangle centred at (cx, cy) with optional sub-label."""
    box = FancyBboxPatch((cx - w / 2, cy - h / 2), w, h,
                         boxstyle=f"round,pad=0.01,rounding_size={radius}",
                         linewidth=0.8, edgecolor="#FFFFFF",
                         facecolor=bg, zorder=zorder)
    ax.add_patch(box)
    weight = "bold" if bold else "normal"
    if sublabel:
        ax.text(cx, cy + h * 0.13, label, ha="center", va="center",
                fontsize=fontsize, fontweight=weight, color=fg,
                fontfamily=FONT, zorder=zorder + 1)
        ax.text(cx, cy - h * 0.22, sublabel, ha="center", va="center",
                fontsize=sublabel_size, color=fg, alpha=0.88,
                fontfamily=FONT, zorder=zorder + 1)
    else:
        ax.text(cx, cy, label, ha="center", va="center",
                fontsize=fontsize, fontweight=weight, color=fg,
                fontfamily=FONT, zorder=zorder + 1)


def _arrow(ax, x1, y1, x2, y2, label="", color="#1F3864",
           lw=1.4, style="->", label_side="top", fontsize=7.5):
    """Annotated straight arrow from (x1,y1) to (x2,y2)."""
    ax.annotate("", xy=(x2, y2), xytext=(x1, y1),
                arrowprops=dict(arrowstyle=style, color=color,
                                lw=lw, connectionstyle="arc3,rad=0.0"),
                zorder=5)
    if label:
        mx, my = (x1 + x2) / 2, (y1 + y2) / 2
        dy = 0.03 if label_side == "top" else -0.03
        ax.text(mx, my + dy, label, ha="center", va="center",
                fontsize=fontsize, color=color, fontfamily=FONT,
                fontstyle="italic", zorder=6,
                bbox=dict(boxstyle="round,pad=0.15", fc="white",
                          ec="none", alpha=0.88))


def _shield(ax, x, y, r=0.045, color="#1E6B30"):
    t = np.linspace(0, math.pi, 40)
    xs = x + r * np.cos(t)
    ys = y + r * np.sin(t)
    xs = np.append(xs, [x + r * 0.5, x, x - r * 0.5, x - r, xs[0]])
    ys = np.append(ys, [y - r * 0.6, y - r * 1.1, y - r * 0.6, ys[-1], ys[0]])
    ax.fill(xs, ys, color=color, zorder=6, alpha=0.9)


def _lock(ax, x, y, s=0.03, color="#C47A00"):
    body = FancyBboxPatch((x - s, y - s * 0.8), s * 2, s * 1.4,
                          boxstyle="round,pad=0.005",
                          fc=color, ec="none", zorder=6)
    ax.add_patch(body)
    arc = mpatches.Arc((x, y + s * 0.6), s * 1.2, s * 1.2,
                       angle=0, theta1=0, theta2=180,
                       color=color, lw=2.5, zorder=6)
    ax.add_patch(arc)


# ============================================================================
# DIAGRAM 1 — System Architecture Overview
# ============================================================================
def d1_system_overview():
    fig, ax = plt.subplots(figsize=(16, 10))
    fig.patch.set_facecolor("#F8FAFF")
    ax.set_facecolor("#F8FAFF")
    ax.set_xlim(0, 16)
    ax.set_ylim(0, 10)
    ax.axis("off")

    ax.text(8, 9.65, "MCP Hub — System Architecture Overview",
            ha="center", va="center", fontsize=15, fontweight="bold",
            color=P["navy"], fontfamily=FONT)

    # ── Layer backgrounds ────────────────────────────────────────────────────
    layers = [
        (0.2, 8.30, 15.6, 1.25, "PRESENTATION LAYER — Browser",           "#EFF4FC"),
        (0.2, 5.90, 15.6, 2.15, "APPLICATION LAYER — Services",           "#F0F7F0"),
        (0.2, 3.55, 15.6, 2.10, "ORCHESTRATION LAYER — Agent",            "#F5F0FF"),
        (0.2, 0.90, 15.6, 2.40, "DATA & INTEGRATION LAYER — MCP Servers + DB", "#FFF8EE"),
    ]
    for (x, y, w, h, lbl, bg) in layers:
        rect = FancyBboxPatch((x, y), w, h,
                              boxstyle="round,pad=0.05,rounding_size=0.12",
                              linewidth=0.6, edgecolor="#CCCCCC",
                              facecolor=bg, zorder=1)
        ax.add_patch(rect)
        # label inside the band, top-left corner
        ax.text(x + 0.20, y + h - 0.15, lbl,
                ha="left", va="top", fontsize=6.5, color="#777777",
                fontfamily=FONT, fontstyle="italic", zorder=2)

    # ── Presentation ─────────────────────────────────────────────────────────
    _box(ax, 5.0, 8.82, 3.8, 0.70, "Chat UI  (SPA)",
         "Query · SSE stream · Conversation history", bg=P["dkblue"])
    _box(ax, 11.2, 8.82, 3.8, 0.70, "Admin UI  (SPA)",
         "Server registry · Logs · Health · CRUD", bg=P["dkblue"])

    # ── Application ──────────────────────────────────────────────────────────
    _box(ax, 4.0, 6.82, 3.4, 1.60, "Chat Server  :8080",
         "User auth (PBKDF2) · Rate limiting\nSSE streaming · Background tasks\nConversation persistence",
         bg=P["teal"], fontsize=8.5, sublabel_size=7)
    _box(ax, 12.0, 6.82, 3.4, 1.60, "MCP Hub Server  :8090",
         "Server registry · JWT issuance\nLLM routing · Admin REST API\nJWKS publication · Observability",
         bg=P["navy"], fontsize=8.5, sublabel_size=7)

    # ── Orchestration ────────────────────────────────────────────────────────
    _box(ax, 7.8, 4.54, 4.4, 1.55, "Agent Orchestrator",
         "POST /discover · mcp_session()\nLangGraph ReAct loop\nTool loading · astream_events",
         bg=P["purple"], fontsize=8.5, sublabel_size=7)
    _box(ax, 13.5, 4.54, 3.6, 1.55, "Database  (MySQL)",
         "mcp_servers — registry\nhub_events — event log\nconversations · users",
         bg="#5C3D11", fontsize=8.5, sublabel_size=7)

    # ── Data / Integration ────────────────────────────────────────────────────
    mcp_nodes = [
        (2.3,  "Server A\n:9100"),
        (5.2,  "Server B\n:9200"),
        (8.1,  "Server C\n:9300"),
        (11.0, "Server D\n:9400"),
    ]
    for xp, lbl in mcp_nodes:
        _box(ax, xp, 2.10, 2.5, 1.55, lbl,
             "JWTVerifier · RBAC\nStreamable HTTP\nTools · Resources · Prompts",
             bg=P["green"], fontsize=8, sublabel_size=6.8)
    _box(ax, 14.2, 2.10, 2.8, 1.55, "MySQL / Postgres",
         "fab_semantic schema\n16 semantic views\n14 base tables",
         bg="#5C3D11", fontsize=8.5, sublabel_size=7)

    # ── Arrows ───────────────────────────────────────────────────────────────
    _arrow(ax, 5.0, 8.47, 4.3, 7.62, "SSE + POST /messages", color=P["teal"])
    _arrow(ax, 11.2, 8.47, 11.8, 7.62, "REST (hub JWT)", color=P["navy"])
    _arrow(ax, 5.5, 6.02, 6.8, 5.31, "run_agent()", color=P["purple"])
    _arrow(ax, 12.0, 6.02, 13.2, 5.31, "SQL registry", color="#5C3D11")
    _arrow(ax, 9.1, 5.31, 10.7, 6.02, "POST /discover (hub JWT)", color=P["navy"])
    for xp in [2.3, 5.2, 8.1, 11.0]:
        _arrow(ax, 7.8, 3.77, xp, 2.87,
               "MCP + JWT" if xp == 5.2 else "", color=P["green"])
    _arrow(ax, 11.0, 2.87, 12.7, 2.0, "SQL (own creds)", color="#5C3D11", fontsize=6.5)

    # ── Legend ───────────────────────────────────────────────────────────────
    legend = [
        (P["teal"],   "Chat Server"),
        (P["navy"],   "Hub Server"),
        (P["purple"], "Agent Orchestrator"),
        (P["green"],  "MCP Servers"),
        ("#5C3D11",   "Database"),
    ]
    for i, (clr, lbl) in enumerate(legend):
        bx = 0.5 + i * 3.1
        rect = FancyBboxPatch((bx, 0.08), 0.26, 0.26,
                              boxstyle="round,pad=0.02",
                              fc=clr, ec="none", zorder=3)
        ax.add_patch(rect)
        ax.text(bx + 0.38, 0.21, lbl, va="center", fontsize=7.5,
                color="#333333", fontfamily=FONT)

    return _save(fig, "01_system_overview", dpi=200)


# ============================================================================
# DIAGRAM 2 — End-to-End Authentication Flow (Swimlane)
#
# v2 REWRITE:
#   - Canvas 20×16 (was 18×13)
#   - Phase bands now use correct ymin/ymax spanning the actual message range
#   - Phase labels are horizontal text at the TOP of each band (no rotation)
#   - Activation bars added per lifeline
#   - All callout boxes anchored inside their bands with connector lines
# ============================================================================
def d2_auth_flow():
    W, H = 20, 16
    fig, ax = plt.subplots(figsize=(W, H))
    fig.patch.set_facecolor("#FAFBFF")
    ax.set_facecolor("#FAFBFF")
    ax.set_xlim(0, W)
    ax.set_ylim(0, H)
    ax.axis("off")

    ax.text(W / 2, H - 0.3, "End-to-End Authentication Flow — Five Phases",
            ha="center", va="center", fontsize=15, fontweight="bold",
            color=P["navy"], fontfamily=FONT)

    # ── Swimlane columns ─────────────────────────────────────────────────────
    # Each lane: (centre_x, label, bg_color)
    lanes = [
        (1.7,  "User /\nBrowser",     "#EFF4FC"),
        (4.7,  "Chat\nServer",        "#E7F3F3"),
        (8.0,  "Hub\nServer",         "#EDF2FB"),
        (11.5, "Agent\nOrchestrator", "#F5F0FF"),
        (15.0, "MCP\nServer",         "#E7F3EC"),
        (18.6, "Database",            "#FFF8EE"),
    ]
    # Draw lane columns from y=0.5 to y=15.2
    COL_TOP, COL_BOT = 15.2, 0.5
    for lx, lbl, bg in lanes:
        rect = FancyBboxPatch((lx - 1.35, COL_BOT), 2.7, COL_TOP - COL_BOT,
                              boxstyle="square,pad=0",
                              linewidth=0.4, edgecolor="#CCCCCC",
                              facecolor=bg, alpha=0.55, zorder=0)
        ax.add_patch(rect)
        # Lifeline (dashed vertical)
        ax.plot([lx, lx], [COL_BOT, COL_TOP - 0.65],
                color="#CCCCCC", lw=0.8, linestyle="--", zorder=1)
        # Header
        ax.text(lx, COL_TOP - 0.25, lbl, ha="center", va="center",
                fontsize=9, fontweight="bold", color=P["navy"],
                fontfamily=FONT, zorder=2)
    # Right border
    ax.axvline(18.6 + 1.35, ymin=COL_BOT / H, ymax=COL_TOP / H,
               color="#CCCCCC", lw=0.4, zorder=0)

    # ── Phase bands ──────────────────────────────────────────────────────────
    # (label, ymin, ymax, color)  — ymax is visually higher on the canvas
    phase_bands = [
        ("PHASE 1 — User Login",                     12.5, 14.8, P["ph1"]),
        ("PHASE 2 — Hub API Auth  (agent → hub)",    10.5, 12.5, P["ph2"]),
        ("PHASE 3 — Server Discovery & JWT Minting",  7.5, 10.5, P["ph3"]),
        ("PHASE 4 — MCP Session & JWT Validation",    4.5,  7.5, P["ph4"]),
        ("PHASE 5 — Per-Tool RBAC & Execution",       0.6,  4.5, P["ph5"]),
    ]
    for lbl, ymin, ymax, clr in phase_bands:
        rect = FancyBboxPatch((0.25, ymin), W - 0.5, ymax - ymin,
                              boxstyle="round,pad=0.04,rounding_size=0.1",
                              linewidth=1.0, edgecolor=clr,
                              facecolor=clr, alpha=0.06, zorder=0.5)
        ax.add_patch(rect)
        # Horizontal label at top-left of band
        ax.text(0.45, ymax - 0.18, lbl,
                ha="left", va="top", fontsize=8, fontweight="bold",
                color=clr, fontfamily=FONT, zorder=1)

    # ── Activation bars ──────────────────────────────────────────────────────
    def act(lx, ybot, ytop, clr):
        r = FancyBboxPatch((lx - 0.13, ybot), 0.26, ytop - ybot,
                           boxstyle="square,pad=0",
                           linewidth=0, facecolor=clr, alpha=0.30, zorder=2)
        ax.add_patch(r)

    # ── Message arrow helper ──────────────────────────────────────────────────
    def msg(x1, x2, y, label, clr, dashed=False, note=None, lw=1.3):
        ls = "--" if dashed else "-"
        arrowstyle = "<-" if x2 < x1 else "->"
        ax.annotate("", xy=(x2, y), xytext=(x1, y),
                    arrowprops=dict(arrowstyle=arrowstyle,
                                   color=clr, lw=lw, linestyle=ls),
                    zorder=4)
        ax.text((x1 + x2) / 2, y + 0.14, label,
                ha="center", va="bottom", fontsize=7.2, color=clr,
                fontfamily=FONT,
                bbox=dict(boxstyle="round,pad=0.12", fc="white",
                          ec="none", alpha=0.92), zorder=5)
        if note:
            ax.text((x1 + x2) / 2, y - 0.12, note,
                    ha="center", va="top", fontsize=6.2, color="#777777",
                    fontfamily=FONT, fontstyle="italic", zorder=5)

    # ── Callout box (anchored to a lifeline) ─────────────────────────────────
    def callout(lx, cy, w, h, lines, clr):
        bx = lx + 0.5 if lx < W / 2 else lx - 0.5 - w
        rect = FancyBboxPatch((bx, cy - h / 2), w, h,
                              boxstyle="round,pad=0.08,rounding_size=0.08",
                              linewidth=0.9, edgecolor=clr,
                              facecolor="#FFFFFF", alpha=0.97, zorder=6)
        ax.add_patch(rect)
        for i, line in enumerate(lines):
            ax.text(bx + 0.12, cy + h / 2 - 0.18 - i * 0.22, line,
                    ha="left", va="top", fontsize=6.5, color=clr,
                    fontfamily=FONT, fontweight="bold", zorder=7)
        # Connector from lifeline to box
        bx_edge = bx if lx < W / 2 else bx + w
        ax.plot([lx, bx_edge], [cy, cy],
                color=clr, lw=0.7, linestyle=":", zorder=5)

    # ════════════════════════════════════════════════════════════════
    # PHASE 1 — User Login (y=12.5 to 14.8)
    # ════════════════════════════════════════════════════════════════
    act(1.7, 13.1, 14.5, P["ph1"])
    act(4.7, 13.1, 14.5, P["ph1"])

    msg(1.7, 4.7, 14.2, "POST /login  { username, password }", P["ph1"])
    msg(4.7, 1.7, 13.7, "PBKDF2-SHA256 verify  (200k iterations)", P["ph1"],
        dashed=True)
    msg(4.7, 1.7, 13.3, "Set-Cookie: session=<Hub JWT · 8 hours>", P["ph1"],
        note="JWT: iss=mcp-hub · aud=mcp-hub · sub=username · roles=[agent]")
    _lock(ax, 3.2, 13.5, s=0.04, color=P["amber"])

    # ════════════════════════════════════════════════════════════════
    # PHASE 2 — Agent → Hub  (y=10.5 to 12.5)
    # ════════════════════════════════════════════════════════════════
    act(11.5, 10.7, 12.2, P["ph2"])
    act(8.0,  10.7, 12.2, P["ph2"])

    msg(4.7, 11.5, 12.1, "run_agent(user_query)", P["ph2"])
    msg(11.5, 8.0, 11.7,
        "POST /discover   Authorization: Bearer <hub_jwt>", P["ph2"])
    msg(8.0, 11.5, 11.2,
        "Validate hub JWT: RS256 sig · iss · aud · exp · roles",
        P["ph2"], dashed=True,
        note="→ 401 Unauthorized if any check fails")

    # ════════════════════════════════════════════════════════════════
    # PHASE 3 — Discovery & JWT Minting  (y=7.5 to 10.5)
    # ════════════════════════════════════════════════════════════════
    act(8.0,  7.8, 10.5, P["ph3"])
    act(18.6, 7.8, 10.5, P["ph3"])

    msg(8.0, 18.6, 10.2, "Load mcp_servers  (60 s in-process cache)", P["ph3"],
        note="SELECT * FROM mcp_servers WHERE is_active=1")
    msg(18.6, 8.0, 9.8, "Server list returned", P["ph3"], dashed=True)

    # LLM routing box
    ax.text(8.0, 9.45,
            "LLM Routing Agent (ReAct):  THINK → CALL pick_server(id, reason) → OBSERVE",
            ha="center", va="center", fontsize=7, color=P["ph3"],
            fontfamily=FONT, style="italic",
            bbox=dict(boxstyle="round,pad=0.22", fc="#F5F0FF",
                      ec=P["ph3"], lw=0.8, alpha=0.95), zorder=6)

    msg(8.0, 11.5, 9.0,
        "Mint RS256 JWT per matched server  ·  aud=server_id  ·  exp=1h",
        P["ph3"], dashed=True,
        note="jwt.encode({iss, aud, sub, roles, exp}, private_key, RS256)")
    msg(11.5, 8.0, 8.3,
        "Response: [ { server_config, server_token }, … ]",
        P["ph3"],
        note="One audience-scoped token per server — cross-server replay impossible")

    # ════════════════════════════════════════════════════════════════
    # PHASE 4 — MCP Session & JWT Validation  (y=4.5 to 7.5)
    # ════════════════════════════════════════════════════════════════
    act(11.5, 4.7, 7.5, P["ph4"])
    act(15.0, 4.7, 7.5, P["ph4"])

    msg(11.5, 15.0, 7.1,
        "POST /mcp  (initialize)   Authorization: Bearer <server_token>",
        P["ph4"])

    # JWTVerifier callout
    callout(15.0, 6.55, 4.2, 1.20, [
        "JWTVerifier (FastMCP):",
        "  RS256 sig verified ✓",
        "  aud = server_id ✓",
        "  iss = mcp-hub ✓",
        "  exp not exceeded ✓",
    ], P["ph4"])
    _shield(ax, 14.6, 6.55, r=0.09, color=P["ph4"])

    msg(15.0, 11.5, 5.85, "Mcp-Session-Id: <uuid>", P["ph4"],
        dashed=True,
        note="BearerClaimsMiddleware → _request_claims ContextVar")
    msg(11.5, 15.0, 5.40,
        "POST /mcp  (tools/list)   Bearer: <server_token>", P["ph4"],
        note="JWT re-validated on EVERY request — not just at handshake")
    msg(15.0, 11.5, 5.00, "Tool list returned", P["ph4"], dashed=True)

    # ════════════════════════════════════════════════════════════════
    # PHASE 5 — Per-Tool RBAC & Execution  (y=0.6 to 4.5)
    # ════════════════════════════════════════════════════════════════
    act(15.0, 0.8, 4.5, P["ph5"])
    act(18.6, 0.8, 4.5, P["ph5"])

    msg(11.5, 15.0, 4.1,
        "tools/call   customer_lookup({ customer_id: 'CUST007' })",
        P["ph5"])

    # RBAC callout
    callout(15.0, 3.4, 4.2, 1.10, [
        "require_role('agent') ✓",
        "audit_log(tool, sub, roles, args_keys)",
        "SQL via MYSQL_USER / MYSQL_PASSWORD",
    ], P["ph5"])

    msg(15.0, 18.6, 2.65,
        "SELECT * FROM customer_360_view WHERE id=?", P["ph5"],
        note="Parameterised query · server's own DB credentials")
    msg(18.6, 15.0, 2.2, "Result rows", P["ph5"], dashed=True)
    msg(15.0, 11.5, 1.8, "Tool result JSON", P["ph5"], dashed=True)
    msg(11.5, 4.7, 1.4, "Final answer (SSE stream)", P["ph5"], dashed=True)
    msg(4.7, 1.7, 1.0, "Rendered in browser", P["ph5"], dashed=True)

    # ── Phase legend ──────────────────────────────────────────────────────────
    legend_phases = [
        (P["ph1"], "Phase 1: Login"),
        (P["ph2"], "Phase 2: Hub auth"),
        (P["ph3"], "Phase 3: Discovery + JWT"),
        (P["ph4"], "Phase 4: MCP validation"),
        (P["ph5"], "Phase 5: Tool RBAC"),
    ]
    for i, (clr, lbl) in enumerate(legend_phases):
        bx = 0.4 + i * 3.9
        ax.plot([bx, bx + 0.45], [0.22, 0.22], color=clr, lw=3.5)
        ax.text(bx + 0.6, 0.22, lbl, va="center", fontsize=7.5,
                color="#333333", fontfamily=FONT)

    return _save(fig, "02_auth_flow", dpi=200)


# ============================================================================
# DIAGRAM 3 — JWT Token Lifecycle & RSA Key Infrastructure
# ============================================================================
def d3_jwt_lifecycle():
    fig, ax = plt.subplots(figsize=(16, 8))
    fig.patch.set_facecolor("#FAFBFF")
    ax.set_facecolor("#FAFBFF")
    ax.set_xlim(0, 16)
    ax.set_ylim(0, 8)
    ax.axis("off")

    ax.text(8, 7.7, "JWT Token Lifecycle & RSA Key Infrastructure",
            ha="center", va="center", fontsize=13, fontweight="bold",
            color=P["navy"], fontfamily=FONT)

    # RSA key pair
    _box(ax, 3.0, 6.4, 4.5, 0.9, "RSA-2048 Key Pair  (Hub only)",
         "hub_service/.keys/private.pem  ·  hub_service/.keys/public.pem",
         bg=P["navy"], fontsize=9.5)

    # Private key
    _box(ax, 1.2, 4.6, 2.0, 0.75, "private.pem",
         "Signs ALL JWTs\nNever leaves hub",
         bg=P["red"], fontsize=8)
    _arrow(ax, 1.8, 5.95, 1.2, 4.97, color=P["red"])

    # JWKS endpoint
    _box(ax, 4.8, 4.6, 2.5, 0.75, "GET /.well-known\n/jwks.json",
         "RSA public key\n(JWK Set format)",
         bg=P["teal"], fontsize=8)
    _arrow(ax, 4.2, 5.95, 4.8, 4.97, color=P["teal"])

    # MCP server JWKS fetch
    _box(ax, 8.5, 4.6, 3.0, 0.75, "MCP Server (startup)",
         "PyJWKClient.get_signing_key()\nFetches & caches public key",
         bg=P["green"], fontsize=8)
    _arrow(ax, 6.05, 4.6, 7.0, 4.6, label="GET once on startup", color=P["teal"])

    # Hub JWT
    _box(ax, 2.5, 2.4, 3.8, 1.30, "Hub JWT  (8 hours)",
         "iss: mcp-hub\naud: mcp-hub\nsub: <username>\nroles: [agent | admin]",
         bg=P["dkblue"], fontsize=8.5, sublabel_size=7.5)
    _arrow(ax, 1.5, 4.22, 2.0, 3.05, label="sign", color=P["red"])
    ax.text(2.5, 1.55, "Used for: POST /discover · GET /servers · Admin UI",
            ha="center", va="center", fontsize=7.5, color=P["dkblue"],
            fontfamily=FONT, fontstyle="italic")

    # Per-server JWT
    _box(ax, 9.5, 2.4, 4.4, 1.30, "Per-Server JWT  (1 hour)",
         "iss: mcp-hub\naud: <server_id>  ← audience-scoped\nsub: <username>\nroles: [forwarded from user]",
         bg=P["purple"], fontsize=8.5, sublabel_size=7.5)
    _arrow(ax, 4.4, 2.4, 7.3, 2.4,
           label="/discover mints one token per matched server",
           color=P["purple"], fontsize=7)
    _arrow(ax, 1.5, 4.22, 7.5, 3.05, label="sign", color=P["red"])
    ax.text(9.5, 1.55,
            "Used for: MCP initialize · tools/list · tools/call  (re-validated every call)",
            ha="center", va="center", fontsize=7.5, color=P["purple"],
            fontfamily=FONT, fontstyle="italic")

    # JWTVerifier
    _box(ax, 13.8, 4.6, 2.6, 0.75, "JWTVerifier",
         "sig · aud · iss · exp\n→ 401 on any failure",
         bg=P["green"], fontsize=8)
    _arrow(ax, 11.7, 2.4, 13.5, 4.22,
           label="token on every\nMCP call", color=P["green"], fontsize=7)
    _arrow(ax, 11.0, 4.6, 12.5, 4.6,
           label="verifies via cached public key", color=P["green"], fontsize=7)

    # Footer rule
    rect = FancyBboxPatch((0.3, 0.15), 15.4, 0.65,
                          boxstyle="round,pad=0.05,rounding_size=0.08",
                          linewidth=1.0, edgecolor=P["amber"],
                          facecolor="#FFF8EE", zorder=2)
    ax.add_patch(rect)
    ax.text(8.0, 0.47,
            "Credential Isolation:  Hub JWT consumed at hub boundary  |  "
            "Per-server JWT consumed at MCP boundary  |  "
            "DB credentials never leave the MCP server",
            ha="center", va="center", fontsize=7.8, color=P["amber"],
            fontfamily=FONT, fontweight="bold", zorder=3)

    return _save(fig, "03_jwt_lifecycle", dpi=200)


# ============================================================================
# DIAGRAM 4 — Server Registration & Lifecycle
#
# v2 fixes:
#   - Probe→Active return arrow added
#   - Disable/Re-enable arrows separated to y±0.12 to avoid label overlap
# ============================================================================
def d4_server_lifecycle():
    fig, ax = plt.subplots(figsize=(15, 7))
    fig.patch.set_facecolor("#FAFBFF")
    ax.set_facecolor("#FAFBFF")
    ax.set_xlim(0, 15)
    ax.set_ylim(0, 7)
    ax.axis("off")

    ax.text(7.5, 6.7, "MCP Server Registration & Lifecycle Management",
            ha="center", va="center", fontsize=13, fontweight="bold",
            color=P["navy"], fontfamily=FONT)

    # ── Registration sources ──────────────────────────────────────────────────
    for sx, lbl, sub in [
        (1.5, "mcp-hub.json",  "Declarative file\n(seed script)"),
        (4.5, "Admin UI",      "Browser-based\nCRUD interface"),
        (7.5, "REST API",      "POST /api/hub/servers\n(CI/CD pipeline)"),
    ]:
        _box(ax, sx, 5.6, 2.4, 0.78, lbl, sub, bg=P["teal"], fontsize=9)

    # Hub Registry
    _box(ax, 4.5, 4.3, 8.0, 0.92, "Hub Registry  (MySQL — mcp_servers table)",
         "id · name · endpoint · transport · capability · skills\n"
         "api_key · api_key_expires · is_active · created_at · updated_at",
         bg=P["navy"], fontsize=9, sublabel_size=7.2)

    # Sources → Registry
    for sx in [1.5, 4.5, 7.5]:
        _arrow(ax, sx, 5.21, 4.5 + (sx - 4.5) * 0.08, 4.76, color=P["teal"])

    # ── States ───────────────────────────────────────────────────────────────
    # (cx, label, sublabel, color)
    states_cfg = [
        (2.0,  "Registered\n(Active)",  "is_active = 1",    P["green"]),
        (5.5,  "Disabled",              "is_active = 0",    P["amber"]),
        (9.0,  "Probe Pending",         "Health check\nrunning", P["purple"]),
        (12.5, "Deleted",               "Row removed\nfrom DB", P["red"]),
    ]
    STATE_Y = 2.5
    for cx, lbl, sub, clr in states_cfg:
        _box(ax, cx, STATE_Y, 2.8, 1.0, lbl, sub, bg=clr, fontsize=9, sublabel_size=7.5)

    # Registry → states
    for cx, *_ in states_cfg:
        _arrow(ax, 4.5, 3.84, cx, STATE_Y + 0.5, color="#888888")

    # ── State transitions ────────────────────────────────────────────────────
    # Active <-> Disabled (separated above/below centre line)
    _arrow(ax, 3.4, STATE_Y + 0.12, 4.15, STATE_Y + 0.12,
           label="Disable", color=P["amber"])
    _arrow(ax, 4.15, STATE_Y - 0.12, 3.4, STATE_Y - 0.12,
           label="Re-enable", color=P["green"])

    # Disabled → Probe
    _arrow(ax, 6.9, STATE_Y, 7.6, STATE_Y,
           label="Probe", color=P["purple"])

    # Probe → Active (return arc, curved slightly)
    ax.annotate("", xy=(2.0, STATE_Y + 0.50), xytext=(9.0, STATE_Y + 0.50),
                arrowprops=dict(arrowstyle="->", color=P["green"],
                                lw=1.3,
                                connectionstyle="arc3,rad=-0.35"),
                zorder=5)
    ax.text(5.5, STATE_Y + 1.10, "Probe → Active  (health OK)",
            ha="center", va="center", fontsize=7.2, color=P["green"],
            fontfamily=FONT, fontstyle="italic",
            bbox=dict(boxstyle="round,pad=0.14", fc="white",
                      ec="none", alpha=0.9))

    # Probe → DELETE
    _arrow(ax, 10.4, STATE_Y, 11.1, STATE_Y, label="DELETE", color=P["red"])

    # ── 60 s cache ────────────────────────────────────────────────────────────
    _box(ax, 12.0, 4.3, 3.2, 0.92, "60 s In-Process Cache",
         "load_hub() caches registry\nPOST /api/hub/refresh busts\nimmediately",
         bg=P["midgrey"], fontsize=9, sublabel_size=7.2)
    _arrow(ax, 8.5, 4.3, 10.4, 4.3, label="cached 60 s", color=P["midgrey"])

    # ── LLM Routing ──────────────────────────────────────────────────────────
    _box(ax, 4.5, 0.88, 7.0, 0.92, "LLM Routing Agent  (LangGraph ReAct)",
         "Server list + user query → pick_server(id, reason)  ·  New instance per request",
         bg=P["purple"], fontsize=9, sublabel_size=7.2)
    _arrow(ax, 4.5, 1.84, 4.5, 2.0, label="feeds routing", color=P["purple"])
    _arrow(ax, 12.0, 3.84, 12.0, 2.0, color=P["midgrey"])
    _arrow(ax, 12.0, 2.0, 8.0, 1.34, label="served from cache", color=P["midgrey"])

    return _save(fig, "04_server_lifecycle", dpi=200)


# ============================================================================
# DIAGRAM 5 — Credential Isolation & Security Model
# ============================================================================
def d5_credential_isolation():
    fig, ax = plt.subplots(figsize=(16, 8))
    fig.patch.set_facecolor("#FAFBFF")
    ax.set_facecolor("#FAFBFF")
    ax.set_xlim(0, 16)
    ax.set_ylim(0, 8)
    ax.axis("off")

    ax.text(8, 7.7, "Credential Isolation Model",
            ha="center", va="center", fontsize=13, fontweight="bold",
            color=P["navy"], fontfamily=FONT)

    # ── Column headers ────────────────────────────────────────────────────────
    col_hdrs = [
        (2.0,  "Layer / Boundary"),
        (6.5,  "Credential"),
        (10.8, "Implementation Detail"),
        (14.8, "Boundary Rule"),
    ]
    for x, lbl in col_hdrs:
        ax.text(x, 7.25, lbl, ha="center", va="center",
                fontsize=9.5, fontweight="bold", color=P["navy"],
                fontfamily=FONT)
    ax.axhline(7.05, xmin=0.02, xmax=0.98, color=P["navy"], lw=0.9)

    # ── Rows ─────────────────────────────────────────────────────────────────
    rows = [
        (6.55, "Browser → Chat Server",
         "Username + Password",
         "PBKDF2-SHA256\n200,000 iterations · unique salt",
         P["ph1"]),
        (5.35, "Agent → Hub API",
         "Hub JWT  (8 hours)",
         "RS256 · iss=hub · aud=hub\nsub=username · roles",
         P["ph2"]),
        (4.15, "Hub → MCP Server  (/discover)",
         "Per-Server JWT  (1 hour)",
         "RS256 · iss=hub · aud=server_id\n1 token per server",
         P["ph3"]),
        (2.95, "MCP Server → Database",
         "MYSQL_USER + MYSQL_PASSWORD",
         "Stored in .env · pool_recycle 30 min\nNever forwarded upstream",
         P["ph4"]),
        (1.75, "MCP Server → External APIs",
         "MCP_TOOL_KEY",
         "Per-server API key\nservice_auth_headers() · not forwarded",
         P["ph5"]),
    ]

    for i, (row_y, layer_lbl, cred_lbl, detail, clr) in enumerate(rows):
        row_bg = "#F8F8F8" if i % 2 == 0 else "#FFFFFF"
        # Row background
        rect = FancyBboxPatch((0.3, row_y - 0.50), 15.4, 1.0,
                              boxstyle="round,pad=0.04,rounding_size=0.06",
                              linewidth=0.5, edgecolor="#DDDDDD",
                              facecolor=row_bg, zorder=0)
        ax.add_patch(rect)
        # Left column: layer label (styled with colour bar)
        bar = FancyBboxPatch((0.3, row_y - 0.50), 0.18, 1.0,
                             boxstyle="square,pad=0",
                             fc=clr, ec="none", zorder=1)
        ax.add_patch(bar)
        ax.text(2.0, row_y, layer_lbl, ha="center", va="center",
                fontsize=8.2, color=P["black"], fontfamily=FONT,
                fontstyle="italic", zorder=2)
        # Credential box
        _box(ax, 6.5, row_y, 3.4, 0.78, cred_lbl, "",
             bg=clr, fontsize=8.5, sublabel_size=7, zorder=2)
        # Detail box
        _box(ax, 10.8, row_y, 3.8, 0.78, detail, "",
             bg="#F2F2F2", fg="#333333", fontsize=7.5, bold=False, zorder=2)
        # Boundary rule badge
        ax.text(14.8, row_y, "isolated", ha="center", va="center",
                fontsize=7.5, color=P["red"], fontweight="bold",
                fontfamily=FONT, zorder=3,
                bbox=dict(boxstyle="round,pad=0.22", fc="#FDECEA",
                          ec=P["red"], lw=0.8))

    # ── Flow arrows (layer → credential) ─────────────────────────────────────
    for row_y, *_ in rows:
        ax.annotate("", xy=(8.0, row_y), xytext=(4.0, row_y),
                    arrowprops=dict(arrowstyle="->", color="#AAAAAA", lw=0.9),
                    zorder=3)

    # ── Footer rule ───────────────────────────────────────────────────────────
    rect2 = FancyBboxPatch((0.3, 0.22), 15.4, 0.62,
                           boxstyle="round,pad=0.05,rounding_size=0.08",
                           linewidth=1.2, edgecolor=P["amber"],
                           facecolor="#FFF8EE", zorder=2)
    ax.add_patch(rect2)
    ax.text(8.0, 0.53,
            "Core Rule:  No credential ever crosses a layer boundary — "
            "each layer authenticates independently with its own credential type.",
            ha="center", va="center", fontsize=8, fontweight="bold",
            color=P["amber"], fontfamily=FONT, zorder=3)

    return _save(fig, "05_credential_isolation", dpi=200)


# ============================================================================
# DIAGRAM 6 — Per-Tool RBAC Decision Flow
#
# v2 REWRITE:
#   - Canvas 16×11 (was 13×8)
#   - All box widths increased to prevent label truncation
#   - audit_log sublabel shortened to fit
#   - "No" labels same fontsize as "Yes" (both 8.5)
#   - Happy path runs straight down the centre
# ============================================================================
def d6_rbac():
    W, H = 16, 11
    fig, ax = plt.subplots(figsize=(W, H))
    fig.patch.set_facecolor("#FAFBFF")
    ax.set_facecolor("#FAFBFF")
    ax.set_xlim(0, W)
    ax.set_ylim(0, H)
    ax.axis("off")

    CX = W / 2  # centre x = 8.0

    ax.text(CX, H - 0.3, "Per-Tool RBAC Decision Flow  (require_role)",
            ha="center", va="center", fontsize=13, fontweight="bold",
            color=P["navy"], fontfamily=FONT)

    # ── Diamond helper ────────────────────────────────────────────────────────
    def diamond(cx, cy, w, h, text, color):
        xs = [cx, cx + w / 2, cx, cx - w / 2, cx]
        ys = [cy + h / 2, cy, cy - h / 2, cy, cy + h / 2]
        ax.fill(xs, ys, color=color, alpha=0.15, zorder=2)
        ax.plot(xs, ys, color=color, lw=1.3, zorder=3)
        ax.text(cx, cy, text, ha="center", va="center",
                fontsize=8.5, color=color, fontweight="bold",
                fontfamily=FONT, zorder=4)

    # ── Vertical flow (all centred at CX) ────────────────────────────────────
    _box(ax, CX, 10.2, 5.5, 0.80, "Tool function called",
         "e.g. customer_360(customer_id='CUST007')",
         bg=P["dkblue"], fontsize=10)

    _box(ax, CX, 9.0, 6.5, 0.75,
         "BearerClaimsMiddleware  →  _request_claims  ContextVar",
         "sub=alice  ·  roles=[agent]   (decoded from JWT, no re-verify needed)",
         bg=P["teal"], fontsize=9, sublabel_size=7.8)

    _arrow(ax, CX, 9.80, CX, 9.37, color="#555555")

    diamond(CX, 7.85, 4.0, 1.0, "claims empty?\n(dev-mode flag)", P["amber"])
    _arrow(ax, CX, 8.62, CX, 8.35, color="#555555")

    # Yes → right → PASS (dev only)
    RIGHT_X = 13.2
    _arrow(ax, CX + 2.0, 7.85, RIGHT_X - 1.6, 7.85,
           label="Yes", color=P["amber"], fontsize=8.5)
    _box(ax, RIGHT_X, 7.85, 2.8, 0.72, "PASS (dev only)",
         "Open dev mode — no auth check",
         bg=P["amber"], fontsize=9)

    # No → down
    diamond(CX, 6.55, 4.0, 1.0, "'admin' in user_roles?", P["purple"])
    _arrow(ax, CX, 7.35, CX, 7.05, label="No", color="#555555", fontsize=8.5)

    # admin Yes → right → PASS
    _arrow(ax, CX + 2.0, 6.55, RIGHT_X - 1.6, 6.55,
           label="Yes", color=P["purple"], fontsize=8.5)
    _box(ax, RIGHT_X, 6.55, 2.8, 0.72, "PASS  (admin bypass)",
         "admin role skips all role checks",
         bg=P["purple"], fontsize=9)

    # admin No → down
    diamond(CX, 5.25, 4.5, 1.0, "any required role in user_roles?", P["green"])
    _arrow(ax, CX, 6.05, CX, 5.75, label="No", color="#555555", fontsize=8.5)

    # role Yes → right → PASS
    _arrow(ax, CX + 2.25, 5.25, RIGHT_X - 1.6, 5.25,
           label="Yes", color=P["green"], fontsize=8.5)
    _box(ax, RIGHT_X, 5.25, 2.8, 0.72, "PASS  (role matched)",
         "Proceed to audit_log",
         bg=P["green"], fontsize=9)

    # role No → down → PermissionError
    _arrow(ax, CX, 4.75, CX, 4.2, label="No", color=P["red"], fontsize=8.5)
    _box(ax, CX, 3.75, 5.5, 0.72, "PermissionError  →  HTTP 403 Forbidden",
         "required=['agent','admin']  ·  caller has=['readonly']",
         bg=P["red"], fontsize=9.5, sublabel_size=7.8)

    # ── Happy path convergence ────────────────────────────────────────────────
    # All PASS boxes drop to audit_log via connector on the right
    for pass_y in [7.85, 6.55, 5.25]:
        _arrow(ax, RIGHT_X, pass_y - 0.36, RIGHT_X, 3.2, color=P["green"])
    # Horizontal into audit_log
    _arrow(ax, RIGHT_X, 3.2, CX + 3.0, 3.2, color=P["green"])

    _box(ax, CX, 2.75, 5.5, 0.72, "audit_log()",
         "records: tool · service · sub · roles · args_keys",
         bg=P["teal"], fontsize=9.5, sublabel_size=7.8)
    _arrow(ax, CX, 3.39, CX, 3.11, color=P["teal"])
    _arrow(ax, CX, 2.39, CX, 1.88, color=P["dkblue"])

    _box(ax, CX, 1.45, 5.5, 0.72, "Execute business logic",
         "DB query via MYSQL_USER/PASSWORD  ·  or external API call  ·  compute result",
         bg=P["dkblue"], fontsize=9.5, sublabel_size=7.8)

    # ── Legend footnote ───────────────────────────────────────────────────────
    ax.text(CX, 0.45,
            "require_role() is called at the START of every tool function — "
            "before any business logic executes.",
            ha="center", va="center", fontsize=8, color="#555555",
            fontfamily=FONT, fontstyle="italic")

    return _save(fig, "06_rbac_flow", dpi=200)


# ============================================================================
# Run all diagrams
# ============================================================================
def generate_all():
    paths = {}
    print("Generating architecture diagrams (v2)...")
    paths["system"]      = d1_system_overview()
    print(f"  [1/6] {paths['system'].name}")
    paths["auth"]        = d2_auth_flow()
    print(f"  [2/6] {paths['auth'].name}")
    paths["jwt"]         = d3_jwt_lifecycle()
    print(f"  [3/6] {paths['jwt'].name}")
    paths["lifecycle"]   = d4_server_lifecycle()
    print(f"  [4/6] {paths['lifecycle'].name}")
    paths["credentials"] = d5_credential_isolation()
    print(f"  [5/6] {paths['credentials'].name}")
    paths["rbac"]        = d6_rbac()
    print(f"  [6/6] {paths['rbac'].name}")
    return paths


if __name__ == "__main__":
    generate_all()
    print("Done — PNGs saved to scripts/diagrams_out/")
