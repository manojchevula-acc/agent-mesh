"""
scripts/generate_design_doc.py
--------------------------------
Generates notes/MCP_Hub_Design_Document.docx
Run:  python scripts/generate_design_doc.py
"""

import datetime, io
from pathlib import Path

from docx import Document
from docx.shared import Pt, RGBColor, Cm, Inches
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.enum.table import WD_TABLE_ALIGNMENT
from docx.oxml.ns import qn
from docx.oxml import OxmlElement

import diagrams as D   # generates PNGs and returns paths

OUT  = Path(__file__).parent.parent / "notes" / "MCP_Hub_Design_Document.docx"
DIAG = Path(__file__).parent / "diagrams_out"

# ---------------------------------------------------------------------------
# Colour palette
# ---------------------------------------------------------------------------
C = {
    "navy":    "1F3864", "blue":   "2672C4", "dkblue":  "1B4F8A",
    "teal":    "006B6B", "green":  "1E6B30", "ltgreen": "E7F3EC",
    "amber":   "7D4200", "ltamber":"FFF3CD", "red":     "9B1C1C",
    "ltred":   "FDECEA", "purple": "4B0082", "ltpurple":"F3E8FF",
    "grey":    "404040", "ltgrey": "F5F5F5", "white":   "FFFFFF",
    "ltblue":  "DDEAF7", "alt":    "EFF4FC",
    "phase1":  "1B4F8A", "phase2": "006B6B", "phase3":  "4B0082",
    "phase4":  "1E6B30", "phase5": "7D4200",
}

def rgb(h): h=h.lstrip("#"); return RGBColor(int(h[0:2],16),int(h[2:4],16),int(h[4:6],16))

# ---------------------------------------------------------------------------
# XML / layout helpers
# ---------------------------------------------------------------------------

def _cell_bg(cell, hex_color):
    tc=cell._tc; tcPr=tc.get_or_add_tcPr()
    for o in tcPr.findall(qn("w:shd")): tcPr.remove(o)
    shd=OxmlElement("w:shd"); shd.set(qn("w:val"),"clear")
    shd.set(qn("w:color"),"auto"); shd.set(qn("w:fill"),hex_color.upper())
    tcPr.append(shd)

def _cell_borders(cell, color="CCCCCC", sz="4"):
    tc=cell._tc; tcPr=tc.get_or_add_tcPr()
    tcB=OxmlElement("w:tcBorders")
    for e in ("top","left","bottom","right"):
        t=OxmlElement(f"w:{e}"); t.set(qn("w:val"),"single")
        t.set(qn("w:sz"),sz); t.set(qn("w:space"),"0")
        t.set(qn("w:color"),color); tcB.append(t)
    tcPr.append(tcB)

def _para_border_bottom(p, color="2672C4", sz="12"):
    pPr=p._p.get_or_add_pPr(); pBdr=OxmlElement("w:pBdr")
    bot=OxmlElement("w:bottom"); bot.set(qn("w:val"),"single")
    bot.set(qn("w:sz"),sz); bot.set(qn("w:space"),"1")
    bot.set(qn("w:color"),color); pBdr.append(bot); pPr.append(pBdr)

def _run(para, text, bold=False, italic=False, size=11,
         color="404040", font="Calibri", underline=False):
    r=para.add_run(text); r.bold=bold; r.italic=italic; r.underline=underline
    r.font.name=font; r.font.size=Pt(size); r.font.color.rgb=rgb(color)
    return r

# ---------------------------------------------------------------------------
# Typography helpers
# ---------------------------------------------------------------------------

def H1(doc, text):
    p=doc.add_paragraph(); p.paragraph_format.space_before=Pt(22)
    p.paragraph_format.space_after=Pt(6); _para_border_bottom(p)
    _run(p, text, bold=True, size=18, color="1F3864"); return p

def H2(doc, text):
    p=doc.add_paragraph(); p.paragraph_format.space_before=Pt(14)
    p.paragraph_format.space_after=Pt(4)
    _run(p, text, bold=True, size=13, color="2672C4"); return p

def H3(doc, text):
    p=doc.add_paragraph(); p.paragraph_format.space_before=Pt(9)
    p.paragraph_format.space_after=Pt(2)
    _run(p, text, bold=True, size=11, color="1F3864"); return p

def body(doc, text, indent=0):
    p=doc.add_paragraph(); p.paragraph_format.space_after=Pt(6)
    p.paragraph_format.left_indent=Cm(indent)
    _run(p, text, size=11, color="333333"); return p

def bullet(doc, text, level=0, label=None, label_color="1F3864"):
    p=doc.add_paragraph(); p.paragraph_format.space_after=Pt(4)
    p.paragraph_format.left_indent=Cm(0.75+level*0.65)
    p.paragraph_format.first_line_indent=Cm(-0.4)
    mark="▪" if level else "●"
    _run(p, f"{mark}  ", bold=True, size=10, color="2672C4")
    if label: _run(p, label, bold=True, size=11, color=label_color)
    _run(p, text, size=11, color="333333"); return p

def numbered(doc, n, text, label=None):
    p=doc.add_paragraph(); p.paragraph_format.space_after=Pt(5)
    p.paragraph_format.left_indent=Cm(0.9)
    p.paragraph_format.first_line_indent=Cm(-0.6)
    _run(p, f"{n}.  ", bold=True, size=11, color="2672C4")
    if label: _run(p, label, bold=True, size=11, color="1F3864")
    _run(p, text, size=11, color="333333"); return p

def code(doc, text):
    tbl=doc.add_table(rows=1,cols=1); tbl.alignment=WD_TABLE_ALIGNMENT.LEFT
    cell=tbl.cell(0,0); _cell_bg(cell,"F0F0F0"); _cell_borders(cell,"AAAAAA","4")
    p=cell.paragraphs[0]; p.paragraph_format.space_before=Pt(5)
    p.paragraph_format.space_after=Pt(5); p.paragraph_format.left_indent=Cm(0.25)
    r=p.add_run(text); r.font.name="Courier New"; r.font.size=Pt(8.5)
    r.font.color.rgb=rgb("1A1A2E"); doc.add_paragraph(); return tbl

def callout(doc, kind, title, text):
    cfg = {
        "note":       ("DDEAF7","1B4F8A","INFO",      "1B4F8A"),
        "warning":    ("FDECEA","9B1C1C","WARNING",    "9B1C1C"),
        "example":    ("E7F3EC","1E6B30","EXAMPLE",    "1E6B30"),
        "definition": ("F3E8FF","4B0082","DEFINITION", "4B0082"),
        "important":  ("FFF3CD","7D4200","IMPORTANT",  "7D4200"),
        "security":   ("FDECEA","9B1C1C","SECURITY",   "9B1C1C"),
    }
    bg,bdr,lbl,lc = cfg.get(kind, cfg["note"])
    tbl=doc.add_table(rows=1,cols=1); tbl.alignment=WD_TABLE_ALIGNMENT.LEFT
    cell=tbl.cell(0,0); _cell_bg(cell,bg)
    tc=cell._tc; tcPr=tc.get_or_add_tcPr()
    tcB=OxmlElement("w:tcBorders")
    for e,clr,sz in [("left",bdr,"24"),("top",bdr,"4"),("bottom",bdr,"4"),("right","FFFFFF","4")]:
        t=OxmlElement(f"w:{e}"); t.set(qn("w:val"),"single")
        t.set(qn("w:sz"),sz); t.set(qn("w:space"),"0")
        t.set(qn("w:color"),clr); tcB.append(t)
    tcPr.append(tcB)
    p=cell.paragraphs[0]; p.paragraph_format.space_before=Pt(3)
    p.paragraph_format.space_after=Pt(3); p.paragraph_format.left_indent=Cm(0.25)
    r1=p.add_run(f"{lbl}"); r1.bold=True; r1.font.name="Calibri"
    r1.font.size=Pt(8.5); r1.font.color.rgb=rgb(lc)
    if title:
        r2=p.add_run(f"  —  {title}\n"); r2.bold=True; r2.font.name="Calibri"
        r2.font.size=Pt(10); r2.font.color.rgb=rgb(lc)
    else: p.add_run("\n")
    r3=p.add_run(text); r3.font.name="Calibri"; r3.font.size=Pt(10)
    r3.font.color.rgb=rgb("333333"); doc.add_paragraph(); return tbl

def table(doc, headers, rows, widths=None):
    tbl=doc.add_table(rows=1+len(rows),cols=len(headers))
    tbl.alignment=WD_TABLE_ALIGNMENT.LEFT; tbl.style="Table Grid"
    hr=tbl.rows[0]
    for i,h in enumerate(headers):
        c=hr.cells[i]; _cell_bg(c,"1F3864")
        p=c.paragraphs[0]; p.alignment=WD_ALIGN_PARAGRAPH.CENTER
        r=p.add_run(h); r.bold=True; r.font.name="Calibri"
        r.font.size=Pt(9.5); r.font.color.rgb=rgb("FFFFFF")
    for ri,row in enumerate(rows):
        bg="EFF4FC" if ri%2==1 else "FFFFFF"
        dr=tbl.rows[ri+1]
        for ci,val in enumerate(row):
            c=dr.cells[ci]; _cell_bg(c,bg); _cell_borders(c,"CCCCCC","4")
            p=c.paragraphs[0]
            if isinstance(val,dict):
                _run(p,val["text"],bold=val.get("bold",False),size=9.5,
                     color=val.get("color","333333"))
            else:
                _run(p,str(val),size=9.5,color="333333")
    if widths:
        for row in tbl.rows:
            for i,w in enumerate(widths):
                if i<len(row.cells): row.cells[i].width=Cm(w)
    doc.add_paragraph(); return tbl

def img(doc, path, width_cm=16.0, caption=None):
    doc.add_picture(str(path), width=Cm(width_cm))
    doc.paragraphs[-1].alignment=WD_ALIGN_PARAGRAPH.CENTER
    if caption:
        cp=doc.add_paragraph(); cp.alignment=WD_ALIGN_PARAGRAPH.CENTER
        cp.paragraph_format.space_before=Pt(2); cp.paragraph_format.space_after=Pt(10)
        _run(cp, caption, italic=True, size=9, color="777777")

def phase_banner(doc, num, title, desc, color):
    tbl=doc.add_table(rows=1,cols=2); tbl.alignment=WD_TABLE_ALIGNMENT.LEFT
    nc=tbl.cell(0,0); _cell_bg(nc,color)
    p1=nc.paragraphs[0]; p1.alignment=WD_ALIGN_PARAGRAPH.CENTER
    r=p1.add_run(f"PHASE\n{num}"); r.bold=True; r.font.name="Calibri"
    r.font.size=Pt(14); r.font.color.rgb=rgb("FFFFFF")
    nc.width=Cm(2.2)
    tc=tbl.cell(0,1); _cell_bg(tc,"DDEAF7")
    p2=tc.paragraphs[0]
    r2=p2.add_run(title+"\n"); r2.bold=True; r2.font.name="Calibri"
    r2.font.size=Pt(12); r2.font.color.rgb=rgb("1F3864")
    r3=p2.add_run(desc); r3.font.name="Calibri"
    r3.font.size=Pt(10); r3.font.color.rgb=rgb("444444")
    doc.add_paragraph(); return tbl

def spacer(doc,pt=8):
    p=doc.add_paragraph(); p.paragraph_format.space_before=Pt(pt); return p

def divider(doc):
    p=doc.add_paragraph(); p.paragraph_format.space_before=Pt(4)
    p.paragraph_format.space_after=Pt(4); _para_border_bottom(p,"DDEAF7","6")

# ============================================================================
# BUILD
# ============================================================================

def build():
    # Generate all diagrams first
    print("Generating diagrams...")
    paths = D.generate_all()
    print("Building document...")

    doc=Document()
    for s in doc.sections:
        s.top_margin=Cm(2.2); s.bottom_margin=Cm(2.2)
        s.left_margin=Cm(2.8); s.right_margin=Cm(2.8)
    doc.styles["Normal"].font.name="Calibri"
    doc.styles["Normal"].font.size=Pt(11)

    # ===========================================================================
    # COVER
    # ===========================================================================
    spacer(doc, 36)

    cover_bar=doc.add_table(rows=1,cols=1)
    _cell_bg(cover_bar.cell(0,0),"1F3864")
    p=cover_bar.cell(0,0).paragraphs[0]
    p.paragraph_format.space_before=Pt(6); p.paragraph_format.space_after=Pt(6)
    r=p.add_run("  MCP Hub — Solution Design Document")
    r.bold=True; r.font.name="Calibri"; r.font.size=Pt(26); r.font.color.rgb=rgb("FFFFFF")

    spacer(doc,10)
    s1=doc.add_paragraph(); _run(s1,"Model Context Protocol Hub",bold=True,size=16,color="2672C4")
    s2=doc.add_paragraph()
    _run(s2,
        "Architecture, Authentication, Authorization, Governance & Standards\n"
        "A comprehensive implementation guide for engineering teams.",
        size=12, color="555555")

    spacer(doc,16)
    meta=doc.add_table(rows=4,cols=2)
    for ri,(k,v) in enumerate([
        ("Version",  "2.0"),
        ("Date",     datetime.date.today().strftime("%B %d, %Y")),
        ("Audience", "Solution Architects · Security Engineers · Backend Developers"),
        ("Status",   "Internal — For Implementation"),
    ]):
        _cell_bg(meta.rows[ri].cells[0],"DDEAF7")
        _cell_bg(meta.rows[ri].cells[1],"FFFFFF")
        _cell_borders(meta.rows[ri].cells[0],"DDEAF7","4")
        _cell_borders(meta.rows[ri].cells[1],"DDEAF7","4")
        _run(meta.rows[ri].cells[0].paragraphs[0],k,bold=True,size=10,color="1F3864")
        _run(meta.rows[ri].cells[1].paragraphs[0],v,size=10,color="333333")

    doc.add_page_break()

    # ===========================================================================
    # 1 · INTRODUCTION
    # ===========================================================================
    H1(doc,"1.  Introduction & Purpose")
    body(doc,
        "Organisations deploying AI agents at scale face a core challenge: how do agents "
        "discover which backend service to call, authenticate securely, and operate within "
        "defined policy boundaries — without each agent needing bespoke integration code?")
    body(doc,
        "The MCP Hub solves this by acting as a single control plane. It maintains a live "
        "registry of all MCP-compliant backend servers, runs intelligent LLM-powered routing "
        "to match queries to the right server, issues cryptographically scoped security "
        "tokens, and enforces role-based access policy — all transparently, so MCP servers "
        "focus purely on their business logic.")

    H2(doc,"1.1  Design Goals")
    table(doc,
        ["Goal","Description","Mechanism"],
        [
            ("Zero-trust auth",     "Every boundary is independently authenticated; no implicit trust","RS256 JWT per boundary"),
            ("Credential isolation","No credential crosses a layer boundary",                         "Separate token per layer"),
            ("Dynamic discovery",   "Agents discover tools at runtime, not compile-time",            "tools/list on every session"),
            ("Intelligent routing", "Best-fit server selected per query, not hardcoded",             "LangGraph ReAct routing agent"),
            ("Auditability",        "Every auth, routing, and tool call logged with caller identity","4-way observability fan-out"),
            ("Governance",          "Servers enabled/disabled without agent code changes",           "Registry is_active flag"),
        ],
        widths=[3.5,7.0,6.0])

    H2(doc,"1.2  Scope")
    bullet(doc,"Hub architecture — components, request lifecycle, routing")
    bullet(doc,"MCP protocol fundamentals — tools, resources, prompts, transports")
    bullet(doc,"Server registration, lifecycle management, and governance")
    bullet(doc,"End-to-end authentication across five phases with implementation examples")
    bullet(doc,"Authorization framework — hub RBAC, server JWT scoping, per-tool RBAC")
    bullet(doc,"Observability, audit logging, and security best practices")

    doc.add_page_break()

    # ===========================================================================
    # 2 · GLOSSARY
    # ===========================================================================
    H1(doc,"2.  Glossary of Key Terms")
    body(doc,"All MCP-specific and security terms used in this document are defined below.")

    table(doc,
        ["Term","Definition"],
        [
            ("MCP","Model Context Protocol — open standard for AI agents to discover and invoke backend capabilities (tools, resources, prompts)."),
            ("MCP Hub","Central gateway: server registry, JWT issuance, LLM routing, policy enforcement, and observability."),
            ("MCP Server","Backend service implementing the MCP protocol. Exposes tools, resources, and/or prompts."),
            ("Tool","Callable function exposed by an MCP server — takes typed arguments, returns typed results."),
            ("Resource","URI-addressed read-only data asset (document, dataset, live feed) exposed by a server."),
            ("Prompt","Reusable prompt template with named parameters, exposed by a server for agent consumption."),
            ("Transport","Network protocol carrying MCP JSON-RPC messages. This implementation uses Streamable HTTP — a stateful POST-based protocol where the first request returns a Mcp-Session-Id header that must accompany all subsequent calls in the same session."),
            ("JWT","JSON Web Token — compact, URL-safe signed claims container. Claims: iss (issuer), aud (audience), sub (subject), exp (expiry), roles."),
            ("RS256","RSA + SHA-256 asymmetric JWT signing. Hub signs with private key; servers verify with public key. No shared secret required."),
            ("JWKS","JSON Web Key Set — standard endpoint (/.well-known/jwks.json) publishing a service's RSA public keys for JWT verification."),
            ("aud (audience)","JWT claim specifying the intended recipient. A server rejects any token whose aud does not exactly match its own ID."),
            ("iss (issuer)","JWT claim identifying who created the token. Recipients verify this matches the expected hub identity string."),
            ("sub (subject)","JWT claim identifying the principal (user) on whose behalf the token was issued."),
            ("RBAC","Role-Based Access Control — restrict operations based on roles in the caller's JWT, not their individual identity."),
            ("PBKDF2","Password-Based Key Derivation Function 2 — slow hash (200k iterations) for password storage; resists offline brute-force."),
            ("JWTVerifier","FastMCP built-in middleware that validates RS256 tokens (sig, aud, iss, exp) on every MCP request; returns 401 on failure."),
            ("BearerClaimsMiddleware","Custom middleware that decodes the already-verified JWT into a ContextVar for per-tool RBAC without a second JWKS fetch."),
            ("require_role()","Tool-level RBAC function — reads claims ContextVar, raises PermissionError (→403) if caller lacks required role."),
            ("ReAct Agent","Reasoning+Acting LLM loop (LangGraph): THINK about query → CALL pick_server tool → OBSERVE result → route."),
            ("Semantic View","Pre-joined DB view abstracting raw tables from tool logic — decouples schema from tool API."),
            ("ContextVar","Python asyncio context variable — isolated per-request state for async code; used to pass JWT claims to tool functions."),
        ],
        widths=[4.0,13.0])

    doc.add_page_break()

    # ===========================================================================
    # 3 · MCP PROTOCOL FUNDAMENTALS
    # ===========================================================================
    H1(doc,"3.  MCP Protocol Fundamentals")

    H2(doc,"3.1  Protocol Overview")
    body(doc,
        "MCP defines a JSON-RPC 2.0 interface for AI agents to interact with backend services. "
        "The agent is the client; backend services implement the server interface. "
        "All messages are JSON-RPC envelopes carrying one of the defined MCP method names.")
    body(doc,
        "The protocol lifecycle has three phases: (1) initialize — negotiate capabilities and "
        "receive a session ID; (2) operate — discover and call tools, read resources, fetch "
        "prompts; (3) terminate — close the session.")

    code(doc,
"-- JSON-RPC envelope structure\n"
'{ "jsonrpc": "2.0", "id": 1, "method": "tools/call",\n'
'  "params": { "name": "customer_lookup", "arguments": { "customer_id": "C001" } } }\n\n'
"-- Response\n"
'{ "jsonrpc": "2.0", "id": 1,\n'
'  "result": { "content": [{ "type": "text", "text": "{name: Alice, plan: Gold}" }] } }')

    H2(doc,"3.2  Capability Types")
    table(doc,
        ["Capability","MCP Methods","Direction","Best For"],
        [
            ("Tools",    "tools/list  ·  tools/call",                    "Agent → Server", "Actions, computations, DB queries, API calls — anything with arguments"),
            ("Resources","resources/list  ·  resources/read  ·  resources/subscribe","Agent → Server","Read-only reference data: policies, catalogues, config, static lookups"),
            ("Prompts",  "prompts/list  ·  prompts/get",                 "Agent → Server","Reusable prompt templates with parameters; centralised prompt management"),
        ],
        widths=[3.2,5.8,3.0,6.5])

    H2(doc,"3.3  Tools — Detail")
    body(doc,
        "Tools are the primary interaction mechanism. Each tool has a name, description, "
        "inputSchema (JSON Schema defining accepted parameters), and optionally an outputSchema. "
        "The agent calls tools/list to discover available tools, then tools/call to invoke them.")
    code(doc,
"-- tools/list response (excerpt)\n"
'[\n'
'  {\n'
'    "name":        "order_summary",\n'
'    "description": "Return the N most recent orders for a given customer.",\n'
'    "inputSchema": {\n'
'      "type": "object",\n'
'      "properties": {\n'
'        "customer_id": { "type": "string",  "description": "Customer identifier" },\n'
'        "limit":        { "type": "integer", "description": "Max orders (default 10)" }\n'
'      },\n'
'      "required": ["customer_id"]\n'
'    },\n'
'    "outputSchema": { "type": "array", "items": { "type": "object" } }\n'
'  }\n'
']')

    H2(doc,"3.4  Resources — Detail")
    body(doc,
        "Resources are identified by URI and are read-only. No computation arguments are "
        "accepted — if arguments are needed, use a Tool instead. "
        "Servers may support resource subscriptions (push notifications when a resource changes).")
    table(doc,
        ["URI Scheme","Use Case","Example URI"],
        [
            ("docs://",   "Policy, governance, markdown documents",    "docs://pricing-policy/current"),
            ("data://",   "Catalogue records, reference tables",        "data://product-catalogue/v3"),
            ("config://", "Feature flags, runtime configuration",       "config://feature-flags/prod"),
            ("feed://",   "Live data streams, market rates",            "feed://fx-rates/usd-gbp"),
        ],
        widths=[3.0,7.0,7.5])

    H2(doc,"3.5  Prompts — Detail")
    body(doc,
        "Prompts let the server own and version prompt templates. The agent fetches and renders "
        "a prompt with supplied arguments. This allows prompt improvements to roll out without "
        "redeploying agent code.")
    code(doc,
"-- prompts/get request\n"
'{ "method": "prompts/get",\n'
'  "params": { "name": "customer_briefing",\n'
'              "arguments": { "customer_id": "C001", "tone": "professional" } } }\n\n'
"-- Response\n"
'{ "messages": [{\n'
'    "role": "user",\n'
'    "content": { "type": "text",\n'
'      "text": "Prepare a professional meeting brief for customer C001. Include: '\
'account status, recent orders, open issues, and renewal talking points." }\n'
'}] }')

    H2(doc,"3.6  Transport Protocol — Streamable HTTP")
    body(doc,
        "This implementation uses the Streamable HTTP transport exclusively. All MCP servers "
        "expose a single POST /mcp endpoint. The agent sends all JSON-RPC messages as HTTP POST "
        "requests, with session continuity maintained via the Mcp-Session-Id header.")
    table(doc,
        ["Aspect","Streamable HTTP Detail"],
        [
            ("Endpoint",          "Single POST /mcp — all methods (initialize, tools/list, tools/call, …) use the same URL"),
            ("Session init",      "First POST returns Mcp-Session-Id in the response header — must be sent on all subsequent requests"),
            ("Subsequent calls",  "POST /mcp with Mcp-Session-Id: <uuid> header — server correlates to session state"),
            ("Accept header",     "Must include both: Accept: application/json, text/event-stream  (FastMCP returns 406 if missing)"),
            ("Authorization",     "Authorization: Bearer <per-server JWT>  present on EVERY POST — validated independently each time"),
            ("Response format",   "JSON response body or text/event-stream depending on method and Accept negotiation"),
            ("Session teardown",  "DELETE /mcp with Mcp-Session-Id to close cleanly; or session times out server-side"),
        ],
        widths=[4.0,13.5])

    callout(doc,"important","Bearer token on every call",
        "The Authorization: Bearer <token> header must be present on EVERY JSON-RPC request — "
        "not just the initialize handshake. The MCP server's JWTVerifier validates the token "
        "independently on each request. An expired token will be rejected mid-session with HTTP 401, "
        "even if the session was previously established successfully.")

    doc.add_page_break()

    # ===========================================================================
    # 4 · HUB ARCHITECTURE
    # ===========================================================================
    H1(doc,"4.  MCP Hub Architecture")

    H2(doc,"4.1  System Architecture Overview")
    body(doc,
        "The diagram below shows all system components across four layers: "
        "presentation (browser), application (chat server + hub server), "
        "orchestration (agent), and integration (MCP servers + database).")
    img(doc, paths["system"], width_cm=16.5,
        caption="Figure 1 — System Architecture Overview")

    H2(doc,"4.2  Hub Responsibilities")
    table(doc,
        ["Responsibility","Detail","Implemented In"],
        [
            ("Server Registry",        "MySQL table mcp_servers: id, endpoint, transport (streamable-http), capabilities, api_key, is_active","hub_server.py + db.py"),
            ("JWKS Publication",       "GET /.well-known/jwks.json — publishes RSA public key in JWK Set format for server verification","hub_server.py"),
            ("JWT Issuance",           "POST /discover mints per-server RS256 JWT: aud=server_id, exp=1h, sub=user, roles forwarded",  "hub_server.py"),
            ("LLM Routing",            "LangGraph ReAct agent selects best-matching MCP server per query using capability/skills/examples","hub_server.py"),
            ("Registry Cache",         "In-process 60s cache of mcp_servers; POST /api/hub/refresh for immediate invalidation",       "hub_server.py load_hub()"),
            ("Admin REST API",         "CRUD for servers, user management, event log query (admin role required)",                     "hub_server.py /api/hub/*"),
            ("Admin UI",               "Browser SPA: server list, tool probe, key copy, event log viewer",                           "hub_server.py (inline HTML)"),
            ("Observability",          "log_event() fans out to memory + stdout + JSONL file + MySQL hub_events",                     "observability.py"),
            ("Hub RBAC",               "_classify_token() validates hub JWT + checks roles against endpoint requirement",             "hub_server.py"),
        ],
        widths=[4.0,9.5,4.0])

    H2(doc,"4.3  Request Lifecycle")
    body(doc,"Every agent query follows this six-step lifecycle:")
    numbered(doc,1,"Browser sends query via POST to Chat Server over an active SSE session.",label="User sends query:  ")
    numbered(doc,2,"Chat Server launches run_agent(query) as a background asyncio.Task.",label="Task created:  ")
    numbered(doc,3,"Agent calls POST /discover with the user's hub JWT. Hub validates, runs LLM routing, returns selected server(s) + per-server JWTs.",label="Hub discovery:  ")
    numbered(doc,4,"Agent opens a Streamable HTTP MCP session to the selected server. Sends initialize, receives Mcp-Session-Id, then attaches both the session ID and the per-server JWT on every subsequent call.",label="MCP session open:  ")
    numbered(doc,5,"Agent's LangGraph ReAct loop: discover tools → select tool → call tool → observe result → synthesise answer.",label="ReAct loop:  ")
    numbered(doc,6,"Final answer emitted as SSE event to browser. Saved to MySQL conversations regardless of connection state.",label="Answer streamed:  ")

    H2(doc,"4.4  Hub Internal Components")
    code(doc,
"hub_server.py  (FastAPI application)\n"
"  │\n"
"  ├── _startup()               Load RSA keys; create DB tables; ensure api_key column exists\n"
"  ├── load_hub()               MySQL registry query with 60s in-process cache\n"
"  ├── _classify_token()        Validate hub JWT: RS256 sig · iss · aud · exp · RBAC role check\n"
"  ├── GET /.well-known/jwks.json  Serve RSA public key as JWK Set\n"
"  ├── POST /discover\n"
"  │     ├── validate hub JWT\n"
"  │     ├── load_hub() → server list (from cache, transport=streamable-http)\n"
"  │     ├── _agent_route()     LangGraph ReAct agent (new instance per request)\n"
"  │     │     └── pick_server(id, reason) tool → best server selected\n"
"  │     └── for each matched server:\n"
"  │           mint RS256 JWT (aud=server_id, exp=1h) using private.pem\n"
"  ├── GET /servers             Return active server list (auth: agent|admin)\n"
"  └── /api/hub/*              Admin CRUD: add / edit / disable / delete / probe / refresh\n"
"\n"
"db.py\n"
"  └── get_engine()  SQLAlchemy engine — pool_pre_ping + pool_recycle=1800s (MySQL timeout guard)\n"
"\n"
"observability.py\n"
"  └── log_event()   4-way fan-out: deque(500) + stdout + logs/hub.log (JSONL) + hub_events table")

    doc.add_page_break()

    # ===========================================================================
    # 5 · SERVER REGISTRATION
    # ===========================================================================
    H1(doc,"5.  Server Registration & Governance")

    H2(doc,"5.1  Registry Schema")
    body(doc,
        "Every MCP server participating in the hub ecosystem must be registered "
        "in the mcp_servers table. This table is the authoritative source of truth "
        "for routing, JWT audience validation, and admin UI display.")
    code(doc,
"CREATE TABLE mcp_servers (\n"
"    id              VARCHAR(100)   NOT NULL,          -- JWT aud value; unique per server\n"
"    name            VARCHAR(255)   NOT NULL,          -- display name (Admin UI)\n"
"    endpoint        VARCHAR(500)   NOT NULL,          -- full MCP URL (http://host:port/path)\n"
"    transport       VARCHAR(50)    NOT NULL DEFAULT 'streamable-http',  -- always 'streamable-http'\n"
"    capability      TEXT,                             -- one-line routing hint\n"
"    skills          JSON,                             -- e.g. ['customer','crm','360']\n"
"    description     TEXT,                             -- detailed routing context\n"
"    examples        JSON,                             -- sample queries for routing\n"
"    start_cmd       TEXT,                             -- how to start the server locally\n"
"    api_key         VARCHAR(1000)  DEFAULT NULL,      -- static fallback Bearer token\n"
"    api_key_expires TIMESTAMP      DEFAULT NULL,      -- NULL = never\n"
"    is_active       TINYINT(1)     NOT NULL DEFAULT 1,-- 0=disabled; excluded from routing\n"
"    created_at      TIMESTAMP      NOT NULL DEFAULT CURRENT_TIMESTAMP,\n"
"    updated_at      TIMESTAMP      NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,\n"
"    PRIMARY KEY (id)\n"
") ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;")

    H2(doc,"5.2  Registration Methods")
    numbered(doc,1,
        "mcp-hub.json seed file — declarative JSON list seeded via python scripts/seed_hub_db.py. "
        "Uses INSERT ... ON DUPLICATE KEY UPDATE — safe to re-run. "
        "Best for bootstrapping a known set of servers.",
        label="JSON seed script:  ")
    numbered(doc,2,
        "POST /api/hub/servers (Admin UI or REST) — add servers at runtime. "
        "Requires admin JWT. Suitable for CI/CD pipelines that register new services on deploy.",
        label="REST API:  ")
    numbered(doc,3,
        "PUT /api/hub/servers/{id} — update any field of an existing registration. "
        "Changes appear within 60 seconds (or immediately after POST /api/hub/refresh).",
        label="Update:  ")

    callout(doc,"warning","Re-seed re-activates disabled servers",
        "The seed UPSERT sets is_active=1 unconditionally. If you manually disabled a server "
        "via the Admin UI and then re-seed, it will be re-activated. "
        "To prevent this: remove the server from mcp-hub.json, or set is_active=0 after seeding.")

    H2(doc,"5.3  Server Lifecycle")
    img(doc, paths["lifecycle"], width_cm=16.0,
        caption="Figure 2 — Server Registration & Lifecycle State Machine")

    H2(doc,"5.4  Admin Operations")
    table(doc,
        ["Operation","Endpoint","Auth","Behaviour"],
        [
            ("Add server",    "POST /api/hub/servers",           "admin", "Insert registry row; active immediately after cache refresh"),
            ("Edit server",   "PUT /api/hub/servers/{id}",       "admin", "Update any field; change visible within 60s"),
            ("Disable",       "PATCH /api/hub/servers/{id}",     "admin", "Set is_active=0; excluded from all routing"),
            ("Probe server",  "POST /api/hub/servers/{id}/probe","admin", "Hub mints test JWT → calls tools/list → verifies auth working"),
            ("Refresh cache", "POST /api/hub/refresh",           "admin", "Bust 60s cache immediately; next /discover reads fresh DB"),
            ("View logs",     "GET /api/logs",                   "admin", "Return last N events from MySQL hub_events (or in-memory)"),
            ("Delete server", "DELETE /api/hub/servers/{id}",    "admin", "Remove row permanently; irreversible"),
        ],
        widths=[3.0,5.5,2.0,7.0])

    callout(doc,"note","Probe admin JWT",
        "When the Admin UI probes a server, the hub mints a temporary JWT (aud=server_id) "
        "and calls tools/list. If auth is misconfigured on the MCP server, the probe "
        "returns an error and surfaces it in the Admin UI — useful for debugging JWT config.")

    doc.add_page_break()

    # ===========================================================================
    # 6 · AUTHENTICATION — END TO END
    # ===========================================================================
    H1(doc,"6.  Authentication Architecture — End to End")
    body(doc,
        "Authentication in the MCP Hub follows a layered, zero-trust model. "
        "Every boundary between components is independently authenticated using a "
        "different credential type. Compromise of one layer does not give access to another.")

    callout(doc,"security","Core Security Principle — Credential Isolation",
        "Hub JWT           → accepted at hub API; never sent to MCP servers\n"
        "Per-server JWT    → accepted at MCP server; never forwarded to DB or external APIs\n"
        "DB credentials    → used inside MCP server only; never sent to agent or hub\n"
        "External API keys → used inside MCP server only; never sent to agent or hub\n\n"
        "Each layer owns exactly one credential type and validates it independently.")

    H2(doc,"6.0  Authentication Overview Diagram")
    img(doc, paths["auth"], width_cm=17.5,
        caption="Figure 3 — End-to-End Authentication Swimlane (Five Phases)")

    H2(doc,"6.0b  RSA Key Infrastructure")
    img(doc, paths["jwt"], width_cm=16.5,
        caption="Figure 4 — JWT Token Lifecycle & RSA Key Infrastructure")
    body(doc,
        "The hub generates a 2048-bit RSA key pair on first startup. The private key signs "
        "all JWTs and never leaves the hub process. The public key is published as a JWK Set "
        "at the well-known JWKS endpoint. MCP servers fetch and cache this on startup, "
        "then use it to verify every incoming Bearer token without contacting the hub again.")

    code(doc,
"# Hub startup — key generation (hub_server.py _startup())\n"
"from cryptography.hazmat.primitives.asymmetric import rsa\n"
"from cryptography.hazmat.primitives import serialization\n\n"
"private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)\n"
"private_pem = private_key.private_bytes(Encoding.PEM, PrivateFormat.PKCS8, NoEncryption())\n"
"public_pem  = private_key.public_key().public_bytes(Encoding.PEM, PublicFormat.SubjectPublicKeyInfo)\n"
"Path('hub_service/.keys/private.pem').write_bytes(private_pem)\n"
"Path('hub_service/.keys/public.pem').write_bytes(public_pem)\n\n"
"# JWKS endpoint — served to all MCP servers\n"
"# GET /.well-known/jwks.json\n"
'{ "keys": [{ "kty":"RSA", "use":"sig", "alg":"RS256",\n'
'             "n":"<base64url modulus>", "e":"AQAB" }] }')

    divider(doc); spacer(doc,4)

    # ── PHASE 1
    phase_banner(doc,"1","User Login — Browser to Chat Server",
        "User submits credentials. Chat server verifies PBKDF2 hash. Issues hub JWT as session cookie.",
        C["phase1"])
    body(doc,
        "The chat server maintains a user table seeded from the CHAT_USERS environment variable. "
        "Passwords are stored as PBKDF2-SHA256 hashes with a unique random salt per user. "
        "On successful login, a hub JWT (8-hour expiry) is returned as an HttpOnly cookie.")
    code(doc,
"-- Step 1: Browser\n"
'POST /login  Body: { "username": "alice", "password": "s3cret!" }\n\n'
"-- Step 2: Chat server — hash verification\n"
"stored = 'pbkdf2:sha256:200000:<16-byte-salt>:<hex-digest>'\n"
"salt, expected = parse_stored_hash(stored)\n"
"derived = hashlib.pbkdf2_hmac('sha256', password.encode(), salt, 200_000)\n"
"if not hmac.compare_digest(derived, expected):  →  401 Unauthorized\n\n"
"-- Step 3: Mint hub JWT (RS256)\n"
'payload = { "sub": "alice", "roles": ["agent"],\n'
'            "iss": "mcp-hub", "aud": "mcp-hub", "exp": now + 28800 }\n'
'token = jwt.encode(payload, private_key, algorithm="RS256")\n\n'
"-- Step 4: Return token\n"
"Set-Cookie: session=<token>; HttpOnly; SameSite=Strict; Secure")

    table(doc,
        ["Control","Implementation","Attack Mitigated"],
        [
            ("PBKDF2-SHA256",   "200,000 iterations + unique 16-byte salt per user",       "Offline brute-force · Rainbow tables"),
            ("Rate limiting",   "10 failed attempts per username in 15-min window → 423",  "Online brute-force"),
            ("HttpOnly cookie", "Browser JS cannot read the token",                         "XSS token theft"),
            ("SameSite=Strict", "Cookie not sent on cross-origin requests",                 "CSRF attacks"),
            ("Constant-time compare","hmac.compare_digest() prevents timing oracle",        "Timing side-channel"),
        ],
        widths=[4.0,6.5,6.0])

    divider(doc); spacer(doc,4)

    # ── PHASE 2
    phase_banner(doc,"2","Hub API Authentication — Agent to Hub Server",
        "Every hub API call requires a valid hub JWT. Hub validates locally — no DB round-trip.",
        C["phase2"])
    body(doc,
        "The hub validates hub JWTs using its own RSA public key, loaded from public.pem at "
        "startup. The _classify_token() function decodes and verifies the token, then checks "
        "the caller's roles against the endpoint's required role.")
    code(doc,
"-- Agent calls hub\n"
"POST /discover\n"
"Authorization: Bearer eyJhbGciOiJSUzI1NiJ9...\n\n"
"-- hub_server.py _classify_token()\n"
"decoded = jwt.decode(\n"
"    token, public_key,\n"
"    algorithms=['RS256'],\n"
"    issuer='mcp-hub',\n"
"    audience='mcp-hub'\n"
")  # raises on bad sig / expired / wrong iss or aud → 401\n\n"
"-- RBAC check\n"
"required = endpoint_roles['/discover']   # = {'agent', 'admin'}\n"
"caller   = set(decoded['roles'])         # = {'agent'}\n"
"if not caller & required:  →  403 Forbidden\n"
"# else: proceed — decoded['sub'] and decoded['roles'] used for per-server JWT minting")

    table(doc,
        ["Hub Role","Permitted Endpoints","Typical Holder"],
        [
            ("admin",    "All: /discover · /servers · /health · /api/hub/* (CRUD)",  "Operator / admin login"),
            ("agent",    "POST /discover · GET /servers · GET /health",               "Agent/orchestrator process"),
            ("readonly", "GET /servers · GET /health only",                           "Monitoring / audit consumer"),
        ],
        widths=[3.0,9.5,5.0])

    divider(doc); spacer(doc,4)

    # ── PHASE 3
    phase_banner(doc,"3","Server Discovery & JWT Minting — POST /discover",
        "Hub runs LLM routing to select the best MCP server, then mints a scoped RS256 JWT for it.",
        C["phase3"])
    H3(doc,"LLM Routing Implementation")
    body(doc,
        "A fresh LangGraph ReAct agent is instantiated for each /discover call. "
        "The agent receives all active servers' capability, skills, description, and examples "
        "in its system prompt, then reasons about which server best fits the user's query.")
    callout(doc,"important","New agent instance per request",
        "LangGraph ReAct agents hold internal message state. A shared instance across "
        "concurrent requests would interleave messages and produce wrong routing decisions. "
        "A new instance costs ~1ms and is always safe. Never reuse a routing agent across requests.")
    code(doc,
"# hub_server.py _agent_route()\n"
"def _agent_route(query: str, servers: list[dict]) -> list[str]:\n"
"    tool = _make_routing_tool(servers)          # stateless tool wrapping pick_server logic\n"
"    agent = create_react_agent(llm, [tool])     # NEW instance per call\n"
"    result = agent.invoke({\n"
"        'messages': [('user', build_routing_prompt(query, servers))]\n"
"    })\n"
"    return extract_selected_ids(result)         # list of server IDs to mint JWTs for\n\n"
"# Routing prompt injects:\n"
"#   - User query\n"
"#   - For each server: id, capability, skills, description, examples\n"
"# LLM reasons: THINK → CALL pick_server(id, reason) → OBSERVE\n"
"# Fallback: if LLM unavailable → return first registered server")

    H3(doc,"Per-Server JWT Minting")
    code(doc,
"# For each server selected by routing agent:\n"
"import jwt as pyjwt\n"
"import time\n\n"
"payload = {\n"
"    'iss':   'mcp-hub',          # must match MCP_JWT_ISSUER env on MCP server\n"
"    'aud':   server['id'],       # SCOPED: only this server can accept this token\n"
"    'sub':   hub_claims['sub'],  # forwarded from hub JWT (the user's identity)\n"
"    'roles': hub_claims['roles'],# forwarded (never elevated)\n"
"    'iat':   int(time.time()),\n"
"    'exp':   int(time.time()) + 3600   # 1 hour — shorter than 8h user session\n"
"}\n"
"private_key = Path('hub_service/.keys/private.pem').read_bytes()\n"
"server_token = pyjwt.encode(payload, private_key, algorithm='RS256')\n\n"
"# Returned in /discover response:\n"
"# { servers: [{ ...server_config, server_token: '<JWT>' }], method: 'agent', intent: '...' }")

    callout(doc,"security","Why audience-scoped tokens?",
        "Each JWT carries aud = server_id. Server B rejects a token intended for Server A "
        "because the aud claim doesn't match. This means:\n"
        "  * A token intercepted in transit to Server A cannot be replayed against any other server\n"
        "  * Blast radius of a token compromise = 1 server x 1 hour\n"
        "  * Revocation is implicit — the token expires within 1 hour")

    divider(doc); spacer(doc,4)

    # ── PHASE 4
    phase_banner(doc,"4","MCP Server JWT Validation — Agent to MCP Server (Streamable HTTP)",
        "Agent opens a Streamable HTTP session. Per-server JWT attached and validated on every call.",
        C["phase4"])
    H3(doc,"Token Priority Chain")
    code(doc,
"# agent.py mcp_session() — token resolution\n"
"token = (\n"
"    server.get('server_token')     # 1st: per-server JWT from /discover  ← PREFERRED\n"
"    or server.get('api_key')       # 2nd: static key from registry DB\n"
"    or os.environ.get('MCP_API_KEY')  # 3rd: global env fallback\n"
")\n"
"auth_headers = {'Authorization': f'Bearer {token}'} if token else {}")

    H3(doc,"Two-Layer Middleware Architecture")
    body(doc,
        "Two middleware layers run in sequence on every Streamable HTTP request. "
        "They are complementary — the first validates the JWT cryptographically via the hub's "
        "JWKS endpoint; the second decodes the already-verified claims into a per-request "
        "ContextVar for RBAC without a redundant JWKS round-trip:")
    code(doc,
"# mcp_server/server.py — middleware registration\n\n"
"# Layer 1 — FastMCP JWTVerifier (cryptographic validation)\n"
"from fastmcp.server.auth.providers.jwt import JWTVerifier\n"
"verifier = JWTVerifier(\n"
"    jwks_uri = f'{HUB_URL}/.well-known/jwks.json',  # fetches RSA public key from hub\n"
"    issuer   = 'mcp-hub',\n"
"    audience = os.environ['MCP_SERVER_ID']           # e.g. 'customer-server'\n"
")\n"
"# Returns HTTP 401 if:\n"
"#   RS256 signature invalid | aud != MCP_SERVER_ID\n"
"#   iss != 'mcp-hub'        | token has expired\n\n"
"# Layer 2 — BearerClaimsMiddleware (decode for RBAC — no second JWKS fetch)\n"
"class BearerClaimsMiddleware(BaseHTTPMiddleware):\n"
"    async def dispatch(self, request, call_next):\n"
"        token = request.headers.get('Authorization','').removeprefix('Bearer ').strip()\n"
"        if token:\n"
"            # verify_signature=False is safe — JWTVerifier already validated above\n"
"            payload = jwt.decode(token, options={'verify_signature': False})\n"
"            _request_claims.set({'sub': payload['sub'],\n"
"                                  'roles': payload.get('roles', ['agent'])})\n"
"        return await call_next(request)")

    H3(doc,"Session Lifecycle — Streamable HTTP")
    code(doc,
"# agent.py mcp_session() — Streamable HTTP\n"
"from mcp.client.streamable_http import streamablehttp_client\n\n"
"async with streamablehttp_client(\n"
"    server['endpoint'],        # e.g. https://mcp.internal:9100/mcp\n"
"    headers=auth_headers       # Authorization: Bearer <per-server JWT>\n"
") as (read_stream, write_stream, _):\n"
"    async with ClientSession(read_stream, write_stream) as session:\n\n"
"        # Step 1 — initialize  (POST /mcp)\n"
"        #   JWTVerifier validates token; server returns Mcp-Session-Id in response header\n"
"        await session.initialize()\n\n"
"        # Step 2 — discover tools  (JWT + session ID sent on every call)\n"
"        tools = await session.list_tools()\n\n"
"        # Step 3 — call a tool  (JWT re-validated; require_role() enforced inside tool)\n"
"        result = await session.call_tool('customer_360', {'customer_id': 'C001'})")

    divider(doc); spacer(doc,4)

    # ── PHASE 5
    phase_banner(doc,"5","Per-Tool RBAC — Inside MCP Server Tool Functions",
        "Each tool enforces its own role check before any business logic executes.",
        C["phase5"])
    img(doc, paths["rbac"], width_cm=15.0,
        caption="Figure 5 — Per-Tool RBAC Decision Flow")

    H3(doc,"Implementation Pattern")
    code(doc,
"# mcp_server/auth.py\n"
"_request_claims: ContextVar[dict] = ContextVar('mcp_request_claims', default={})\n\n"
"def require_role(*roles: str) -> None:\n"
"    claims = _request_claims.get()\n"
"    if not claims:                          # empty = open dev mode (no auth)\n"
"        return\n"
"    user_roles = claims.get('roles', [])\n"
"    if 'admin' in user_roles:               # admin bypasses all checks\n"
"        return\n"
"    if not any(r in user_roles for r in roles):\n"
"        raise PermissionError(\n"
"            f'Role required: {list(roles)}, caller has {user_roles}'\n"
"        )  # → FastMCP serialises as JSON-RPC error, HTTP 403\n\n"
"def audit_log(tool: str, args: dict | None = None, service: str = 'mysql') -> None:\n"
"    claims = _request_claims.get()\n"
"    print(json.dumps({\n"
"        'ts':      round(time.time(), 3),\n"
"        'type':    'tool_audit',\n"
"        'tool':    tool,\n"
"        'service': service,\n"
"        'sub':     claims.get('sub', 'unknown'),\n"
"        'roles':   claims.get('roles', []),\n"
"        'args_keys': sorted(args.keys()) if args else [],  # keys only — NO values (PII)\n"
"    }))")

    H3(doc,"Standard Tool Template")
    code(doc,
"@mcp.tool()\n"
"def customer_360(customer_id: str) -> dict:\n"
"    \"\"\"\n"
"    Retrieve a full 360-degree customer profile.\n"
"    Returns: account status, segment, recent orders, open issues.\n"
"    Requires: 'agent' or 'admin' role.\n"
"    \"\"\"\n"
"    # 1. RBAC — MUST be first; before any I/O\n"
"    require_role('agent', 'admin')\n\n"
"    # 2. Audit — MUST be before data access; logs keys NOT values\n"
"    audit_log('customer_360', args={'customer_id': customer_id}, service='mysql')\n\n"
"    # 3. Input validation\n"
"    if not customer_id or len(customer_id) > 50:\n"
"        raise ValueError('customer_id must be 1-50 characters')\n\n"
"    # 4. Business logic — parameterised query; own DB credentials\n"
"    return db.query_one(\n"
"        'SELECT * FROM customer_360_view WHERE id = %s',  # semantic view, not raw table\n"
"        (customer_id,)   # parameterised — never string-format with user input\n"
"    )")

    table(doc,
        ["Step","What","Why"],
        [
            ("1 — require_role()","Check caller's roles from ContextVar", "No business logic runs without auth check; exception exits cleanly"),
            ("2 — audit_log()",  "Log tool name, service, sub, roles, args_keys (not values)","Immutable audit trail; PII-safe"),
            ("3 — validate input","Check types, lengths, allowed values",  "Prevent injection; fail fast with clear error"),
            ("4 — query semantic view","SELECT from pre-joined view, not raw table","Decouples tool API from schema; prevents accidental joins"),
        ],
        widths=[4.0,6.0,7.5])

    doc.add_page_break()

    # ===========================================================================
    # 7 · CREDENTIAL ISOLATION
    # ===========================================================================
    H1(doc,"7.  Credential Isolation Model")
    img(doc, paths["credentials"], width_cm=16.5,
        caption="Figure 6 — Credential Isolation Across All Boundaries")

    table(doc,
        ["Boundary","Credential Used","Issued By","Expiry","Never Forwarded To"],
        [
            ("Browser → Chat Server",    "Username + PBKDF2 password",          "DBA / env config", "Per request",  "Anything beyond chat server"),
            ("Chat Server → Browser",    "Hub JWT (RS256, aud=hub, 8h)",         "Hub Server",       "8 hours",      "MCP servers, database"),
            ("Agent → Hub API",          "Hub JWT (RS256, aud=hub, 8h)",         "Hub Server",       "8 hours",      "MCP servers, database"),
            ("Hub → MCP Server",         "Per-server JWT (RS256, aud=srv, 1h)",  "Hub Server",       "1 hour",       "Other MCP servers, database"),
            ("Agent → MCP Server",       "Per-server JWT (RS256, aud=srv, 1h)",  "Hub Server",       "1 hour",       "Other MCP servers, database"),
            ("MCP Server → Database",    "MYSQL_USER + MYSQL_PASSWORD",          "DBA / .env",       "Connection pool","Agent, hub, browser"),
            ("MCP Server → External API","MCP_TOOL_KEY",                         ".env file",        "Never expires", "Agent, hub, browser"),
        ],
        widths=[4.5,4.5,2.8,2.0,4.2])

    doc.add_page_break()

    # ===========================================================================
    # 8 · AUTHORIZATION
    # ===========================================================================
    H1(doc,"8.  Authorization Framework")
    body(doc,
        "Authorization operates at three independent levels. Passing one level does not "
        "bypass the others — all three must pass for a tool call to succeed.")

    H2(doc,"8.1  Three-Level Authorization Model")
    table(doc,
        ["Level","Enforced In","Mechanism","Granularity"],
        [
            ("Hub RBAC",     "hub_server.py _classify_token()", "roles in hub JWT vs endpoint requirement",    "Per HTTP endpoint"),
            ("Server JWT",   "MCP server JWTVerifier",          "aud · iss · exp · RS256 signature",          "Per server (wrong aud = 401)"),
            ("Tool RBAC",    "Tool function require_role()",     "roles in per-server JWT via ContextVar",     "Per tool function"),
        ],
        widths=[3.5,5.0,6.0,4.0])

    H2(doc,"8.2  Role Design Guidelines")
    bullet(doc,"Define roles at the business capability level (e.g. 'pricing-analyst'), not technical level ('db-read').",label="Meaningful roles:  ")
    bullet(doc,"Use narrow roles for sensitive operations — prefer 'write:pricing' over broad 'admin' for non-operators.",label="Least privilege:  ")
    bullet(doc,"admin role bypasses ALL tool-level checks — reserve for operators only, not agents.",label="Admin guard:  ")
    bullet(doc,"Forward only the roles the user holds in the hub JWT — never elevate in the per-server JWT.",label="No elevation:  ")
    bullet(doc,"Audit every require_role() decision (pass or fail) for compliance traceability.",label="Audit all decisions:  ")
    bullet(doc,"If a token has no claims (empty ContextVar), tool executes without checks — dev mode only. NEVER in production.",label="Dev mode risk:  ",label_color="9B1C1C")

    H2(doc,"8.3  Scope Extension (Advanced Pattern)")
    body(doc,
        "For finer-grained control beyond roles, extend the JWT payload with a scopes "
        "claim (analogous to OAuth 2.0 scopes). Add a require_scope() function alongside "
        "require_role() for data-level access control.")
    code(doc,
"# Extended JWT payload\n"
'{ "sub": "alice", "roles": ["agent"],\n'
'  "scopes": ["read:customer", "read:pricing"],   # ← scope extension\n'
'  "aud": "customer-server", "exp": now + 3600 }\n\n'
"# Tool enforcement\n"
"def order_create(customer_id: str, amount: float) -> dict:\n"
"    require_role('agent')            # role check\n"
"    require_scope('write:orders')    # scope check — more granular\n"
"    audit_log('order_create', args={'customer_id': customer_id})\n"
"    ...")

    doc.add_page_break()

    # ===========================================================================
    # 9 · TOOLS, RESOURCES & PROMPTS
    # ===========================================================================
    H1(doc,"9.  Tools, Resources & Prompts — Standards")

    H2(doc,"9.1  Tool Design Standards")
    table(doc,
        ["Standard","Requirement","Anti-Pattern"],
        [
            ("Naming",          "snake_case noun_verb (customer_lookup, order_create)",     "Generic: 'query', 'execute', 'run'"),
            ("Description",     "First sentence = what a user would ask (for LLM routing)","Vague: 'Does stuff with orders'"),
            ("inputSchema",     "Every parameter typed + described; required fields explicit","Missing schema; no descriptions"),
            ("require_role()",  "First statement — before any I/O",                        "After business logic — auth bypass on exception"),
            ("audit_log()",     "Immediately after require_role() — before data access",    "Skipped; or after DB call"),
            ("SQL queries",     "Parameterised only; query semantic views not raw tables", "f-string SQL with user input"),
            ("Output",          "Consistent typed schema; Pydantic or TypedDict",          "Untyped dict; changes per code path"),
            ("Error handling",  "Raise specific exceptions (ValueError, PermissionError)","Silent except: pass; or raw Exception"),
            ("Single responsibility","One tool = one action",                              "Mode parameter that branches behaviour"),
            ("Input caps",      "Server-side max (e.g. limit <= 100) regardless of caller","Trust caller-provided limits"),
        ],
        widths=[3.5,6.5,7.5])

    H2(doc,"9.2  Resource Standards")
    bullet(doc,"Use URI templates (RFC 6570) for parameterised resources: data://orders/{order_id}",label="URIs:  ")
    bullet(doc,"Declare mimeType on every resource (text/markdown, application/json, text/csv).",label="MIME type:  ")
    bullet(doc,"Resources must be read-only. If computation or arguments are needed, use a Tool.",label="Read-only:  ")
    bullet(doc,"Support resources/subscribe for data that changes frequently (rates, status).",label="Subscriptions:  ")

    H2(doc,"9.3  Prompt Standards")
    bullet(doc,"Version prompts — include version in the prompt name: customer_briefing_v2.",label="Versioning:  ")
    bullet(doc,"Declare all arguments with required flag and description.",label="Arguments:  ")
    bullet(doc,"Test prompts against the target LLM — prompt effectiveness is model-dependent.",label="Testing:  ")
    bullet(doc,"Use prompts for complex multi-step reasoning where template structure is stable.",label="Use case:  ")

    doc.add_page_break()

    # ===========================================================================
    # 10 · OBSERVABILITY
    # ===========================================================================
    H1(doc,"10.  Observability & Audit Trail")

    H2(doc,"10.1  Four-Way Event Fan-Out")
    body(doc,
        "Every log_event() call in the hub fans out to four independent sinks. No single "
        "sink failure blocks the others — degraded observability is always preferred over "
        "dropped requests.")
    code(doc,
"# hub_service/observability.py\n"
"def log_event(event_type: str, **data) -> None:\n"
"    entry = {'ts': round(time.time(), 3), 'type': event_type, **data}\n\n"
"    # Sink 1: in-memory ring buffer (maxlen=500) — GET /api/logs fast path\n"
"    with _lock: _buffer.append(entry)\n\n"
"    # Sink 2: stdout (print) — docker logs / console\n"
"    print(json.dumps(entry, default=str))\n\n"
"    # Sink 3: JSONL file (line-buffered) — logs/hub.log\n"
"    #   opened ONCE at module import time (not per-event) → no per-call fd overhead\n"
"    _write_log(entry)\n\n"
"    # Sink 4: MySQL hub_events table\n"
"    #   _db_failed = True permanently after first failure (no retry flood)\n"
"    #   restart hub to re-enable MySQL logging after DB recovers\n"
"    if not _db_failed:\n"
"        _write_db(entry)")

    H2(doc,"10.2  Event Reference")
    table(doc,
        ["Event Type","When","Key Fields","Example Use"],
        [
            ("auth",       "Every JWT validation",        "ts · sub · roles · path · valid · _error",     "Detect auth failures; trace identity per request"),
            ("request",    "Every HTTP request",          "ts · method · path · status · latency_ms",     "Performance monitoring; error rate alerting"),
            ("routing",    "Every /discover call",        "ts · method · server_id · reason · intent",    "Understand which servers are selected and why"),
            ("tool_audit", "Before each tool executes",   "ts · tool · service · sub · roles · args_keys","Compliance — who called what and when (PII-safe)"),
            ("error",      "Unhandled exceptions",        "ts · message · traceback · path",              "Debug production issues; set up error alerting"),
        ],
        widths=[3.0,4.0,6.0,5.5])

    callout(doc,"warning","PII in audit logs",
        "audit_log() records args_keys (parameter names) but NOT argument values. "
        "For example: args_keys=['customer_id'] not args_values=['C001']. "
        "This gives traceability without exposing customer data in log streams. "
        "Never log raw tool argument values — they will contain PII.")

    H2(doc,"10.3  Log Retention & Query")
    bullet(doc,"GET /api/logs?n=100 — returns last N events (MySQL first, in-memory fallback).",label="API:  ")
    bullet(doc,"logs/hub.log — JSONL; survives restarts; pipe to jq, grep, or ELK/Splunk.",label="File:  ")
    bullet(doc,"hub_events table — indexed on (type, ts); use for compliance queries and dashboards.",label="Database:  ")
    bullet(doc,"MySQL sink disabled permanently on first failure; restart hub to re-enable after DB recovers.",label="Resilience:  ")

    doc.add_page_break()

    # ===========================================================================
    # 11 · SECURITY BEST PRACTICES
    # ===========================================================================
    H1(doc,"11.  Security Best Practices")

    H2(doc,"11.1  Credential Management")
    bullet(doc,"Store all secrets in environment variables or a secrets manager (Vault, AWS Secrets Manager).",label="Storage:  ")
    bullet(doc,"Add .env and all .pem key files to .gitignore BEFORE the first git commit.",label="Git:  ")
    bullet(doc,"Rotate RSA key pair annually or on any suspected compromise; update hub + wait for MCP servers to refresh JWKS cache.",label="Key rotation:  ")
    bullet(doc,"Use a separate MySQL user per MCP server with minimum permissions (SELECT-only for read servers).",label="DB users:  ")
    bullet(doc,"Never log credential values — log only sub, roles, path from decoded claims.",label="Log hygiene:  ")

    H2(doc,"11.2  Token Security")
    bullet(doc,"Use RS256 (asymmetric) always — never HS256 (HMAC shared secret) in multi-server deployments.",label="Algorithm:  ")
    bullet(doc,"Always set aud on per-server tokens. Never issue tokens with no audience.",label="Audience:  ")
    bullet(doc,"Keep MCP token expiry short (1 hour). Refresh by calling /discover again.",label="Expiry:  ")
    bullet(doc,"Validate exp, iss, aud, and signature on every request — no shortcuts.",label="Validation:  ")
    bullet(doc,"Never log full JWT values — they are Bearer credentials. Log sub and roles from decoded payload only.",label="No logging tokens:  ")

    H2(doc,"11.3  Network Security")
    bullet(doc,"All inter-service communication must use TLS (HTTPS) in production.",label="TLS:  ")
    bullet(doc,"JWKS endpoint must be HTTPS — public key served over plain HTTP can be MITM'd.",label="JWKS:  ")
    bullet(doc,"MCP server endpoints should not be publicly reachable — hub network access only.",label="Isolation:  ")
    bullet(doc,"Use a reverse proxy (nginx / Caddy / AWS ALB) for TLS termination + rate limiting.",label="Proxy:  ")

    H2(doc,"11.4  Security Properties Summary")
    table(doc,
        ["Property","Implementation","Threat Mitigated"],
        [
            ("Token forgery prevention",     "RS256 asymmetric — private key never leaves hub",           "Forged tokens rejected at signature verification"),
            ("Cross-server token replay",    "Per-server aud claim; servers reject wrong audience",       "Stolen token unusable on any other server"),
            ("Token theft window",           "1-hour MCP token expiry; re-minted on next /discover",     "Short window limits damage from interception"),
            ("Credential isolation",         "Each layer uses its own credential type",                   "One credential compromise doesn't cascade"),
            ("Brute-force resistance",       "PBKDF2-SHA256 (200k iter) + per-username rate limiting",   "Offline dictionary and online brute-force attacks"),
            ("Accidental open access",       "MCP_AUTH_ENABLED=true by default; must explicitly disable","Dev config leaking into staging/production"),
            ("Secret file in git",           ".gitignore covers .env + private.pem + public.pem",        "Credential exposure in version control history"),
            ("PII in logs",                  "audit_log() logs args_keys not values",                    "Customer data not in log streams or SIEM"),
            ("Dev mode implicit admin",       "Only when MCP_SERVER_ID not set AND no token provided",    "Restricted to environments with no MCP_SERVER_ID set"),
        ],
        widths=[5.0,6.0,6.5])

    doc.add_page_break()

    # ===========================================================================
    # 12 · PATTERNS & ANTI-PATTERNS
    # ===========================================================================
    H1(doc,"12.  Standard Patterns & Anti-Patterns")

    H2(doc,"12.1  Recommended Patterns")
    H3(doc,"Pattern 1 — New Routing Agent Per /discover Request")
    body(doc,"Always create a fresh LangGraph ReAct routing agent per request. Never share agent instances across concurrent calls.")
    code(doc,
"# CORRECT — new instance per call\n"
"def _agent_route(query, servers):\n"
"    agent = create_react_agent(llm, [_make_routing_tool(servers)])  # fresh\n"
"    return agent.invoke({'messages': [('user', prompt)]})\n\n"
"# WRONG — shared instance corrupts concurrent requests\n"
"_SHARED_AGENT = create_react_agent(llm, [tool])  # module-level singleton — DON'T DO THIS")

    H3(doc,"Pattern 2 — Background Task Decoupled from SSE Lifetime")
    body(doc,
        "Create the agent asyncio.Task before opening the SSE generator. "
        "If the browser disconnects, the generator closes but the Task continues — "
        "the final answer is always saved to MySQL.")
    code(doc,
"# chat_server.py\n"
"async def stream_response(query, session_id):\n"
"    queue = asyncio.Queue()\n"
"    task  = asyncio.create_task(run_agent(query, queue.put))  # created BEFORE generator\n"
"    _bg_tasks[session_id] = task\n"
"    try:\n"
"        async for event in generate_sse(queue):   # browser reads this\n"
"            yield event\n"
"    finally:\n"
"        pass  # Task outlives generator; saves answer to DB regardless")

    H3(doc,"Pattern 3 — Semantic View Isolation for Tools")
    body(doc,"Tools query semantic views, not raw tables. Schema changes require only view updates; tool code is unchanged.")
    code(doc,
"-- Raw tables (tools NEVER query directly)\n"
"customers, orders, addresses, preferences, credit_ratings ...\n\n"
"-- Semantic view (what tools query)\n"
"CREATE VIEW customer_360_view AS\n"
"  SELECT c.id, c.name, c.segment, c.status,\n"
"         COUNT(o.id) AS order_count, MAX(o.date) AS last_order\n"
"  FROM customers c LEFT JOIN orders o ON o.customer_id = c.id\n"
"  GROUP BY c.id;")

    H3(doc,"Pattern 4 — Registry Cache with Manual Invalidation")
    body(doc,"Cache the server registry for 60s; expose a flush endpoint for immediate admin changes.")
    code(doc,
"_hub_cache: dict | None = None\n"
"_hub_cache_ts: float = 0\n"
"HUB_CACHE_TTL = 60  # seconds\n\n"
"def load_hub() -> list[dict]:\n"
"    global _hub_cache, _hub_cache_ts\n"
"    if _hub_cache and (time.time() - _hub_cache_ts) < HUB_CACHE_TTL:\n"
"        return _hub_cache\n"
"    _hub_cache = db_load_active_servers()\n"
"    _hub_cache_ts = time.time()\n"
"    return _hub_cache\n\n"
"# POST /api/hub/refresh\n"
"def refresh():\n"
"    global _hub_cache\n"
"    _hub_cache = None  # bust immediately")

    H2(doc,"12.2  Anti-Patterns to Avoid")
    table(doc,
        ["Anti-Pattern","Risk","Correct Approach"],
        [
            ("HS256 shared secret across servers",  "Compromised server leaks key for all",              "RS256 — public key only on servers"),
            ("Single token for all servers",         "Full blast radius on token theft",                  "Per-server aud-scoped JWTs from /discover"),
            ("require_role() after I/O",             "Data accessed before auth check; partial leaks",   "require_role() as FIRST statement"),
            ("String-formatted SQL",                 "SQL injection via tool arguments",                  "Parameterised queries / ORM only"),
            ("Logging full JWT value",               "Bearer tokens in logs = instant credential leak",   "Log sub + roles from decoded claims only"),
            ("Logging tool arg values",              "PII (customer data) in log streams",                "Log args_keys (parameter names) only"),
            ("Shared routing agent instance",        "Concurrent state corruption → wrong routing",       "New agent instance per /discover call"),
            ("MCP_AUTH_ENABLED=false in staging",    "Unauthenticated callers get admin access",          "Always true; disable only for local dev"),
            ("Committing .env or .pem to git",       "Credential in git history — forever exposed",       ".gitignore before first commit; verify with git diff --cached"),
            ("No server-side input caps",            "Caller requests limit=999999; OOM or DB overload",  "Always cap server-side regardless of caller"),
            ("Silently swallowing exceptions",       "Auth failures hidden; broken audit trail",          "Log and re-raise; return typed error responses"),
        ],
        widths=[5.0,5.0,7.5])

    doc.add_page_break()

    # ===========================================================================
    # 13 · DEPLOYMENT
    # ===========================================================================
    H1(doc,"13.  Deployment & Configuration Reference")

    H2(doc,"13.1  Environment Variables")
    table(doc,
        ["Variable","Service","Required","Description"],
        [
            ("MCP_AUTH_ENABLED",   "MCP Server", "Yes (prod)", "Set 'true' always in non-dev. 'false' = completely open."),
            ("MCP_SERVER_ID",      "MCP Server", "Yes (prod)", "This server's unique ID — must match registry id field; used as JWT aud."),
            ("HUB_SERVER_URL",     "MCP Server", "Yes",        "Hub base URL for JWKS fetch, e.g. https://hub.internal:8090"),
            ("MCP_JWT_ISSUER",     "MCP Server", "Yes",        "Expected JWT issuer string — must match hub config (default: mcp-hub)."),
            ("MCP_TOOL_KEY",       "MCP Server", "If ext API", "Key for MCP server → external HTTP API calls; never forwarded."),
            ("MYSQL_HOST",         "Hub + MCP",  "Yes",        "Database host (default: 127.0.0.1)."),
            ("MYSQL_PORT",         "Hub + MCP",  "Yes",        "Database port (default: 3306)."),
            ("MYSQL_USER",         "Hub + MCP",  "Yes",        "Database username — use separate user per service."),
            ("MYSQL_PASSWORD",     "Hub + MCP",  "Yes",        "Database password — store in secrets manager in production."),
            ("MYSQL_DATABASE",     "Hub + MCP",  "Yes",        "Target database name (e.g. fab_semantic)."),
            ("CHAT_USERS",         "Chat Server","Yes",        "Comma-separated user:password pairs for initial seeding."),
            ("OLLAMA_BASE_URL",    "Hub",        "If LLM",     "Local LLM endpoint for routing agent (default: http://localhost:11434/v1)."),
            ("HUB_JWT_EXPIRY_HOURS","Hub",       "No",         "Hub token expiry hours (default: 8). MCP token always 1 hour."),
        ],
        widths=[4.5,2.8,2.5,8.7])

    H2(doc,"13.2  Production Readiness Checklist")
    table(doc,
        ["Area","Check","Status"],
        [
            ("Network",       "TLS/HTTPS on all inter-service endpoints", "[ ]"),
            ("Auth",          "MCP_AUTH_ENABLED=true on all MCP servers", "[ ]"),
            ("Auth",          "MCP_SERVER_ID set on all MCP servers",     "[ ]"),
            ("Auth",          "MCP_JWT_ISSUER matches hub config",        "[ ]"),
            ("Keys",          "RSA key pair generated; private.pem accessible only to hub", "[ ]"),
            ("Keys",          "JWKS endpoint reachable by all MCP servers over HTTPS",      "[ ]"),
            ("Secrets",       ".env and .pem excluded from version control",               "[ ]"),
            ("Secrets",       "Secrets in vault / secret manager; not in env files on servers","[ ]"),
            ("Database",      "pool_recycle <= 1800s (MySQL wait_timeout guard)",          "[ ]"),
            ("Database",      "hub_events table exists and is indexed",                    "[ ]"),
            ("Observability", "logs/ directory writable by hub process",                   "[ ]"),
            ("Observability", "Log aggregator (Splunk / ELK) ingesting logs/hub.log",      "[ ]"),
            ("Security",      "Rate limiting active on /login endpoint",                   "[ ]"),
            ("Security",      "MCP server endpoints not publicly reachable",               "[ ]"),
            ("Operations",    "GET /health monitored by infrastructure health check",      "[ ]"),
            ("Operations",    "Background task cleanup implemented (_bg_tasks TTL)",       "[ ]"),
        ],
        widths=[3.0,11.0,2.5])

    # ===========================================================================
    # 14 · KEY FILES
    # ===========================================================================
    H1(doc,"14.  Key Files Reference")
    table(doc,
        ["File","Role","Notes"],
        [
            ("hub_service/hub_server.py",             "FastAPI hub: registry, JWT, routing, Admin UI", "~2900 lines; entry point for hub process"),
            ("hub_service/db.py",                     "SQLAlchemy engine factory",                     "pool_recycle=1800; loads .env at import time"),
            ("hub_service/observability.py",          "4-way event fan-out logger",                    "Permanent MySQL skip on first failure"),
            ("hub_service/.keys/private.pem",         "RSA private key — signs all JWTs",              "NEVER share or commit to git"),
            ("hub_service/.keys/public.pem",          "RSA public key — served at JWKS endpoint",      "Safe to share; published publicly"),
            ("hub_service/mcp-hub.json",              "Declarative server registry source file",        "Seeded into MySQL by seed_hub_db.py"),
            ("chat_service/chat_server.py",           "Chat UI SPA server",                            "User auth; SSE; background tasks"),
            ("agent.py",                              "LangGraph ReAct orchestrator",                  "Hub discovery; mcp_session(); astream_events"),
            ("datalayer-as-service/mcp_server/auth.py","JWT verification, RBAC, audit logging",        "require_role(); audit_log(); BearerClaimsMiddleware"),
            ("datalayer-as-service/mcp_server/server.py","FastMCP server setup + middleware wiring",   "JWTVerifier + BearerClaimsMiddleware registration"),
            ("datalayer-as-service/mcp_server/tools.py","MCP tool implementations",                    "All @mcp.tool() definitions"),
            ("datalayer-as-service/mcp_server/db.py", "MySQL connection for MCP data tools",           "SQLAlchemy; queries semantic views"),
            ("scripts/seed_hub_db.py",                "Idempotent DB seeder",                          "UPSERT: re-run safe; re-activates disabled servers"),
            ("datalayer-as-service/.env",             "MySQL + MCP server config",                     "NEVER commit to git"),
            ("logs/hub.log",                          "JSONL structured event log",                    "Line-buffered; survives restarts"),
            ("ARCHITECTURE.md",                       "ASCII architecture reference",                  "Component diagrams; design decisions"),
            ("AUTH.md",                               "JWT auth deep-dive",                            "Standard JWT-for-MCP pattern mapping"),
            ("RUNBOOK.md",                            "Operational playbook",                          "Start / stop / debug procedures"),
        ],
        widths=[5.5,5.5,6.5])

    # Footer
    spacer(doc,16)
    p=doc.add_paragraph()
    p.paragraph_format.space_before=Pt(0); p.paragraph_format.space_after=Pt(0)
    _para_border_bottom(p,"DDEAF7","6")
    spacer(doc,4)
    fp=doc.add_paragraph(); fp.alignment=WD_ALIGN_PARAGRAPH.CENTER
    _run(fp,
        f"MCP Hub Design Document  |  Version 2.0  |  "
        f"{datetime.date.today().strftime('%B %d, %Y')}  |  Internal",
        italic=True, size=9, color="999999")

    doc.save(str(OUT))
    print(f"Saved: {OUT}")


if __name__ == "__main__":
    build()
