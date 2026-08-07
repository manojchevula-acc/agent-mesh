"""
scripts/generate_design_doc.py
--------------------------------
Generates MCP_Hub_Design_Document.docx in the project root.
Run:  python scripts/generate_design_doc.py
"""

from pathlib import Path
from docx import Document
from docx.shared import Pt, RGBColor, Inches, Cm
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.enum.table import WD_TABLE_ALIGNMENT
from docx.oxml.ns import qn
from docx.oxml import OxmlElement
import datetime

OUT = Path(__file__).parent.parent / "MCP_Hub_Design_Document.docx"

# ---------------------------------------------------------------------------
# Color palette (all stored as hex strings to avoid RGBColor attribute issues)
# ---------------------------------------------------------------------------
C = {
    "navy":     "1F3864",
    "blue":     "2672C4",
    "dkblue":   "1B4F8A",
    "teal":     "006B6B",
    "green":    "1E6B30",
    "ltgreen":  "E7F3EC",
    "amber":    "7D4200",
    "ltamber":  "FFF3CD",
    "red":      "9B1C1C",
    "ltred":    "FDECEA",
    "purple":   "4B0082",
    "ltpurple": "F3E8FF",
    "grey":     "404040",
    "ltgrey":   "F5F5F5",
    "midgrey":  "D9D9D9",
    "white":    "FFFFFF",
    "ltblue":   "DDEAF7",
    "code_bg":  "F0F0F0",
    "heading_row": "1F3864",
    "alt_row":  "EFF4FC",
}

def rgb(hex_str):
    h = hex_str.lstrip("#")
    return RGBColor(int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16))


# ---------------------------------------------------------------------------
# XML helpers
# ---------------------------------------------------------------------------

def set_cell_bg(cell, hex_color: str):
    tc = cell._tc
    tcPr = tc.get_or_add_tcPr()
    for old in tcPr.findall(qn("w:shd")):
        tcPr.remove(old)
    shd = OxmlElement("w:shd")
    shd.set(qn("w:val"),   "clear")
    shd.set(qn("w:color"), "auto")
    shd.set(qn("w:fill"),  hex_color.upper())
    tcPr.append(shd)


def set_para_border(p, bottom_color="CCCCCC", bottom_sz="6"):
    pPr = p._p.get_or_add_pPr()
    pBdr = OxmlElement("w:pBdr")
    bot = OxmlElement("w:bottom")
    bot.set(qn("w:val"),   "single")
    bot.set(qn("w:sz"),    bottom_sz)
    bot.set(qn("w:space"), "1")
    bot.set(qn("w:color"), bottom_color)
    pBdr.append(bot)
    pPr.append(pBdr)


def set_table_borders(table, color="BFBFBF", sz="4"):
    for row in table.rows:
        for cell in row.cells:
            tc = cell._tc
            tcPr = tc.get_or_add_tcPr()
            tcBorders = OxmlElement("w:tcBorders")
            for edge in ("top", "left", "bottom", "right"):
                tag = OxmlElement(f"w:{edge}")
                tag.set(qn("w:val"),   "single")
                tag.set(qn("w:sz"),    sz)
                tag.set(qn("w:space"), "0")
                tag.set(qn("w:color"), color)
                tcBorders.append(tag)
            tcPr.append(tcBorders)


def add_run(para, text, bold=False, italic=False, size=11,
            color="404040", font="Calibri", underline=False):
    run = para.add_run(text)
    run.bold      = bold
    run.italic    = italic
    run.underline = underline
    run.font.name = font
    run.font.size = Pt(size)
    run.font.color.rgb = rgb(color)
    return run


# ---------------------------------------------------------------------------
# Layout primitives
# ---------------------------------------------------------------------------

def heading1(doc, text):
    p = doc.add_paragraph()
    p.paragraph_format.space_before = Pt(22)
    p.paragraph_format.space_after  = Pt(6)
    set_para_border(p, bottom_color="2672C4", bottom_sz="12")
    add_run(p, text, bold=True, size=18, color="1F3864")
    return p


def heading2(doc, text):
    p = doc.add_paragraph()
    p.paragraph_format.space_before = Pt(16)
    p.paragraph_format.space_after  = Pt(4)
    add_run(p, text, bold=True, size=14, color="2672C4")
    return p


def heading3(doc, text):
    p = doc.add_paragraph()
    p.paragraph_format.space_before = Pt(10)
    p.paragraph_format.space_after  = Pt(2)
    add_run(p, text, bold=True, size=12, color="1F3864")
    return p


def body_para(doc, text, indent_cm=0):
    p = doc.add_paragraph()
    p.paragraph_format.space_after = Pt(6)
    p.paragraph_format.left_indent = Cm(indent_cm)
    add_run(p, text, size=11, color="333333")
    return p


def bullet_item(doc, text, level=0, bold_prefix=None):
    p = doc.add_paragraph()
    p.paragraph_format.space_after  = Pt(4)
    p.paragraph_format.left_indent  = Cm(0.75 + level * 0.65)
    p.paragraph_format.first_line_indent = Cm(-0.4)
    marker = "▪" if level > 0 else "●"
    add_run(p, f"{marker}  ", bold=True, size=10, color="2672C4")
    if bold_prefix:
        add_run(p, bold_prefix, bold=True, size=11, color="1F3864")
    add_run(p, text, size=11, color="333333")
    return p


def numbered_item(doc, num, text, bold_prefix=None):
    p = doc.add_paragraph()
    p.paragraph_format.space_after  = Pt(5)
    p.paragraph_format.left_indent  = Cm(0.9)
    p.paragraph_format.first_line_indent = Cm(-0.6)
    add_run(p, f"{num}.  ", bold=True, size=11, color="2672C4")
    if bold_prefix:
        add_run(p, bold_prefix, bold=True, size=11, color="1F3864")
    add_run(p, text, size=11, color="333333")
    return p


def code_para(doc, text, indent_cm=0.5):
    """Monospaced code block with light grey background via table."""
    tbl = doc.add_table(rows=1, cols=1)
    tbl.alignment = WD_TABLE_ALIGNMENT.LEFT
    cell = tbl.cell(0, 0)
    set_cell_bg(cell, "F0F0F0")
    # thin border
    tc = cell._tc
    tcPr = tc.get_or_add_tcPr()
    for edge in ("top","left","bottom","right"):
        tcBorders = OxmlElement("w:tcBorders")
        tag = OxmlElement(f"w:{edge}")
        tag.set(qn("w:val"),   "single")
        tag.set(qn("w:sz"),    "4")
        tag.set(qn("w:space"), "0")
        tag.set(qn("w:color"), "AAAAAA")
        tcBorders.append(tag)
        tcPr.append(tcBorders)
    p = cell.paragraphs[0]
    p.paragraph_format.space_before = Pt(4)
    p.paragraph_format.space_after  = Pt(4)
    p.paragraph_format.left_indent  = Cm(0.2)
    run = p.add_run(text)
    run.font.name = "Courier New"
    run.font.size = Pt(8.5)
    run.font.color.rgb = rgb("1A1A2E")
    doc.add_paragraph()   # spacing after code block
    return tbl


def callout(doc, kind, title, text):
    """Colored callout box: kind = 'note'|'warning'|'example'|'definition'|'important'"""
    cfg = {
        "note":       ("ltblue",   "dkblue",  "ℹ  NOTE",        "1B4F8A"),
        "warning":    ("ltred",    "red",     "⚠  WARNING",      "9B1C1C"),
        "example":    ("ltgreen",  "green",   "✎  EXAMPLE",      "1E6B30"),
        "definition": ("ltpurple", "purple",  "◈  DEFINITION",   "4B0082"),
        "important":  ("ltamber",  "amber",   "★  IMPORTANT",    "7D4200"),
    }
    bg, border, label, label_color = cfg.get(kind, cfg["note"])

    tbl = doc.add_table(rows=1, cols=1)
    tbl.alignment = WD_TABLE_ALIGNMENT.LEFT
    cell = tbl.cell(0, 0)
    set_cell_bg(cell, C[bg])

    # left border accent via shade color trick — set left border thick
    tc = cell._tc
    tcPr = tc.get_or_add_tcPr()
    tcBorders = OxmlElement("w:tcBorders")
    left = OxmlElement("w:left")
    left.set(qn("w:val"),   "single")
    left.set(qn("w:sz"),    "24")
    left.set(qn("w:space"), "0")
    left.set(qn("w:color"), C[border])
    tcBorders.append(left)
    for edge in ("top","bottom","right"):
        tag = OxmlElement(f"w:{edge}")
        tag.set(qn("w:val"),   "single")
        tag.set(qn("w:sz"),    "2")
        tag.set(qn("w:space"), "0")
        tag.set(qn("w:color"), C[border])
        tcBorders.append(tag)
    tcPr.append(tcBorders)

    p = cell.paragraphs[0]
    p.paragraph_format.space_before = Pt(2)
    p.paragraph_format.space_after  = Pt(2)
    p.paragraph_format.left_indent  = Cm(0.2)

    r1 = p.add_run(f"{label}")
    r1.bold = True
    r1.font.name = "Calibri"
    r1.font.size = Pt(9)
    r1.font.color.rgb = rgb(label_color)

    if title:
        r2 = p.add_run(f" — {title}\n")
        r2.bold = True
        r2.font.name = "Calibri"
        r2.font.size = Pt(10)
        r2.font.color.rgb = rgb(label_color)
    else:
        p.add_run("\n").font.size = Pt(4)

    r3 = p.add_run(text)
    r3.font.name = "Calibri"
    r3.font.size = Pt(10)
    r3.font.color.rgb = rgb("333333")

    doc.add_paragraph()
    return tbl


def rich_table(doc, headers, rows, col_widths_cm=None):
    """Styled table with navy header row and alternating body rows."""
    tbl = doc.add_table(rows=1 + len(rows), cols=len(headers))
    tbl.alignment = WD_TABLE_ALIGNMENT.LEFT
    tbl.style = "Table Grid"

    # Header row
    hrow = tbl.rows[0]
    for i, hdr in enumerate(headers):
        cell = hrow.cells[i]
        set_cell_bg(cell, C["heading_row"])
        p = cell.paragraphs[0]
        p.alignment = WD_ALIGN_PARAGRAPH.CENTER
        r = p.add_run(hdr)
        r.bold = True
        r.font.name = "Calibri"
        r.font.size = Pt(10)
        r.font.color.rgb = rgb("FFFFFF")

    # Data rows
    for ri, row_data in enumerate(rows):
        drow = tbl.rows[ri + 1]
        bg = C["alt_row"] if ri % 2 == 1 else "FFFFFF"
        for ci, val in enumerate(row_data):
            cell = drow.cells[ci]
            set_cell_bg(cell, bg)
            p = cell.paragraphs[0]
            if isinstance(val, dict):
                text  = val.get("text", "")
                bold  = val.get("bold", False)
                color = val.get("color", "333333")
                align = val.get("align", WD_ALIGN_PARAGRAPH.LEFT)
            else:
                text, bold, color, align = str(val), False, "333333", WD_ALIGN_PARAGRAPH.LEFT
            p.alignment = align
            r = p.add_run(text)
            r.font.name = "Calibri"
            r.font.size = Pt(10)
            r.bold = bold
            r.font.color.rgb = rgb(color)

    set_table_borders(tbl, color="CCCCCC", sz="4")

    if col_widths_cm:
        for i, w in enumerate(col_widths_cm):
            for row in tbl.rows:
                row.cells[i].width = Cm(w)

    doc.add_paragraph()
    return tbl


def phase_box(doc, phase_num, phase_name, description):
    """Numbered phase banner for auth flow stages."""
    tbl = doc.add_table(rows=1, cols=2)
    tbl.alignment = WD_TABLE_ALIGNMENT.LEFT

    # Left: phase number badge
    num_cell = tbl.cell(0, 0)
    set_cell_bg(num_cell, C["navy"])
    num_cell.width = Cm(2.0)
    p1 = num_cell.paragraphs[0]
    p1.alignment = WD_ALIGN_PARAGRAPH.CENTER
    r1 = p1.add_run(f"PHASE\n{phase_num}")
    r1.bold = True
    r1.font.name = "Calibri"
    r1.font.size = Pt(13)
    r1.font.color.rgb = rgb("FFFFFF")

    # Right: name + description
    txt_cell = tbl.cell(0, 1)
    set_cell_bg(txt_cell, C["ltblue"])
    p2 = txt_cell.paragraphs[0]
    r2 = p2.add_run(phase_name + "\n")
    r2.bold = True
    r2.font.name = "Calibri"
    r2.font.size = Pt(12)
    r2.font.color.rgb = rgb("1F3864")
    r3 = p2.add_run(description)
    r3.font.name = "Calibri"
    r3.font.size = Pt(10)
    r3.font.color.rgb = rgb("333333")

    doc.add_paragraph()
    return tbl


def section_divider(doc):
    p = doc.add_paragraph()
    p.paragraph_format.space_before = Pt(6)
    p.paragraph_format.space_after  = Pt(6)
    set_para_border(p, bottom_color="DDEAF7", bottom_sz="8")
    return p


def spacer(doc, pt=6):
    p = doc.add_paragraph()
    p.paragraph_format.space_before = Pt(pt)
    p.paragraph_format.space_after  = Pt(0)
    return p


# ---------------------------------------------------------------------------
# Main document
# ---------------------------------------------------------------------------

def build():
    doc = Document()

    for section in doc.sections:
        section.top_margin    = Cm(2.2)
        section.bottom_margin = Cm(2.2)
        section.left_margin   = Cm(2.8)
        section.right_margin  = Cm(2.8)

    doc.styles["Normal"].font.name = "Calibri"
    doc.styles["Normal"].font.size = Pt(11)

    # =========================================================================
    # COVER PAGE
    # =========================================================================
    spacer(doc, 30)

    # Blue accent bar (thin table)
    bar = doc.add_table(rows=1, cols=1)
    set_cell_bg(bar.cell(0,0), C["navy"])
    bar.cell(0,0).width = Cm(16)
    p = bar.cell(0,0).paragraphs[0]
    p.paragraph_format.space_before = Pt(4)
    p.paragraph_format.space_after  = Pt(4)

    spacer(doc, 10)

    tp = doc.add_paragraph()
    tp.alignment = WD_ALIGN_PARAGRAPH.LEFT
    add_run(tp, "MCP Hub", bold=True, size=36, color="1F3864")

    sp = doc.add_paragraph()
    sp.alignment = WD_ALIGN_PARAGRAPH.LEFT
    add_run(sp, "Solution Design & Architecture", bold=False, size=22, color="2672C4")

    spacer(doc, 8)

    desc = doc.add_paragraph()
    desc.alignment = WD_ALIGN_PARAGRAPH.LEFT
    add_run(desc,
        "Comprehensive design guide covering authentication, authorization,\n"
        "server governance, tool management, and security best practices\n"
        "for Model Context Protocol (MCP) hub deployments.",
        size=12, color="555555")

    spacer(doc, 20)

    meta = doc.add_table(rows=4, cols=2)
    meta_data = [
        ("Version",  "1.0"),
        ("Date",     datetime.date.today().strftime("%B %d, %Y")),
        ("Audience", "Solution Architects, Security Engineers, Backend Developers"),
        ("Status",   "Internal — Draft for Review"),
    ]
    for ri, (k, v) in enumerate(meta_data):
        set_cell_bg(meta.rows[ri].cells[0], C["ltblue"])
        set_cell_bg(meta.rows[ri].cells[1], "FFFFFF")
        pk = meta.rows[ri].cells[0].paragraphs[0]
        pv = meta.rows[ri].cells[1].paragraphs[0]
        add_run(pk, k, bold=True, size=10, color="1F3864")
        add_run(pv, v, size=10, color="333333")
    set_table_borders(meta, color="DDEAF7", sz="4")

    doc.add_page_break()

    # =========================================================================
    # TABLE OF CONTENTS (manual)
    # =========================================================================
    heading1(doc, "Table of Contents")
    toc_items = [
        ("1", "Introduction & Purpose",                              "3"),
        ("2", "Glossary of Key Terms",                               "4"),
        ("3", "MCP Protocol Fundamentals",                           "5"),
        ("  3.1", "What is MCP?",                                    "5"),
        ("  3.2", "Tools",                                           "5"),
        ("  3.3", "Resources",                                       "6"),
        ("  3.4", "Prompts",                                         "6"),
        ("  3.5", "Transport Protocols",                             "6"),
        ("4", "MCP Hub Architecture",                                "7"),
        ("  4.1", "Hub Responsibilities",                            "7"),
        ("  4.2", "Component Overview",                              "7"),
        ("  4.3", "Request Lifecycle",                               "8"),
        ("5", "Server Registration & Governance",                    "9"),
        ("  5.1", "Server Registry Schema",                          "9"),
        ("  5.2", "Registration Process",                            "9"),
        ("  5.3", "Server Lifecycle Management",                    "10"),
        ("6", "Authentication Architecture — End to End",           "11"),
        ("  Phase 1", "User Login (Chat UI → Hub)",                 "12"),
        ("  Phase 2", "Hub API Authentication (Agent → Hub)",       "13"),
        ("  Phase 3", "Server Discovery & JWT Minting",             "14"),
        ("  Phase 4", "MCP Server JWT Validation",                  "16"),
        ("  Phase 5", "Per-Tool Role-Based Access Control",         "17"),
        ("  6.6", "Complete End-to-End Flow",                       "18"),
        ("  6.7", "Token Lifecycle & Expiry Strategy",              "19"),
        ("7", "Authorization Framework",                            "20"),
        ("8", "Tools — Design, Standards & Governance",             "21"),
        ("9", "Resources & Prompts",                                "22"),
        ("10", "Observability & Audit Trail",                       "23"),
        ("11", "Security Best Practices",                           "24"),
        ("12", "Standard Patterns & Anti-Patterns",                 "25"),
    ]
    for num, title, _ in toc_items:
        p = doc.add_paragraph()
        p.paragraph_format.space_after = Pt(3)
        indent = 1.5 if num.startswith("  ") else 0
        p.paragraph_format.left_indent = Cm(indent)
        add_run(p, f"{num.strip()}  ", bold=(not num.startswith("  ")), size=10, color="2672C4")
        add_run(p, title, bold=(not num.startswith("  ")), size=10,
                color="1F3864" if not num.startswith("  ") else "444444")

    doc.add_page_break()

    # =========================================================================
    # 1. INTRODUCTION
    # =========================================================================
    heading1(doc, "1.  Introduction & Purpose")
    body_para(doc,
        "As organisations adopt AI-powered applications, they increasingly need structured "
        "ways for AI agents to discover, authenticate with, and call specialised backend "
        "services. The Model Context Protocol (MCP) provides a standard interface for "
        "this — but deploying MCP servers at scale introduces challenges around "
        "discovery, security, governance, and observability.")
    body_para(doc,
        "An MCP Hub addresses these challenges by acting as a central control plane: "
        "a single service that maintains a registry of all MCP servers, handles "
        "intelligent routing of agent requests, issues scoped security tokens, and "
        "enforces policy — freeing individual MCP servers to focus purely on their "
        "business logic.")

    heading2(doc, "What This Document Covers")
    bullet_item(doc, "The full MCP protocol vocabulary: tools, resources, and prompts")
    bullet_item(doc, "Hub architecture — components, routing, and the request lifecycle")
    bullet_item(doc, "Server registration, lifecycle management, and governance controls")
    bullet_item(doc, "End-to-end authentication across every boundary (five distinct phases)")
    bullet_item(doc, "Role-based authorization at the hub, server, and individual tool level")
    bullet_item(doc, "Observability, audit logging, and compliance event capture")
    bullet_item(doc, "Security best practices, standard patterns, and anti-patterns to avoid")

    callout(doc, "note", "Audience",
        "This document assumes familiarity with REST APIs, JWT tokens, and basic PKI concepts. "
        "All MCP-specific terminology is defined in Section 2.")

    doc.add_page_break()

    # =========================================================================
    # 2. GLOSSARY
    # =========================================================================
    heading1(doc, "2.  Glossary of Key Terms")
    body_para(doc,
        "The following terms are used throughout this document. Readers unfamiliar with "
        "any of these concepts should review this section before proceeding.")

    terms = [
        ("MCP",             "Model Context Protocol — an open standard that defines how AI agents discover "
                            "and call tools, access resources, and use prompt templates exposed by backend servers."),
        ("MCP Hub",         "A central gateway service that maintains the registry of MCP servers, handles "
                            "routing of agent requests, issues security tokens, and enforces policy."),
        ("MCP Server",      "A backend service that implements the MCP protocol, exposing tools, resources, "
                            "and/or prompts. Each server is registered in the hub registry."),
        ("Tool",            "A callable function exposed by an MCP server — analogous to an API endpoint. "
                            "Tools accept structured arguments and return structured results."),
        ("Resource",        "A URI-addressable data asset exposed by an MCP server (e.g. a database record, "
                            "file, or live data feed). Resources are read-only and fetched by URI."),
        ("Prompt",          "A reusable prompt template exposed by an MCP server. Agents can fetch and "
                            "instantiate prompts with named arguments."),
        ("Transport",       "The network protocol used between agent and MCP server. Two are defined: "
                            "SSE (Server-Sent Events) and Streamable HTTP."),
        ("JWT",             "JSON Web Token — a compact, URL-safe way to represent claims between two parties. "
                            "Signed with either a shared secret (HMAC) or a private key (RSA/EC)."),
        ("RS256",           "RSA Signature with SHA-256 — an asymmetric JWT signing algorithm. "
                            "The hub signs tokens with its private key; servers verify with the public key."),
        ("JWKS",            "JSON Web Key Set — a standard endpoint (/.well-known/jwks.json) that publishes "
                            "a server's public keys for JWT verification by other parties."),
        ("Audience (aud)",  "A JWT claim that specifies the intended recipient of the token. "
                            "A server rejects any token whose aud does not match its own identity."),
        ("Issuer (iss)",    "A JWT claim that identifies who issued the token. Recipients verify this "
                            "matches their expected issuer (the hub's identity string)."),
        ("RBAC",            "Role-Based Access Control — restricting operations based on the roles "
                            "assigned to the caller, rather than their individual identity."),
        ("PKCE",            "Proof Key for Code Exchange — an OAuth 2.0 extension that prevents "
                            "authorization code interception attacks in public clients."),
        ("PBKDF2",          "Password-Based Key Derivation Function 2 — a slow hashing algorithm "
                            "designed for password storage, using many iterations to resist brute-force."),
        ("SSE",             "Server-Sent Events — an HTTP transport where the server pushes events "
                            "over a persistent GET connection."),
        ("Streamable HTTP", "An MCP transport using a single stateful POST endpoint, "
                            "identified by a session ID header on all subsequent calls."),
        ("ReAct Agent",     "A reasoning-and-acting AI loop (LangGraph pattern): THINK → CALL TOOL → "
                            "OBSERVE result → repeat until final answer."),
        ("Semantic View",   "A pre-joined, business-readable database view that abstracts raw schema "
                            "details from tool implementations."),
    ]

    rich_table(doc,
        ["Term", "Definition"],
        terms,
        col_widths_cm=[4.5, 12.0])

    doc.add_page_break()

    # =========================================================================
    # 3. MCP PROTOCOL FUNDAMENTALS
    # =========================================================================
    heading1(doc, "3.  MCP Protocol Fundamentals")

    heading2(doc, "3.1  What is MCP?")
    body_para(doc,
        "The Model Context Protocol (MCP) is an open standard that defines a structured "
        "interface for AI agents to interact with backend services. Rather than hard-coding "
        "API integrations into an agent's code, MCP lets agents discover what capabilities "
        "a server offers at runtime and call them through a uniform protocol.")
    body_para(doc,
        "MCP follows a client-server architecture. The AI agent acts as the MCP client; "
        "backend services implement the MCP server interface. The protocol defines three "
        "types of capability that a server may expose:")

    rich_table(doc,
        ["Capability", "Analogy", "Initiated By", "Description"],
        [
            ("Tools",     "REST POST endpoint", "Agent (client)",  "Callable functions that perform actions or return computed results"),
            ("Resources", "REST GET endpoint",  "Agent (client)",  "URI-addressable read-only data assets"),
            ("Prompts",   "Template library",   "Agent (client)",  "Reusable prompt templates with named parameters"),
        ],
        col_widths_cm=[3.0, 3.5, 3.5, 7.0])

    # ---- 3.2 Tools
    heading2(doc, "3.2  Tools")
    body_para(doc,
        "Tools are the primary mechanism through which agents take actions. Each tool has "
        "a name, a human-readable description, and a typed JSON Schema defining its input "
        "parameters and expected output. The agent calls tools.call with a tool name and "
        "argument payload; the MCP server executes the logic and returns a result.")

    code_para(doc,
"-- Agent discovers tools from a server\n"
"tools/list  →  [\n"
"  {\n"
'    "name":        "customer_lookup",\n'
'    "description": "Retrieve a customer profile by ID or email address.",\n'
'    "inputSchema":  {\n'
'       "type": "object",\n'
'       "properties": {\n'
'         "customer_id": { "type": "string", "description": "Unique customer ID" }\n'
'       },\n'
'       "required": ["customer_id"]\n'
'    },\n'
'    "outputSchema": { "type": "object" }\n'
"  }\n"
"]\n\n"
"-- Agent calls a tool\n"
'tools/call  →  { "name": "customer_lookup", "arguments": { "customer_id": "C001" } }\n'
'            ←  { "content": [{ "type": "text", "text": "{ name: Alice, plan: Gold ... }" }] }')

    # ---- 3.3 Resources
    heading2(doc, "3.3  Resources")
    body_para(doc,
        "Resources expose read-only data assets identified by URIs. An agent can list "
        "available resources, read a specific resource by URI, or subscribe to resource "
        "updates. Resources are suited for data that doesn't change on every call — "
        "configuration documents, product catalogues, or cached reference data.")

    code_para(doc,
"-- List available resources\n"
"resources/list  →  [\n"
"  { \"uri\": \"docs://pricing-policy/current\",   \"name\": \"Pricing Policy\",   \"mimeType\": \"text/markdown\" },\n"
"  { \"uri\": \"data://product-catalogue/v3\",     \"name\": \"Product Catalogue\", \"mimeType\": \"application/json\" }\n"
"]\n\n"
"-- Read a resource by URI\n"
"resources/read  →  { \"uri\": \"docs://pricing-policy/current\" }\n"
"               ←   { \"contents\": [{ \"uri\": \"...\", \"text\": \"# Pricing Policy ...\" }] }")

    # ---- 3.4 Prompts
    heading2(doc, "3.4  Prompts")
    body_para(doc,
        "Prompts let MCP servers expose reusable prompt templates with typed parameters. "
        "The agent fetches a prompt template, fills in the arguments, and uses the "
        "rendered prompt directly in its reasoning loop. This allows centralised prompt "
        "management and versioning on the server side.")

    code_para(doc,
"-- List available prompts\n"
"prompts/list  →  [\n"
"  { \"name\": \"summarise-customer\",  \"description\": \"Produce a customer brief.\",\n"
"    \"arguments\": [{ \"name\": \"customer_id\", \"required\": true }] }\n"
"]\n\n"
"-- Get a rendered prompt\n"
"prompts/get   →  { \"name\": \"summarise-customer\", \"arguments\": { \"customer_id\": \"C001\" } }\n"
"              ←  { \"messages\": [{ \"role\": \"user\", \"content\": { \"type\": \"text\",\n"
"                    \"text\": \"Provide a brief for customer C001 including name, segment, and recent activity.\" }}] }")

    # ---- 3.5 Transports
    heading2(doc, "3.5  Transport Protocols")
    body_para(doc,
        "MCP defines two transport mechanisms for carrying JSON-RPC 2.0 messages between "
        "agent and server. Both carry the same protocol; only the network layer differs.")

    rich_table(doc,
        ["Feature", "SSE Transport", "Streamable HTTP Transport"],
        [
            ("Protocol",        "HTTP GET (persistent) + HTTP POST",     "Single HTTP POST endpoint"),
            ("Session state",   "Server-side per connection",            "Mcp-Session-Id header"),
            ("Typical use",     "Streaming, real-time servers",          "Request/response, stateful sessions"),
            ("Connection",      "Two channels open simultaneously",      "One POST per call"),
            ("Accept header",   "Not required",                          "application/json, text/event-stream"),
            ("Auth",            "Bearer token on every POST",            "Bearer token on every POST"),
        ],
        col_widths_cm=[4.0, 6.5, 6.5])

    callout(doc, "important", "Token on every call",
        "Regardless of transport, the Bearer JWT must be included on EVERY JSON-RPC request "
        "(initialize, tools/list, tools/call, resources/read) — not just the initial handshake. "
        "The MCP server validates the token independently on each request.")

    doc.add_page_break()

    # =========================================================================
    # 4. MCP HUB ARCHITECTURE
    # =========================================================================
    heading1(doc, "4.  MCP Hub Architecture")

    heading2(doc, "4.1  Hub Responsibilities")
    body_para(doc,
        "The MCP Hub is a single control-plane service that sits between agents and MCP servers. "
        "Its responsibilities span discovery, security, routing, and governance:")

    rich_table(doc,
        ["Responsibility", "Description"],
        [
            ("Server Registry",       "Maintains a database of all registered MCP servers with their endpoints, capabilities, and metadata"),
            ("Intelligent Routing",   "Uses an LLM-powered agent to select the best-matching MCP server for each user query"),
            ("JWT Issuance",          "Mints short-lived, audience-scoped RS256 JWTs for agent-to-MCP-server authentication"),
            ("JWKS Publication",      "Publishes its RSA public key at /.well-known/jwks.json for server-side verification"),
            ("Admin UI",              "Browser-based interface for managing servers, viewing logs, and probing server health"),
            ("Observability",         "Structured event logging for every auth check, routing decision, and HTTP request"),
            ("Policy Enforcement",    "Hub-level RBAC ensures only authorised callers can discover servers or perform admin actions"),
        ],
        col_widths_cm=[4.5, 12.5])

    heading2(doc, "4.2  Component Overview")
    code_para(doc,
"  ┌──────────────────────────────────────────────────────────────────────────────┐\n"
"  │                            BROWSER / CLIENT                                  │\n"
"  │      Chat UI (SPA)                           Admin UI (SPA)                 │\n"
"  │   User conversation interface            Server management console          │\n"
"  └───────────────┬──────────────────────────────────────┬───────────────────────┘\n"
"                  │ SSE + POST /messages                  │ REST (hub JWT)\n"
"                  ▼                                       ▼\n"
"  ┌───────────────────────────┐      ┌──────────────────────────────────────────────┐\n"
"  │   Chat Server :8080       │      │   MCP Hub Server :8090                        │\n"
"  │                           │      │                                              │\n"
"  │  • User login (PBKDF2)    │      │  GET  /.well-known/jwks.json  (public)       │\n"
"  │  • Rate-limited auth      │      │  GET  /health                 (public)       │\n"
"  │  • SSE event streaming    │      │  GET  /servers                (auth)         │\n"
"  │  • Background task mgmt   │      │  POST /discover               (auth)         │\n"
"  │  • Conversation history   │      │  GET|POST /api/hub/*          (admin)        │\n"
"  └───────────────┬───────────┘      └───────────────────┬──────────────────────────┘\n"
"                  │                                       │\n"
"                  │  run_agent(query)                     │  SQL (registry + events)\n"
"                  ▼                                       ▼\n"
"  ┌───────────────────────────┐      ┌──────────────────────────────────────────────┐\n"
"  │   Agent Orchestrator      │      │   Database (MySQL / PostgreSQL)               │\n"
"  │                           │      │                                              │\n"
"  │  • POST /discover         │      │  mcp_servers   — server registry             │\n"
"  │  • mcp_session()          │      │  hub_events    — structured event log        │\n"
"  │  • ReAct reasoning loop   │      │  conversations — chat history                │\n"
"  │  • Tool loading + calling │      │  users         — auth subjects               │\n"
"  └───────────────┬───────────┘      └──────────────────────────────────────────────┘\n"
"                  │  MCP Protocol\n"
"                  │  + RS256 JWT Bearer (aud = target server ID)\n"
"                  ▼\n"
"  ┌──────────────────────────────────────────────────────────────────────────────┐\n"
"  │                        MCP Servers                                           │\n"
"  │                                                                              │\n"
"  │   Server A                   Server B                   Server N            │\n"
"  │   e.g. :9100                 e.g. :9200                 e.g. :9300          │\n"
"  │   JWTVerifier                JWTVerifier                JWTVerifier         │\n"
"  │   BearerClaimsMiddleware     BearerClaimsMiddleware     BearerClaimsMiddleware│\n"
"  │   Tools / Resources / Prompts                                                │\n"
"  │   → MySQL (own credentials)  → External APIs (own keys)                     │\n"
"  └──────────────────────────────────────────────────────────────────────────────┘")

    heading2(doc, "4.3  Request Lifecycle")
    body_para(doc,
        "Every agent query follows the same six-step lifecycle from the browser to the "
        "MCP tool result and back:")

    steps = [
        ("User sends query",          "Browser POSTs the query to the Chat Server over an active SSE session."),
        ("Hub discovery",             "Agent calls POST /discover on the Hub with the user's hub JWT. The hub routes the query."),
        ("Server selection + JWT mint","Hub runs LLM routing agent, selects the best MCP server, mints a per-server JWT."),
        ("MCP session open",          "Agent opens an MCP session to the selected server, attaching the per-server JWT."),
        ("Tool execution",            "Agent's ReAct loop discovers tools, selects the right one, calls it, receives result."),
        ("Answer streamed",           "Agent synthesises the final answer and streams it back to the browser via SSE."),
    ]
    for i, (title, desc) in enumerate(steps, 1):
        numbered_item(doc, i, f"{desc}", bold_prefix=f"{title}:  ")

    doc.add_page_break()

    # =========================================================================
    # 5. SERVER REGISTRATION & GOVERNANCE
    # =========================================================================
    heading1(doc, "5.  Server Registration & Governance")

    heading2(doc, "5.1  Server Registry Schema")
    body_para(doc,
        "Every MCP server that participates in the hub ecosystem must be registered in the "
        "server registry. The registry is the source of truth for server metadata, "
        "routing, and access control decisions.")

    rich_table(doc,
        ["Field", "Type", "Required", "Purpose"],
        [
            ("id",              "VARCHAR(100)", "Yes",  "Unique server identifier — used as JWT audience (aud)"),
            ("name",            "VARCHAR(255)", "Yes",  "Human-readable display name for Admin UI"),
            ("endpoint",        "VARCHAR(500)", "Yes",  "Full URL of the MCP server (e.g. http://host:9100/mcp)"),
            ("transport",       "VARCHAR(50)",  "Yes",  "'sse' or 'streamable-http'"),
            ("capability",      "TEXT",         "Yes",  "Short description of what the server does (used in LLM routing)"),
            ("skills",          "JSON",         "No",   "List of skill tags for routing (e.g. [\"customer\", \"crm\"])"),
            ("description",     "TEXT",         "No",   "Detailed description for routing context"),
            ("examples",        "JSON",         "No",   "Sample queries that this server can answer (routing hints)"),
            ("api_key",         "VARCHAR(1000)","No",   "Per-server static Bearer token (overrides env var if set)"),
            ("api_key_expires", "TIMESTAMP",    "No",   "Expiry of the api_key (NULL = never expires)"),
            ("is_active",       "TINYINT(1)",   "Yes",  "0 = disabled (excluded from routing); 1 = active"),
            ("created_at",      "TIMESTAMP",    "Auto", "Row creation time"),
            ("updated_at",      "TIMESTAMP",    "Auto", "Last modification time (auto-updated)"),
        ],
        col_widths_cm=[3.5, 3.0, 2.2, 8.3])

    heading2(doc, "5.2  Registration Process")
    body_para(doc,
        "New MCP servers can be registered through three mechanisms, in order of preference:")

    numbered_item(doc, 1,
        "Declarative JSON file (mcp-hub.json) seeded into the database via a one-time script. "
        "Best for bootstrapping a known set of servers.",
        bold_prefix="JSON + Seed Script:  ")
    numbered_item(doc, 2,
        "Admin UI — the hub's built-in browser interface allows adding, editing, and "
        "deactivating servers without database access.",
        bold_prefix="Admin UI (POST /api/hub/servers):  ")
    numbered_item(doc, 3,
        "Direct REST API — POST /api/hub/servers with a JSON body. Suitable for "
        "automated CI/CD pipelines that register new servers on deployment.",
        bold_prefix="REST API:  ")

    callout(doc, "important", "Idempotent seeding",
        "The seed script uses INSERT ... ON DUPLICATE KEY UPDATE, making it safe to re-run "
        "after adding new servers. Note: re-seeding sets is_active=1, which silently re-activates "
        "any server that was manually disabled via the Admin UI. If a server should remain "
        "disabled, exclude it from the seed file or set is_active=0 after seeding.")

    heading2(doc, "5.3  Server Lifecycle Management")
    body_para(doc,
        "Registered servers move through a defined lifecycle managed by the hub:")

    code_para(doc,
"    ┌─────────────┐       Register        ┌──────────────┐\n"
"    │  Unregistered│  ─────────────────►  │   Active      │\n"
"    └─────────────┘                        │  is_active=1  │\n"
"                                           └──────┬────────┘\n"
"                                                  │\n"
"                         Disable (Admin UI)       │    Re-enable\n"
"                                                  ▼\n"
"                                           ┌──────────────┐\n"
"                                           │  Disabled     │\n"
"                                           │  is_active=0  │\n"
"                                           └──────┬────────┘\n"
"                                                  │\n"
"                         DELETE /api/hub/servers  │\n"
"                                                  ▼\n"
"                                           ┌──────────────┐\n"
"                                           │   Deleted     │\n"
"                                           └──────────────┘")

    callout(doc, "note", "Registry Cache",
        "The hub caches the server registry in-process for 60 seconds to reduce database load. "
        "Admin changes made via the Admin UI appear in routing within 60 seconds. "
        "The POST /api/hub/refresh endpoint immediately invalidates the cache for urgent changes.")

    rich_table(doc,
        ["Admin Action", "Endpoint", "Effect"],
        [
            ("Add server",    "POST /api/hub/servers",         "Inserts new registry row; available for routing immediately (after cache refresh)"),
            ("Edit server",   "PUT /api/hub/servers/{id}",     "Updates fields; endpoint change applies within 60s"),
            ("Disable server","PATCH /api/hub/servers/{id}",   "Sets is_active=0; server excluded from all routing"),
            ("Probe server",  "POST /api/hub/servers/{id}/probe", "Hub mints a test JWT and calls tools/list — verifies auth is working"),
            ("Refresh cache", "POST /api/hub/refresh",         "Invalidates the 60s registry cache immediately"),
            ("Delete server", "DELETE /api/hub/servers/{id}",  "Removes row from registry; irreversible"),
        ],
        col_widths_cm=[3.5, 5.5, 8.0])

    doc.add_page_break()

    # =========================================================================
    # 6. AUTHENTICATION — END TO END
    # =========================================================================
    heading1(doc, "6.  Authentication Architecture — End to End")

    body_para(doc,
        "The MCP Hub implements a layered authentication model where each boundary between "
        "system components uses a different credential type. No credential ever crosses "
        "a layer boundary — each layer is responsible only for the credential it owns.")

    callout(doc, "important", "Core Security Principle — Credential Isolation",
        "Agent JWT → consumed at the MCP boundary and discarded.\n"
        "MCP server JWT → never forwarded to MySQL, external APIs, or other servers.\n"
        "MySQL credentials → stay inside the MCP server process only.\n"
        "External API keys → stay inside the MCP server process only.\n\n"
        "This isolation limits the blast radius of any single credential compromise.")

    heading2(doc, "6.0  Authentication Phases Overview")

    rich_table(doc,
        ["Phase", "Boundary", "Credential Type", "Issued By", "Expiry"],
        [
            ("1", "User → Chat Server",      "Username + PBKDF2 password",      "DBA / env config",  "N/A (stateless verify)"),
            ("2", "Chat Server → Browser",   "Hub JWT (RS256, aud=hub)",         "Hub Server",        "8 hours"),
            ("3", "Agent → Hub API",         "Hub JWT (RS256, aud=hub)",         "Hub Server",        "8 hours"),
            ("4", "Hub → MCP Server",        "Per-server JWT (RS256, aud=srv)",  "Hub Server",        "1 hour"),
            ("5", "Agent → MCP Server",      "Per-server JWT (RS256, aud=srv)",  "Hub Server",        "1 hour"),
            ("Tool", "Tool function → DB",   "MYSQL_USER + MYSQL_PASSWORD",      ".env file",         "N/A (connection pool)"),
        ],
        col_widths_cm=[1.5, 4.5, 4.5, 3.5, 2.5])

    heading2(doc, "RSA Key Infrastructure")
    body_para(doc,
        "All JWTs in the system are signed using RS256 — RSA with SHA-256. The hub maintains "
        "a 2048-bit RSA key pair. The private key signs all tokens; the public key is published "
        "for verification. MCP servers never receive the private key.")

    code_para(doc,
"Hub generates RSA-2048 key pair on first startup:\n"
"  private.pem  ─── signs all JWTs ──► stays inside hub process only\n"
"  public.pem   ─── published via ────► GET /.well-known/jwks.json\n"
"\n"
"MCP servers on startup:\n"
"  PyJWKClient(hub_url + '/.well-known/jwks.json')\n"
"  → fetches and caches public key\n"
"  → uses it to verify ALL incoming Bearer tokens\n"
"\n"
"Why RSA over HMAC (HS256)?\n"
"  HMAC: all servers need the shared secret  → one compromise leaks the signing key\n"
"  RSA:  servers only need the public key    → private key never leaves the hub")

    spacer(doc, 12)

    # ---- PHASE 1
    phase_box(doc, "1", "User Login — Chat UI to Chat Server",
        "The user authenticates with a username and password. The chat server verifies the "
        "credentials and issues a hub JWT that the client uses for all subsequent API calls.")

    heading3(doc, "How It Works")
    body_para(doc,
        "The user submits their credentials via the chat UI login form. The chat server "
        "looks up the stored password hash in its user table and verifies the submission "
        "using PBKDF2-SHA256 with 200,000 iterations. On success, a signed hub JWT is "
        "returned as an HttpOnly cookie.")

    code_para(doc,
"Step 1 — Browser submits credentials\n"
"  POST /login\n"
"  Body: { \"username\": \"alice\", \"password\": \"s3cret!\" }\n"
"\n"
"Step 2 — Server retrieves stored hash\n"
"  Stored format:  pbkdf2:sha256:200000:<16-byte-salt>:<64-byte-hex-digest>\n"
"  e.g.            pbkdf2:sha256:200000:a1b2c3d4e5f6g7h8:9a8f3d...(hex)\n"
"\n"
"Step 3 — PBKDF2 verification\n"
"  derived = PBKDF2(password='s3cret!', salt=<stored_salt>, iterations=200000,\n"
"                   hash='SHA256', dklen=32)\n"
"  if derived == stored_hash:  → PASS\n"
"  else:                       → 401 Unauthorized  (increment rate-limit counter)\n"
"\n"
"Step 4 — Hub JWT minted\n"
"  payload = {\n"
"    \"sub\":   \"alice\",\n"
"    \"roles\": [\"agent\"],\n"
"    \"iss\":   \"mcp-hub\",\n"
"    \"aud\":   \"mcp-hub\",\n"
"    \"exp\":   now + 28800   # 8 hours\n"
"  }\n"
"  token = RS256_sign(payload, private_key)\n"
"\n"
"Step 5 — Token returned\n"
"  Set-Cookie: session=<token>; HttpOnly; SameSite=Strict\n"
"  or:  Authorization: Bearer <token>  (API mode)")

    rich_table(doc,
        ["Control", "Implementation", "Protects Against"],
        [
            ("PBKDF2-SHA256",         "200,000 iterations; unique salt per user",         "Offline dictionary + rainbow table attacks"),
            ("Rate limiting",         "10 failed attempts per username / 15 min window",  "Online brute-force attacks"),
            ("HttpOnly cookie",       "Browser JS cannot read token",                      "XSS token theft"),
            ("SameSite=Strict",       "Cookie not sent on cross-site requests",            "CSRF attacks"),
        ],
        col_widths_cm=[4.0, 6.0, 7.0])

    callout(doc, "warning", "Rate Limit Scope",
        "The rate limiter tracks failed attempts per username, not per IP address. "
        "This means a distributed brute-force attack from many IPs would not be blocked. "
        "For production: add an IP-based rate limiter or integrate with a WAF.")

    spacer(doc, 12)

    # ---- PHASE 2
    phase_box(doc, "2", "Hub API Authentication — Agent to Hub Server",
        "Every API call to the hub (except public health and JWKS endpoints) requires "
        "a valid hub JWT in the Authorization header. The hub validates it locally using "
        "its own public key — no database round-trip required.")

    heading3(doc, "How It Works")
    body_para(doc,
        "The agent includes the hub JWT (received at login) in the Authorization: Bearer "
        "header on every call to the hub API. The hub's _classify_token() function decodes "
        "and validates the token, then checks the caller's roles against the endpoint's "
        "required role before allowing access.")

    code_para(doc,
"Step 1 — Agent calls Hub API\n"
"  POST /discover\n"
"  Authorization: Bearer eyJhbGciOiJSUzI1NiJ9...<hub_jwt>\n"
"\n"
"Step 2 — Hub validates the token\n"
"  decoded = jwt.decode(\n"
"    token,\n"
"    public_key,           # loaded from public.pem at startup\n"
"    algorithms=[\"RS256\"],\n"
"    issuer=\"mcp-hub\",\n"
"    audience=\"mcp-hub\"\n"
"  )\n"
"  # Checks: signature ✓  iss ✓  aud ✓  exp ✓\n"
"\n"
"Step 3 — RBAC check\n"
"  required_role = endpoint_config[\"/discover\"]   # = \"agent\" or \"admin\"\n"
"  caller_roles  = decoded[\"roles\"]               # = [\"agent\"]\n"
"  if required_role in caller_roles:  → PASS → continue\n"
"  else:                              → 403 Forbidden\n"
"\n"
"Step 4 — Request proceeds\n"
"  Hub uses decoded[\"sub\"] and decoded[\"roles\"] to mint the per-server JWT later.")

    rich_table(doc,
        ["Hub Role", "Permitted Endpoints", "Typical Holder"],
        [
            ("admin",    "All endpoints: CRUD /api/hub/*, /discover, /servers, /health",  "Administrator via login"),
            ("agent",    "POST /discover, GET /servers, GET /health",                      "Agent/orchestrator process"),
            ("readonly", "GET /servers, GET /health only",                                 "Monitoring / audit consumers"),
        ],
        col_widths_cm=[3.0, 8.5, 5.5])

    spacer(doc, 12)

    # ---- PHASE 3
    phase_box(doc, "3", "Server Discovery & Per-Server JWT Minting — POST /discover",
        "The hub's most important operation: accept the agent's query, select the right "
        "MCP server using LLM routing, and mint a cryptographically scoped JWT for "
        "that specific server.")

    heading3(doc, "LLM Routing Agent")
    body_para(doc,
        "The hub runs a LangGraph ReAct agent that receives the user's query and the full "
        "server registry (capability, skills, description, examples for each server). "
        "The routing agent reasons about which server best matches the query and calls "
        "a pick_server() tool to record its decision.")

    code_para(doc,
"-- Hub routing agent reasoning example\n\n"
"User query:  \"Get the 360 profile for customer C001\"\n\n"
"Server registry injected into prompt:\n"
"  server_a: capability='Customer intelligence and CRM data'\n"
"            skills=['customer','crm','360']\n"
"            examples=['Get customer profile', 'List recent orders']\n"
"  server_b: capability='Product pricing and margin analysis'\n"
"            skills=['pricing','margin','deals']\n"
"\n"
"LLM reasoning:\n"
"  THINK: Query mentions 'customer profile' → matches server_a skills ['customer','360']\n"
"  THINK: server_b is pricing-related → not a match\n"
"  CALL:  pick_server(server_id='server_a', reason='Customer 360 query matches CRM server')\n"
"  OBSERVE: selection confirmed\n"
"\n"
"Result: server_a selected → proceed to JWT minting")

    heading3(doc, "Per-Server JWT Minting")
    body_para(doc,
        "For each selected server, the hub mints a brand-new RS256 JWT with claims "
        "precisely scoped to that server and the current user's identity:")

    code_para(doc,
"For each matched server:\n"
"  payload = {\n"
"    \"iss\":   \"mcp-hub\",           # Issuer = hub identity string\n"
"    \"aud\":   \"server_a\",          # Audience = THIS server's ID only\n"
"    \"sub\":   \"alice\",             # Subject = user who initiated the query\n"
"    \"roles\": [\"agent\"],           # Forwarded from the hub JWT\n"
"    \"iat\":   now,                  # Issued-at timestamp\n"
"    \"exp\":   now + 3600            # Expires in 1 hour (shorter than 8h user session)\n"
"  }\n"
"  server_token = jwt.encode(payload, private_key, algorithm=\"RS256\")\n"
"\n"
"Response:\n"
"  {\n"
"    \"servers\": [\n"
"      {\n"
"        \"id\":           \"server_a\",\n"
"        \"endpoint\":     \"http://host:9100/mcp\",\n"
"        \"transport\":    \"streamable-http\",\n"
"        \"server_token\": \"eyJhbGci...\" ← per-server JWT\n"
"      }\n"
"    ],\n"
"    \"method\": \"agent\",\n"
"    \"intent\": \"Customer 360 query matches CRM server\"\n"
"  }")

    callout(doc, "important", "Why Per-Server Audience Scoping?",
        "Each JWT carries aud = server_id. When server_b receives a token whose aud = 'server_a', "
        "it rejects it with 401. This means:\n"
        "  • A token intercepted from one server cannot be replayed against any other server\n"
        "  • Even if an attacker steals a valid token, its damage is limited to one server\n"
        "  • The blast radius of any single token compromise is bounded by: 1 server × 1 hour")

    spacer(doc, 12)

    # ---- PHASE 4
    phase_box(doc, "4", "MCP Server JWT Validation — Agent to MCP Server",
        "The agent opens an MCP session to the selected server, attaching the per-server JWT. "
        "The MCP server validates the token on every request before allowing any operation.")

    heading3(doc, "Token Priority Chain")
    body_para(doc,
        "The agent resolves the Bearer token for each MCP server connection using this "
        "priority order (first match wins):")

    code_para(doc,
"1st priority:  server[\"server_token\"]     ← per-server JWT minted by /discover  (PREFERRED)\n"
"2nd priority:  server[\"api_key\"]          ← static key stored in the registry database\n"
"3rd priority:  MCP_API_KEY env var         ← global fallback key (dev environments)\n\n"
"In production: 1st priority should always be present (hub minted it).\n"
"The 2nd and 3rd options are fallbacks for servers not using hub JWT auth.")

    heading3(doc, "MCP Session & JWT Validation Flow")
    code_para(doc,
"Agent                                          MCP Server (server_a :9100)\n"
"─────                                          ──────────────────────────\n"
"                                               [Startup: fetch JWKS]\n"
"                                               PyJWKClient(hub_url+'/.well-known/jwks.json')\n"
"                                               → caches hub's RSA public key\n"
"\n"
"mcp_session(server_a):\n"
"  token = server_a['server_token']\n"
"  headers = { Authorization: Bearer <token> }\n"
"  streamablehttp_client(endpoint, headers)\n"
"\n"
"  POST /mcp (initialize) ──────────────────────► [FastMCP JWTVerifier — Layer 1]\n"
"  Accept: application/json,                       signing_key = JWKS.get_signing_key(token)\n"
"          text/event-stream                        jwt.decode(token,\n"
"                                                    signing_key,\n"
"                                                    algorithms=['RS256'],\n"
"                                                    issuer='mcp-hub',\n"
"                                                    audience='server_a')\n"
"                                                  ✓ signature valid\n"
"                                                  ✓ iss = 'mcp-hub'\n"
"                                                  ✓ aud = 'server_a'  (exact match)\n"
"                                                  ✓ exp not passed\n"
"                                                  ✗ any failure → 401 Unauthorized\n"
"\n"
"                                                  [BearerClaimsMiddleware — Layer 2]\n"
"                                                  jwt.decode(token,\n"
"                                                    options={'verify_signature': False})\n"
"                                                  # safe: already verified by Layer 1\n"
"                                                  _request_claims.set({\n"
"                                                    'sub':   'alice',\n"
"                                                    'roles': ['agent']\n"
"                                                  })\n"
"\n"
"  Mcp-Session-Id: <uuid> ◄──────────────────────  initialize response\n"
"\n"
"  POST /mcp (tools/list) ──────────────────────► same validation repeated\n"
"  Mcp-Session-Id: <uuid>\n"
"  Authorization: Bearer <token>\n"
"\n"
"  POST /mcp (tools/call) ──────────────────────► same validation repeated\n"
"  Authorization: Bearer <token>")

    callout(doc, "note", "Two-Layer Middleware Design",
        "Layer 1 (JWTVerifier): Full cryptographic validation — signature, iss, aud, exp. Returns 401 if any check fails.\n"
        "Layer 2 (BearerClaimsMiddleware): Decodes the already-validated token (no re-verify) into a "
        "ContextVar so tool functions can read the caller's identity without touching the JWT.\n\n"
        "This separation avoids redundant JWKS fetches on every request while keeping RBAC clean.")

    spacer(doc, 12)

    # ---- PHASE 5
    phase_box(doc, "5", "Per-Tool Role-Based Access Control — Inside MCP Server Tools",
        "After JWT validation, each individual tool function enforces its own role check "
        "before executing any business logic. This is the innermost security layer.")

    heading3(doc, "How require_role() Works")
    body_para(doc,
        "Every MCP tool that should be role-protected calls require_role() as its first "
        "statement. This function reads the claims stored in the _request_claims ContextVar "
        "by the middleware and raises PermissionError if the caller lacks the required role.")

    code_para(doc,
"# Inside an MCP server — tool definition\n"
"@mcp.tool()\n"
"def customer_360(customer_id: str) -> dict:\n"
"    \"\"\"\n"
"    Retrieve a full 360-degree customer profile.\n"
"    Requires: 'agent' or 'admin' role.\n"
"    \"\"\"\n"
"\n"
"    # Step 1: RBAC check (reads _request_claims ContextVar set by middleware)\n"
"    require_role('agent', 'admin')\n"
"    # → claims = {'sub': 'alice', 'roles': ['agent']}\n"
"    # → 'admin' bypasses all checks\n"
"    # → 'agent' in required_roles (['agent','admin']) → PASS\n"
"    # → no matching role → raise PermissionError → MCP server returns 403\n"
"\n"
"    # Step 2: Audit log (before any data access)\n"
"    audit_log(\n"
"        tool    = 'customer_360',\n"
"        args    = {'customer_id': customer_id},  # keys logged, values omitted (PII)\n"
"        service = 'mysql'\n"
"    )\n"
"    # → emits: { type:'tool_audit', tool:'customer_360', sub:'alice',\n"
"    #            roles:['agent'], args_keys:['customer_id'], service:'mysql' }\n"
"\n"
"    # Step 3: Business logic — uses MCP server's OWN MySQL credentials\n"
"    return db.query(\n"
"        'SELECT * FROM customer_360_view WHERE id = %s',\n"
"        (customer_id,)  # parameterised query — prevents SQL injection\n"
"    )\n\n"
"\n"
"def require_role(*roles: str) -> None:\n"
"    claims = _request_claims.get()   # ContextVar from middleware\n"
"    if not claims:                   # no claims = open dev mode only\n"
"        return\n"
"    user_roles = claims.get('roles', [])\n"
"    if 'admin' in user_roles:        # admin bypasses every check\n"
"        return\n"
"    if not any(r in user_roles for r in roles):\n"
"        raise PermissionError(f'Role required: {list(roles)}, caller has {user_roles}')  # → 403")

    rich_table(doc,
        ["Caller Role", "require_role('agent', 'admin')", "Outcome"],
        [
            ("admin",           "admin in user_roles → bypass all checks",   "✓  Tool executes"),
            ("agent",           "'agent' in ['agent','admin'] → PASS",       "✓  Tool executes"),
            ("readonly",        "'readonly' not in ['agent','admin'] → FAIL","✗  PermissionError → 403"),
            ("(empty / dev)",   "claims = {} → no-op (dev mode only)",       "✓  Tool executes — ONLY in dev"),
        ],
        col_widths_cm=[3.5, 7.0, 5.5])

    callout(doc, "warning", "Dev Mode Risk",
        "When MCP_AUTH_ENABLED=false OR when MCP_SERVER_ID is not set and no token is provided, "
        "the server operates in open dev mode — any caller gets implicit admin access. "
        "ALWAYS set MCP_SERVER_ID and MCP_AUTH_ENABLED=true in non-development environments.")

    spacer(doc, 8)

    # ---- 6.6 Full E2E Flow
    heading2(doc, "6.6  Complete End-to-End Authentication Flow")
    body_para(doc,
        "The following diagram traces a single user query from browser to tool result, "
        "showing every authentication step across all five phases:")

    code_para(doc,
"USER          CHAT UI       CHAT SERVER    HUB SERVER      MCP SERVER     DATABASE\n"
"────          ───────       ───────────    ──────────       ──────────     ────────\n"
"\n"
"━━━ PHASE 1: Login ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
"  POST /login ─────────────►\n"
"  {user, pass}               PBKDF2 verify\n"
"                             mint Hub JWT (8h)\n"
"  ◄──────── Set-Cookie ─────\n"
"             session=<hub_jwt>\n"
"\n"
"━━━ PHASE 2: User sends query ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
"  POST /messages ──────────►\n"
"  {query, session}           run_agent(query)\n"
"\n"
"━━━ PHASE 3: Hub routing + JWT minting ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
"                             POST /discover ──────────────────────────────────►\n"
"                             Bearer: <hub_jwt>  validate hub JWT\n"
"                                                LLM routes to server_a\n"
"                                                mint per-server JWT:\n"
"                                                  aud=server_a, exp=1h\n"
"                                                  sub=alice, roles=[agent]\n"
"                             ◄─── [{server_a config + server_token}] ──────────\n"
"\n"
"━━━ PHASE 4: MCP session open + JWT validation ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
"                             mcp_session(server_a) ──────────────────────────►\n"
"                             Bearer: <server_token>  JWTVerifier:\n"
"                                                       RS256 verify ✓\n"
"                                                       aud=server_a ✓\n"
"                                                       exp valid    ✓\n"
"                                                     BearerClaimsMiddleware:\n"
"                                                       _request_claims ←\n"
"                                                         {sub:alice, roles:[agent]}\n"
"\n"
"━━━ PHASE 5: Tool RBAC + execution ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
"                             tools/call ─────────────────────────────────────►\n"
"                             customer_360(C001)         require_role('agent') ✓\n"
"                                                        audit_log() recorded\n"
"                                                        SQL query ─────────────►\n"
"                                                        ◄──────── result ────────\n"
"                             ◄──── tool result ──────────\n"
"  ◄── SSE stream ───────────\n"
"  Final answer rendered")

    heading2(doc, "6.7  Token Lifecycle & Expiry Strategy")

    rich_table(doc,
        ["Token Type", "Expiry", "Rationale", "On Expiry"],
        [
            ("Hub JWT",          "8 hours",  "Matches a typical working day session; reduces re-login friction",     "User must log in again"),
            ("Per-server JWT",   "1 hour",   "Short window limits damage from token interception; re-minted on next /discover call", "Next /discover mints a fresh token"),
            ("JWKS cache",       "Varies",   "MCP servers cache the public key; update requires server restart",     "Server fetches fresh JWKS"),
        ],
        col_widths_cm=[3.5, 2.5, 8.5, 3.0])

    doc.add_page_break()

    # =========================================================================
    # 7. AUTHORIZATION FRAMEWORK
    # =========================================================================
    heading1(doc, "7.  Authorization Framework")

    body_para(doc,
        "Authorization in the MCP Hub ecosystem operates at three distinct levels, "
        "each independent and additive — passing one level does not bypass the others.")

    rich_table(doc,
        ["Level", "Where Enforced", "Mechanism", "Granularity"],
        [
            ("Hub RBAC",       "Hub server (hub_server.py)",     "Role check on hub JWT roles claim",          "Per endpoint (e.g. /discover, /api/hub/*)"),
            ("Server JWT",     "MCP server (JWTVerifier)",       "aud, iss, exp validation",                   "Per server (one wrong server = 401)"),
            ("Tool RBAC",      "MCP server tool function",       "require_role() reads ContextVar claims",     "Per tool function"),
        ],
        col_widths_cm=[3.0, 4.5, 5.0, 5.0])

    heading2(doc, "Role Design Guidelines")
    bullet_item(doc, "Define roles at the business capability level, not the technical level.",
                bold_prefix="Principle: ")
    bullet_item(doc, "Use narrow roles for sensitive tools (e.g. 'pricing-admin' rather than 'admin').",
                bold_prefix="Principle: ")
    bullet_item(doc, "admin role should bypass all tool-level checks — reserve it for operators only.",
                bold_prefix="Principle: ")
    bullet_item(doc, "Forward only the roles the user actually holds — never elevate roles in the per-server JWT.",
                bold_prefix="Principle: ")
    bullet_item(doc, "Audit every require_role() decision — pass or fail — for compliance traceability.",
                bold_prefix="Principle: ")

    heading2(doc, "Scope-Based Extension (Future)")
    body_para(doc,
        "For finer-grained control beyond roles, the JWT payload can carry a scopes claim "
        "(analogous to OAuth 2.0 scopes). Each tool can declare its required scopes, and "
        "require_scope() can enforce them independently of roles. This allows, for example, "
        "'read:customer' vs 'write:customer' distinctions within the same role.")

    code_para(doc,
"# Extended JWT payload with scopes\n"
"{\n"
"  \"sub\":    \"alice\",\n"
"  \"roles\":  [\"agent\"],\n"
"  \"scopes\": [\"read:customer\", \"read:pricing\"],  # ← scope extension\n"
"  \"aud\":    \"server_a\",\n"
"  \"exp\":    now + 3600\n"
"}\n\n"
"# Tool enforcement\n"
"def customer_360(customer_id: str):\n"
"    require_role('agent')\n"
"    require_scope('read:customer')  # fine-grained data-level control\n"
"    ...")

    doc.add_page_break()

    # =========================================================================
    # 8. TOOLS — DESIGN, STANDARDS & GOVERNANCE
    # =========================================================================
    heading1(doc, "8.  Tools — Design, Standards & Governance")

    heading2(doc, "8.1  Tool Design Principles")
    numbered_item(doc, 1,
        "Use a single noun-verb name (e.g. customer_lookup, order_create). "
        "Avoid generic names like 'query' or 'execute'.",
        bold_prefix="Named clearly: ")
    numbered_item(doc, 2,
        "Write description as the first sentence a user would ask: "
        "'Retrieve a customer's full 360 profile including orders and segment.'",
        bold_prefix="Self-documenting description: ")
    numbered_item(doc, 3,
        "Every input parameter must be in inputSchema with a type and description. "
        "Mark required fields explicitly.",
        bold_prefix="Fully typed schema: ")
    numbered_item(doc, 4,
        "Call require_role() as the very first statement — before any I/O.",
        bold_prefix="Secure by default: ")
    numbered_item(doc, 5,
        "Call audit_log() immediately after require_role() — before data access.",
        bold_prefix="Audit before access: ")
    numbered_item(doc, 6,
        "Use parameterised queries / ORMs — never string-format SQL with user input.",
        bold_prefix="No injection risk: ")
    numbered_item(doc, 7,
        "Return a consistent result schema. Use typed Pydantic models or TypedDicts.",
        bold_prefix="Consistent output: ")
    numbered_item(doc, 8,
        "One tool = one responsibility. Avoid mega-tools that branch on a 'mode' parameter.",
        bold_prefix="Single responsibility: ")

    heading2(doc, "8.2  Tool Schema Standard")
    code_para(doc,
"# Standard tool definition template\n"
"@mcp.tool()\n"
"def order_summary(customer_id: str, limit: int = 10) -> list[dict]:\n"
"    \"\"\"\n"
"    Return the most recent orders for a customer.\n"
"\n"
"    Args:\n"
"        customer_id: Unique customer identifier (e.g. C001).\n"
"        limit:       Maximum number of orders to return (default 10, max 100).\n"
"\n"
"    Returns:\n"
"        List of order dicts: {order_id, date, status, amount_usd}\n"
"    \"\"\"\n"
"    require_role('agent', 'admin')\n"
"    audit_log('order_summary', args={'customer_id': customer_id, 'limit': limit})\n"
"    limit = min(limit, 100)               # enforce server-side cap\n"
"    return db.query_all(\n"
"        'SELECT * FROM order_summary_view WHERE customer_id=%s ORDER BY date DESC LIMIT %s',\n"
"        (customer_id, limit)\n"
"    )")

    heading2(doc, "8.3  Tool Governance")
    rich_table(doc,
        ["Governance Control", "How Implemented", "Why"],
        [
            ("Tool inventory",     "tools/list discovered live from server",    "Always reflects current server version; no stale registry"),
            ("Input validation",   "inputSchema enforced by FastMCP",           "Rejects malformed calls before tool code runs"),
            ("Role requirement",   "require_role() as first statement",         "No business logic executes without auth check"),
            ("Audit trail",        "audit_log() before every data access",      "Immutable record of who accessed what"),
            ("Output schema",      "outputSchema declared in tool definition",  "Agents know what to expect; reduces hallucination"),
            ("Error handling",     "Raise typed exceptions (ValueError, etc.)", "MCP serialises exceptions cleanly as error responses"),
            ("Deprecation",        "Keep old tool name, add new tool, remove old in next major version", "Avoids breaking agents that hardcode tool names"),
        ],
        col_widths_cm=[4.5, 6.0, 6.5])

    doc.add_page_break()

    # =========================================================================
    # 9. RESOURCES & PROMPTS
    # =========================================================================
    heading1(doc, "9.  Resources & Prompts")

    heading2(doc, "9.1  Resource Design")
    body_para(doc,
        "Resources are best suited for read-only reference data that changes infrequently "
        "and is not the result of a computation. Good candidates: configuration documents, "
        "product catalogues, policy documents, static lookup tables.")

    rich_table(doc,
        ["Resource Pattern", "URI Scheme", "Example URI"],
        [
            ("Document / policy",   "docs://",  "docs://pricing-policy/v3"),
            ("Database record",     "data://",  "data://product-catalogue/all"),
            ("Live feed / stream",  "feed://",  "feed://market-rates/usd-gbp"),
            ("Configuration",       "config://","config://feature-flags/current"),
        ],
        col_widths_cm=[4.5, 3.0, 9.5])

    callout(doc, "note", "Resources vs Tools",
        "If retrieving data requires arguments (e.g. a customer ID), use a Tool, not a Resource. "
        "Resources are addressable by URI alone — they do not accept query parameters. "
        "Use URI templates (RFC 6570) for parameterised resources if needed.")

    heading2(doc, "9.2  Prompt Design")
    body_para(doc,
        "Prompts are valuable for complex, multi-step reasoning tasks where the prompt "
        "structure is well-known and reusable across many agent calls. Centralising them "
        "in the MCP server means prompt improvements are rolled out instantly without "
        "redeploying the agent.")

    code_para(doc,
"# Prompt definition example\n"
"@mcp.prompt()\n"
"def customer_briefing(customer_id: str, tone: str = 'professional') -> list[Message]:\n"
"    \"\"\"\n"
"    Generate a briefing prompt for a customer meeting.\n"
"    Args:\n"
"        customer_id: Customer to brief on.\n"
"        tone:        Writing tone — 'professional' or 'casual'.\n"
"    \"\"\"\n"
"    return [\n"
"        UserMessage(\n"
"            f'Prepare a {tone} meeting brief for customer {customer_id}. '\n"
"            f'Include: account status, recent orders, open issues, '\n"
"            f'and recommended talking points for a renewal conversation.'\n"
"        )\n"
"    ]")

    doc.add_page_break()

    # =========================================================================
    # 10. OBSERVABILITY & AUDIT
    # =========================================================================
    heading1(doc, "10.  Observability & Audit Trail")

    heading2(doc, "10.1  Four-Way Event Fan-Out")
    body_para(doc,
        "Every log_event() call fans out to four independent sinks simultaneously. "
        "No single sink failure blocks the others — degraded observability is always "
        "preferred over dropped requests.")

    code_para(doc,
"log_event(type='auth', sub='alice', path='/discover', valid=True)\n"
"   │\n"
"   ├──► In-memory ring buffer  (maxlen=500)    → GET /api/logs  fast read\n"
"   │     (always available; lost on restart)\n"
"   │\n"
"   ├──► stdout / print                         → docker logs / console\n"
"   │     (line-buffered; immediate)\n"
"   │\n"
"   ├──► logs/hub.log  (JSONL, line-buffered)   → persistent file log\n"
"   │     (survives process restart; grep-able)\n"
"   │\n"
"   └──► MySQL hub_events table                 → queryable history\n"
"         (disabled permanently if MySQL fails;\n"
"          restart required to re-enable)")

    heading2(doc, "10.2  Event Types & Fields")

    rich_table(doc,
        ["Event Type", "When Emitted", "Key Fields"],
        [
            ("auth",       "Every JWT validation attempt",        "ts, sub, roles, path, valid, _error"),
            ("request",    "Every HTTP request completes",        "ts, method, path, status, latency_ms"),
            ("routing",    "Hub selects MCP server",              "ts, method, server_id, server_ids, reason, intent"),
            ("tool_audit", "Before each MCP tool executes",       "ts, tool, service, sub, roles, args_keys"),
            ("error",      "Any uncaught runtime exception",      "ts, message, traceback, path"),
        ],
        col_widths_cm=[3.0, 5.5, 8.5])

    heading2(doc, "10.3  Audit Requirements")
    bullet_item(doc, "Every JWT validation must be logged (success and failure) with sub, roles, path.", bold_prefix="Auth: ")
    bullet_item(doc, "Every tool_audit event must log args_keys (not values) — PII must not appear in logs.", bold_prefix="Privacy: ")
    bullet_item(doc, "Routing decisions must include the reason and intent from the LLM.", bold_prefix="Routing: ")
    bullet_item(doc, "hub_events table provides queryable history for compliance review.", bold_prefix="Retention: ")
    bullet_item(doc, "JSONL file log survives process restarts and is readable by log aggregators (Splunk, ELK).", bold_prefix="Durability: ")

    callout(doc, "warning", "PII in Audit Logs",
        "audit_log() deliberately logs args_keys (the parameter names) but NOT the argument values. "
        "This gives traceability ('customer_360 was called with customer_id') without exposing "
        "the actual customer data in the log stream. Never log raw tool argument values.")

    doc.add_page_break()

    # =========================================================================
    # 11. SECURITY BEST PRACTICES
    # =========================================================================
    heading1(doc, "11.  Security Best Practices")

    heading2(doc, "11.1  Credential Management")
    bullet_item(doc, "Store all secrets in environment variables or a secrets manager (Vault, AWS Secrets Manager) — never in code.", bold_prefix="Rule: ")
    bullet_item(doc, "Add .env, *.pem, and private key files to .gitignore before the first commit.", bold_prefix="Rule: ")
    bullet_item(doc, "Rotate the RSA key pair annually or on any suspected compromise. Update JWKS immediately.", bold_prefix="Rule: ")
    bullet_item(doc, "Use separate MySQL users per MCP server with minimum required permissions (SELECT only for read servers).", bold_prefix="Rule: ")
    bullet_item(doc, "Audit all credential access via tool_audit events — never log credential values.", bold_prefix="Rule: ")

    heading2(doc, "11.2  Token Security")
    bullet_item(doc, "Use RS256 (asymmetric) — never HS256 for multi-server deployments.", bold_prefix="Algorithm: ")
    bullet_item(doc, "Always set aud (audience) on per-server tokens. Never issue tokens with no audience.", bold_prefix="Audience: ")
    bullet_item(doc, "Keep MCP token expiry short (1 hour). User session tokens may be longer (4–8 hours).", bold_prefix="Expiry: ")
    bullet_item(doc, "Never log full JWT values — they are bearer credentials.", bold_prefix="Logging: ")
    bullet_item(doc, "Validate exp, iss, aud, and signature on every request — never skip any claim.", bold_prefix="Validation: ")

    heading2(doc, "11.3  Network Security")
    bullet_item(doc, "All inter-service communication must use TLS (HTTPS) in production.", bold_prefix="TLS: ")
    bullet_item(doc, "The JWKS endpoint (/.well-known/jwks.json) must be HTTPS in production.", bold_prefix="JWKS: ")
    bullet_item(doc, "MCP server endpoints should not be publicly reachable — hub access only.", bold_prefix="Network isolation: ")
    bullet_item(doc, "Use a reverse proxy (nginx/Caddy) in front of all services for TLS termination.", bold_prefix="Proxy: ")

    heading2(doc, "11.4  Input Validation")
    bullet_item(doc, "Use parameterised queries for all database access — never string-format SQL.", bold_prefix="SQL: ")
    bullet_item(doc, "Validate all tool arguments against inputSchema before execution.", bold_prefix="Schema: ")
    bullet_item(doc, "Cap list sizes (e.g. limit <= 100) server-side regardless of what the caller requests.", bold_prefix="Limits: ")
    bullet_item(doc, "Sanitise and validate all URIs in resource requests.", bold_prefix="Resources: ")

    rich_table(doc,
        ["Security Property", "Implementation", "Threat Mitigated"],
        [
            ("Token forgery",            "RS256 — only hub's private key can sign",          "Forged tokens rejected at signature check"),
            ("Cross-server replay",      "Per-server aud claim validated on each request",   "Stolen token unusable on other servers"),
            ("Token theft window",       "1-hour MCP token expiry",                          "Short window limits damage from interception"),
            ("Credential isolation",     "Each layer uses its own credential",               "Compromise of one credential doesn't cascade"),
            ("Brute-force login",        "PBKDF2-SHA256 (200k iter) + rate limiting",        "Offline and online attacks slowed"),
            ("Dev mode leakage",         "MCP_AUTH_ENABLED=true by default",                 "Accidental open access in staging"),
            ("Secret file in git",       ".gitignore covers .env and .pem files",            "Secret exposure in source control"),
            ("PII in logs",              "audit_log() logs keys, not values",                "Customer data not in log streams"),
        ],
        col_widths_cm=[4.5, 5.5, 7.0])

    doc.add_page_break()

    # =========================================================================
    # 12. STANDARD PATTERNS & ANTI-PATTERNS
    # =========================================================================
    heading1(doc, "12.  Standard Patterns & Anti-Patterns")

    heading2(doc, "12.1  Recommended Patterns")

    heading3(doc, "Pattern 1 — New Routing Agent Per Request")
    body_para(doc,
        "Always instantiate a fresh LangGraph routing agent for each /discover request. "
        "A shared agent instance holds internal message-state that would be corrupted "
        "by interleaved concurrent requests.")
    callout(doc, "example", "Implementation",
        "def _agent_route(query, servers):\n"
        "    agent = create_react_agent(llm, [_make_routing_tool()])  # fresh per request\n"
        "    result = agent.invoke({'messages': [('user', build_prompt(query, servers))]})\n"
        "    return result")

    heading3(doc, "Pattern 2 — Registry Cache with Manual Invalidation")
    body_para(doc,
        "Cache the server registry in-process for 60 seconds to avoid a database round-trip "
        "on every /discover call. Expose a /refresh endpoint for immediate invalidation "
        "when the admin makes a change.")

    heading3(doc, "Pattern 3 — Background Task Decoupled from SSE Lifetime")
    body_para(doc,
        "Create the agent asyncio.Task BEFORE opening the SSE generator. If the browser "
        "disconnects, the generator closes but the Task continues. Save the final answer "
        "to the database regardless of client connection state.")

    heading3(doc, "Pattern 4 — Semantic Views for Tool Isolation")
    body_para(doc,
        "MCP tools should query semantic database views, not raw tables. The view abstracts "
        "the physical schema — if a base table is restructured, only the view needs updating; "
        "tool signatures and agent code remain unchanged.")

    heading3(doc, "Pattern 5 — Probe-Before-Route")
    body_para(doc,
        "Before serving a server from the registry, the hub can probe it with a test JWT "
        "(tools/list call). Unhealthy servers can be temporarily excluded from routing "
        "without being deleted from the registry.")

    heading2(doc, "12.2  Anti-Patterns to Avoid")

    rich_table(doc,
        ["Anti-Pattern", "Risk", "Correct Approach"],
        [
            ("Shared HMAC secret across servers",  "One compromised server leaks the signing key for all",      "RS256 with per-server JWKS verification"),
            ("Single token for all servers",        "Token replay across any server; large blast radius",        "Per-server audience-scoped tokens"),
            ("require_role() after business logic", "Data accessed before auth check — could leak on exception", "require_role() as the very first statement"),
            ("String-formatted SQL in tools",       "SQL injection via tool arguments",                          "Parameterised queries or ORM only"),
            ("Logging full JWT values",             "Bearer tokens in log files — instant credential leak",      "Log only sub, roles, path — never the token"),
            ("Logging tool argument values",        "PII (customer data) in log streams",                        "Log args_keys (parameter names) only"),
            ("Shared routing agent instance",       "Concurrent request state corruption, wrong server selected", "New agent instance per /discover request"),
            ("open dev mode in staging/prod",       "Unauthenticated callers get admin access",                  "Set MCP_AUTH_ENABLED=true and MCP_SERVER_ID always"),
            ("Committing .env or .pem to git",      "Credential exposure in version history forever",            ".gitignore before first commit; git-secret for auditing"),
            ("Catching all exceptions silently",    "Hides auth failures and data errors; breaks traceability",  "Log and re-raise, or return typed error responses"),
        ],
        col_widths_cm=[5.0, 5.5, 6.5])

    doc.add_page_break()

    # =========================================================================
    # 13. DEPLOYMENT CONSIDERATIONS
    # =========================================================================
    heading1(doc, "13.  Deployment Considerations")

    heading2(doc, "13.1  Environment Configuration Checklist")
    rich_table(doc,
        ["Variable", "Description", "Required In"],
        [
            ("MCP_AUTH_ENABLED",   "Enable JWT auth on MCP servers (must be 'true')",         "All non-dev environments"),
            ("MCP_SERVER_ID",      "This server's ID — used as JWT audience for validation",  "All MCP servers"),
            ("HUB_SERVER_URL",     "Hub base URL for JWKS fetch",                             "All MCP servers"),
            ("MCP_JWT_ISSUER",     "Expected issuer string in JWT (must match hub config)",   "All MCP servers"),
            ("MYSQL_USER",         "Database username for MCP server data access",            "All MCP servers with DB"),
            ("MYSQL_PASSWORD",     "Database password",                                        "All MCP servers with DB"),
            ("MYSQL_DATABASE",     "Target database name",                                     "All MCP servers with DB"),
            ("MYSQL_HOST/PORT",    "Database host and port",                                   "All services using DB"),
            ("CHAT_USERS",         "Comma-separated user:password pairs for chat server",     "Chat server"),
            ("HUB_JWT_SECRET",     "If using HMAC (not recommended): shared secret",          "Hub (HMAC mode only)"),
        ],
        col_widths_cm=[4.5, 7.5, 5.0])

    heading2(doc, "13.2  Production Readiness Checklist")
    bullet_item(doc, "TLS/HTTPS on all inter-service endpoints", bold_prefix="Network: ")
    bullet_item(doc, "MCP_AUTH_ENABLED=true on all MCP servers", bold_prefix="Auth: ")
    bullet_item(doc, "MCP_SERVER_ID set on all MCP servers", bold_prefix="Auth: ")
    bullet_item(doc, "RSA key pair generated and private.pem accessible only to hub process", bold_prefix="Keys: ")
    bullet_item(doc, ".env and .pem excluded from version control (.gitignore verified)", bold_prefix="Secrets: ")
    bullet_item(doc, "hub_events MySQL table accessible and indexed", bold_prefix="Observability: ")
    bullet_item(doc, "logs/ directory writable by hub process", bold_prefix="Observability: ")
    bullet_item(doc, "Rate limiting active on /login endpoint", bold_prefix="Security: ")
    bullet_item(doc, "Background task cleanup implemented (prevent _bg_tasks memory leak)", bold_prefix="Stability: ")
    bullet_item(doc, "Health check endpoint (GET /health) monitored by infrastructure", bold_prefix="Operations: ")
    bullet_item(doc, "JWKS endpoint reachable by all MCP servers", bold_prefix="Operations: ")
    bullet_item(doc, "Database connection pool tuned (pool_recycle <= 1800s for MySQL)", bold_prefix="Database: ")

    # =========================================================================
    # Footer
    # =========================================================================
    spacer(doc, 16)
    section_divider(doc)
    foot_p = doc.add_paragraph()
    foot_p.alignment = WD_ALIGN_PARAGRAPH.CENTER
    add_run(foot_p,
        f"MCP Hub Design Document  ·  Version 1.0  ·  {datetime.date.today().strftime('%B %d, %Y')}  ·  Internal",
        size=9, color="999999", italic=True)

    doc.save(str(OUT))
    print(f"\nSaved: {OUT}\n")


if __name__ == "__main__":
    build()
