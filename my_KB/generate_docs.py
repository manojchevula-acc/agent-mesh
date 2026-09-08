"""
AgentMesh Technical Documentation Generator — v2
Run: python my_KB/generate_docs.py
Output: my_KB/AgentMesh_Technical_Documentation.pdf
"""

import os
from datetime import date
from reportlab.lib.pagesizes import A4
from reportlab.lib import colors
from reportlab.lib.units import cm
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.enums import TA_CENTER, TA_JUSTIFY
from reportlab.platypus import (
    SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle,
    PageBreak, HRFlowable, KeepTogether, Flowable,
)
from reportlab.pdfgen import canvas as pdfcanvas

# ── Palette ───────────────────────────────────────────────────────────────────
NAVY       = colors.HexColor("#1B2A4A")
BLUE       = colors.HexColor("#2563EB")
LIGHT_BLUE = colors.HexColor("#DBEAFE")
SLATE      = colors.HexColor("#475569")
LIGHT_GRAY = colors.HexColor("#F1F5F9")
MID_GRAY   = colors.HexColor("#CBD5E1")
DARK_TEXT  = colors.HexColor("#1E293B")
WHITE      = colors.white
GREEN      = colors.HexColor("#16A34A")
DARK_GREEN = colors.HexColor("#166534")
AMBER      = colors.HexColor("#D97706")
RED_SOFT   = colors.HexColor("#DC2626")
TEAL       = colors.HexColor("#0891B2")
PURPLE     = colors.HexColor("#7C3AED")
INDIGO     = colors.HexColor("#1E40AF")

PAGE_W, PAGE_H = A4
MARGIN    = 2.2 * cm
CONTENT_W = PAGE_W - 2 * MARGIN

# ── Styles ────────────────────────────────────────────────────────────────────
def build_styles():
    base = getSampleStyleSheet()
    def ps(name, **kw):
        return ParagraphStyle(name, parent=base["Normal"], **kw)

    return dict(
        h1     = ps("H1",  fontName="Helvetica-Bold", fontSize=16, textColor=NAVY,
                     spaceBefore=16, spaceAfter=6, leading=20),
        h2     = ps("H2",  fontName="Helvetica-Bold", fontSize=12, textColor=NAVY,
                     spaceBefore=12, spaceAfter=4, leading=16),
        h3     = ps("H3",  fontName="Helvetica-Bold", fontSize=10, textColor=BLUE,
                     spaceBefore=8, spaceAfter=3, leading=14),
        body   = ps("Body", fontName="Helvetica", fontSize=9.5, textColor=DARK_TEXT,
                     spaceBefore=3, spaceAfter=3, leading=14, alignment=TA_JUSTIFY),
        bullet = ps("Bullet", fontName="Helvetica", fontSize=9.5, textColor=DARK_TEXT,
                     leftIndent=14, spaceBefore=2, spaceAfter=2, leading=13),
        bullet2= ps("Bullet2", fontName="Helvetica", fontSize=9, textColor=DARK_TEXT,
                     leftIndent=26, spaceBefore=1, spaceAfter=1, leading=12),
        code   = ps("Code", fontName="Courier", fontSize=8, textColor=DARK_TEXT,
                     backColor=LIGHT_GRAY, leftIndent=10, rightIndent=10,
                     spaceBefore=4, spaceAfter=4, leading=12, borderPad=4),
        caption= ps("Caption", fontName="Helvetica-Oblique", fontSize=8, textColor=SLATE,
                     alignment=TA_CENTER, spaceBefore=2, spaceAfter=6),
        toc_h  = ps("TOCH",  fontName="Helvetica-Bold", fontSize=9.5, textColor=DARK_TEXT,
                     spaceBefore=3, spaceAfter=3, leading=14),
        toc_e  = ps("TOCE",  fontName="Helvetica", fontSize=9, textColor=SLATE,
                     spaceBefore=2, spaceAfter=2, leading=13, leftIndent=12),
        label  = ps("Label", fontName="Helvetica-Bold", fontSize=8.5, textColor=WHITE,
                     alignment=TA_CENTER, leading=11),
    )

S = build_styles()

# ── Shorthand ─────────────────────────────────────────────────────────────────
def P(text, style="body"):  return Paragraph(text, S[style])
def SP(n=6):                return Spacer(1, n)
def HR():                   return HRFlowable(width="100%", thickness=0.5,
                                              color=MID_GRAY, spaceAfter=6, spaceBefore=6)
def B(text, s="bullet"):    return Paragraph(f"• &nbsp;{text}", S[s])
def B2(text):               return Paragraph(f"&nbsp;&nbsp;&nbsp;– &nbsp;{text}", S["bullet2"])

def sec_hdr(num, title):
    return [SP(4), P(f"{num}. {title}", "h1"),
            HRFlowable(width="100%", thickness=1.5, color=BLUE, spaceBefore=2, spaceAfter=8)]

def sub(t):  return P(t, "h2")
def sub2(t): return P(t, "h3")

# ── Table factory ─────────────────────────────────────────────────────────────
BASE_TS = TableStyle([
    ("BACKGROUND",    (0,0),(-1,0), NAVY),
    ("TEXTCOLOR",     (0,0),(-1,0), WHITE),
    ("FONTNAME",      (0,0),(-1,0), "Helvetica-Bold"),
    ("FONTSIZE",      (0,0),(-1,0), 9),
    ("BOTTOMPADDING", (0,0),(-1,0), 6),
    ("TOPPADDING",    (0,0),(-1,0), 6),
    ("ROWBACKGROUNDS",(0,1),(-1,-1),[WHITE, LIGHT_GRAY]),
    ("FONTNAME",      (0,1),(-1,-1),"Helvetica"),
    ("FONTSIZE",      (0,1),(-1,-1), 8.5),
    ("TOPPADDING",    (0,1),(-1,-1), 4),
    ("BOTTOMPADDING", (0,1),(-1,-1), 4),
    ("LEFTPADDING",   (0,0),(-1,-1), 7),
    ("RIGHTPADDING",  (0,0),(-1,-1), 7),
    ("GRID",          (0,0),(-1,-1), 0.4, MID_GRAY),
    ("VALIGN",        (0,0),(-1,-1),"MIDDLE"),
])

def mktable(data, cws, style=None):
    rows = []
    for i, row in enumerate(data):
        rows.append([P(str(c), "label" if i == 0 else "body") for c in row])
    t = Table(rows, colWidths=cws, repeatRows=1)
    t.setStyle(style or BASE_TS)
    return t


# ═══════════════════════════════════════════════════════════════════════════════
#  DIAGRAM 1 — Full System Architecture (redesigned)
#  Shows: Frontend → API Server → Compliance + PriceAssist
#         PriceAssist → DataAgent + RAGAgent
#         DataAgent → DataLayer MCP → MySQL
#         RAGAgent  → RAG Service MCP → Qdrant
# ═══════════════════════════════════════════════════════════════════════════════
class ArchDiagram(Flowable):
    def __init__(self, width, height=320):
        super().__init__()
        self.width  = width
        self.height = height

    # ── drawing primitives ────────────────────────────────────────────────────
    def _box(self, c, x, y, w, h, label, sub="", fill=NAVY, tc=WHITE, fs=8, bold=True):
        c.setFillColor(fill)
        c.setStrokeColor(colors.HexColor("#0F172A"))
        c.setLineWidth(0.6)
        c.roundRect(x, y, w, h, 4, fill=1, stroke=1)
        c.setFillColor(tc)
        fn = "Helvetica-Bold" if bold else "Helvetica"
        c.setFont(fn, fs)
        ty = y + h/2 + (5 if sub else 2)
        c.drawCentredString(x + w/2, ty, label)
        if sub:
            c.setFont("Helvetica", fs - 1.5)
            c.drawCentredString(x + w/2, y + h/2 - 7, sub)

    def _arrow(self, c, x1, y1, x2, y2, label="", lx=None, ly=None):
        c.setStrokeColor(SLATE)
        c.setLineWidth(1)
        c.line(x1, y1, x2, y2)
        dx, dy = x2-x1, y2-y1
        L = (dx**2+dy**2)**0.5
        if L == 0: return
        ux, uy = dx/L, dy/L
        ax, ay = x2-ux*7, y2-uy*7
        px, py = -uy*3.5, ux*3.5
        c.setFillColor(SLATE)
        p = c.beginPath()
        p.moveTo(x2, y2); p.lineTo(ax+px, ay+py); p.lineTo(ax-px, ay-py); p.close()
        c.drawPath(p, fill=1, stroke=0)
        if label:
            c.setFillColor(SLATE)
            c.setFont("Helvetica-Oblique", 6.5)
            lx = lx if lx is not None else (x1+x2)/2
            ly = ly if ly is not None else (y1+y2)/2
            c.drawCentredString(lx, ly, label)

    def _dashed_rect(self, c, x, y, w, h, label, fill, alpha=0.08):
        c.setFillColor(fill)
        c.setStrokeColor(fill)
        c.setLineWidth(1)
        c.setDash(4, 3)
        c.roundRect(x, y, w, h, 6, fill=0, stroke=1)
        c.setDash()
        c.setFont("Helvetica-Bold", 7)
        c.setFillColor(fill)
        c.drawString(x+6, y+h-10, label)

    def draw(self):
        c = self.canv
        W, H = self.width, self.height

        BH = 26   # standard box height
        SH = 22   # small box height

        # ── Row positions (top to bottom) ─────────────────────────────────────
        R1 = H - 30   # Frontend
        R2 = R1 - 55  # API Server
        R3 = R2 - 60  # ComplianceAgent | PriceAssistAgent
        R4 = R3 - 58  # DataAgent | RAGAgent
        R5 = R4 - 52  # DataLayer MCP | RAG Service MCP
        R6 = R5 - 48  # MySQL | Qdrant

        # ── Row 1: Frontend (full width) ──────────────────────────────────────
        FW = W - 20
        FX = 10
        self._box(c, FX, R1, FW, BH, "React Frontend", "Port 3000  ·  TypeScript + Vite + TailwindCSS", BLUE)

        # arrow down centre
        CX = W/2
        self._arrow(c, CX, R1, CX, R2+BH+2, "REST + SSE Streaming", lx=CX+50, ly=(R1+R2+BH)/2)

        # ── Row 2: API Server (full width) + ChromaDB bubble ─────────────────
        AW = W * 0.62
        AX = (W - AW)/2
        self._box(c, AX, R2, AW, BH, "API Server  /  Orchestrator",
                  "Port 8000  ·  Starlette + uvicorn  ·  6-Stage Pipeline", NAVY)

        # ChromaDB side label
        CBW = W * 0.26
        CBX = AX + AW + 8
        self._box(c, CBX, R2+2, CBW, SH, "ChromaDB", "Semantic Cache", TEAL, WHITE, 7.5)
        c.setStrokeColor(TEAL); c.setLineWidth(0.7); c.setDash(3,2)
        c.line(AX+AW, R2+BH/2, CBX, R2+SH/2+2)
        c.setDash()

        # ── Row 3: ComplianceAgent (left) + PriceAssistAgent (right, wider) ──
        CAW = W * 0.30
        CAX = 10
        self._box(c, CAX, R3, CAW, BH,
                  "ComplianceAgent", "Port 8015  ·  GPT-OSS 20B", colors.HexColor("#374151"))

        PAW = W * 0.55
        PAX = W - PAW - 10
        self._box(c, PAX, R3, PAW, BH,
                  "PriceAssistAgent  —  Primary Orchestrator",
                  "Port 8018  ·  GPT-OSS 120B  ·  Intent classify → route → synthesise", TEAL)

        # API → ComplianceAgent
        self._arrow(c, AX+AW*0.22, R2, CAX+CAW/2, R3+BH,
                    "A2A", lx=AX+20, ly=(R2+R3+BH)/2)
        # API → PriceAssistAgent
        self._arrow(c, AX+AW*0.68, R2, PAX+PAW/2, R3+BH,
                    "A2A", lx=AX+AW*0.75, ly=(R2+R3+BH)/2)

        # ── Dashed grouping box around DataAgent+RAGAgent chain ───────────────
        self._dashed_rect(c, PAX-2, R6-6, PAW+4, R3-R6+BH+8,
                          "PriceAssistAgent orchestrates ↓", TEAL)

        # ── Row 4: DataAgent | RAGAgent (under PriceAssistAgent) ─────────────
        DAW = PAW * 0.44
        DAX = PAX + 4
        self._box(c, DAX, R4, DAW, BH,
                  "DataAgent", "Port 8016  ·  QWen 3.6-27B  ·  18 tools", INDIGO)

        RAW = PAW * 0.44
        RAX = PAX + PAW - RAW - 4
        self._box(c, RAX, R4, RAW, BH,
                  "RAGAgent", "Port 8017  ·  QWen 3.6-27B  ·  search_documents", INDIGO)

        PA_CX = PAX + PAW/2
        self._arrow(c, PA_CX - 28, R3, DAX+DAW/2, R4+BH, "A2A", lx=PA_CX-60, ly=(R3+R4+BH)/2)
        self._arrow(c, PA_CX + 28, R3, RAX+RAW/2, R4+BH, "A2A", lx=PA_CX+55, ly=(R3+R4+BH)/2)

        # ── Row 5: DataLayer MCP | RAG Service MCP ───────────────────────────
        DLW = DAW + 6
        DLX = DAX - 3
        self._box(c, DLX, R5, DLW, SH,
                  "DataLayer MCP Service", "Port 9100  ·  FastMCP", GREEN, WHITE, 7.5)

        RSW = RAW + 6
        RSX = RAX - 3
        self._box(c, RSX, R5, RSW, SH,
                  "RAG Service MCP", "Port 9000  ·  FastAPI + MCP", GREEN, WHITE, 7.5)

        self._arrow(c, DAX+DAW/2, R4, DLX+DLW/2, R5+SH, "MCP", lx=DAX+DAW/2+22, ly=(R4+R5+SH)/2)
        self._arrow(c, RAX+RAW/2, R4, RSX+RSW/2, R5+SH, "MCP", lx=RAX+RAW/2+22, ly=(R4+R5+SH)/2)

        # ── Row 6: MySQL | Qdrant ─────────────────────────────────────────────
        DBW = DLW
        self._box(c, DLX, R6, DBW, SH,
                  "MySQL  (fab_semantic)", "18 views  ·  14 curated tables", AMBER, WHITE, 7.5)

        QDW = RSW
        self._box(c, RSX, R6, QDW, SH,
                  "Qdrant  (Vector DB)", "5 FAB PDFs  ·  384-dim embeddings", AMBER, WHITE, 7.5)

        self._arrow(c, DLX+DBW/2, R5, DLX+DBW/2, R6+SH)
        self._arrow(c, RSX+QDW/2, R5, RSX+QDW/2, R6+SH)

    def wrap(self, *_): return self.width, self.height


# ═══════════════════════════════════════════════════════════════════════════════
#  DIAGRAM 2 — PriceAssistAgent Intent Routing
# ═══════════════════════════════════════════════════════════════════════════════
class IntentDiagram(Flowable):
    def __init__(self, width, height=160):
        super().__init__()
        self.width  = width
        self.height = height

    def draw(self):
        c = self.canv
        W, H = self.width, self.height

        def box(x, y, w, h, label, sub="", fill=NAVY, fs=8):
            c.setFillColor(fill); c.setStrokeColor(NAVY); c.setLineWidth(0.5)
            c.roundRect(x, y, w, h, 4, fill=1, stroke=1)
            c.setFillColor(WHITE); c.setFont("Helvetica-Bold", fs)
            c.drawCentredString(x+w/2, y+h/2+(4 if sub else 2), label)
            if sub:
                c.setFont("Helvetica", fs-1.5)
                c.drawCentredString(x+w/2, y+h/2-6, sub)

        def arr(x1,y1,x2,y2,lbl="",lx=None,ly=None):
            c.setStrokeColor(SLATE); c.setLineWidth(0.9); c.line(x1,y1,x2,y2)
            dx,dy=x2-x1,y2-y1; L=(dx**2+dy**2)**0.5
            if L==0: return
            ux,uy=dx/L,dy/L; ax,ay=x2-ux*6,y2-uy*6; px,py=-uy*3,ux*3
            c.setFillColor(SLATE); p=c.beginPath()
            p.moveTo(x2,y2); p.lineTo(ax+px,ay+py); p.lineTo(ax-px,ay-py); p.close()
            c.drawPath(p,fill=1,stroke=0)
            if lbl:
                c.setFillColor(SLATE); c.setFont("Helvetica-Oblique",6.5)
                c.drawCentredString(lx or (x1+x2)/2, ly or (y1+y2)/2, lbl)

        BH, BW = 28, 160
        # Input box
        box(W/2-BW/2, H-32, BW, BH, "User Query", "from API Server", NAVY)

        # Intent classifier diamond-like box
        DW, DH = 180, 26
        box(W/2-DW/2, H-32-50, DW, DH, "PriceAssistAgent — Intent Classification",
            "'data' | 'knowledge' | 'hybrid'", TEAL, 7.5)
        arr(W/2, H-32, W/2, H-32-24)

        # Three branches
        branches = [
            (0.18, "DATA", "Structured query\nSQL views only", INDIGO,
             "query_structured_data()", "DataAgent → DataLayer → MySQL"),
            (0.50, "HYBRID", "Both data + docs\nneeded", colors.HexColor("#065F46"),
             "Both tools called", "DataAgent + RAGAgent → synthesis"),
            (0.82, "KNOWLEDGE", "Policy / regulatory\ndocs only", colors.HexColor("#581C87"),
             "query_knowledge_base()", "RAGAgent → RAG Service → Qdrant"),
        ]

        box_y = H - 32 - 50 - 58
        tool_y = box_y - 48

        for frac, intent, detail, fill, tool_lbl, result_lbl in branches:
            bx = W * frac - 68
            # branch box
            box(bx, box_y, 136, 36, intent, detail, fill, 8)
            # diagonal arrow from classifier centre
            arr(W/2, H-32-50, bx+68, box_y+36)
            # tool label
            c.setFillColor(fill); c.setFont("Helvetica-Bold", 7); c.drawCentredString(bx+68, box_y-10, tool_lbl)
            c.setFillColor(SLATE); c.setFont("Helvetica", 6.5); c.drawCentredString(bx+68, box_y-20, result_lbl)

        # Synthesis arrow to output
        arr(W/2, box_y-28, W/2, tool_y-2, "synthesise")
        box(W/2-BW/2, tool_y-30, BW, 26,
            "Final Answer  +  <llm_reasoning> block", "", DARK_GREEN, 7.5)

    def wrap(self, *_): return self.width, self.height


# ═══════════════════════════════════════════════════════════════════════════════
#  DIAGRAM 3 — 6-Stage Pipeline
# ═══════════════════════════════════════════════════════════════════════════════
class PipelineDiagram(Flowable):
    def __init__(self, width, height=90):
        super().__init__()
        self.width  = width
        self.height = height

    def draw(self):
        c = self.canv
        W, H = self.width, self.height
        stages = [
            ("Input\nGuardrail",   "Regex · <1ms\nNo LLM",      RED_SOFT),
            ("RBAC\nValidation",   "Rules engine\n<1ms",         BLUE),
            ("Semantic\nCache",    "ChromaDB\n~50ms HIT",        TEAL),
            ("Compliance\nGate",   "LLM · Groq\n~2-4s",         AMBER),
            ("Domain\nRouting",    "A2A · Agents\n5-60s",        GREEN),
            ("PII\nRedaction",     "Regex · <1ms\nOutput",       PURPLE),
        ]
        ARR = 14
        BW  = (W - (len(stages)-1)*ARR) / len(stages)
        BH  = H - 22
        BY  = 12

        for i,(name,detail,fill) in enumerate(stages):
            x = i*(BW+ARR)
            # shadow
            c.setFillColor(colors.HexColor("#00000022")); c.setStrokeColor(colors.Color(0,0,0,0))
            c.roundRect(x+2, BY-2, BW, BH, 4, fill=1, stroke=0)
            # box
            c.setFillColor(fill); c.setStrokeColor(colors.HexColor("#0F172A")); c.setLineWidth(0.5)
            c.roundRect(x, BY, BW, BH, 4, fill=1, stroke=1)
            # step number badge
            c.setFillColor(WHITE); c.setStrokeColor(WHITE); c.setLineWidth(0)
            c.circle(x+10, BY+BH-9, 7, fill=1, stroke=0)
            c.setFillColor(fill); c.setFont("Helvetica-Bold", 7)
            c.drawCentredString(x+10, BY+BH-11, str(i+1))
            # name
            c.setFillColor(WHITE); c.setFont("Helvetica-Bold", 7.5)
            for j,ln in enumerate(name.split("\n")):
                c.drawCentredString(x+BW/2, BY+BH-22-j*10, ln)
            # detail
            c.setFont("Helvetica", 6.5)
            for j,ln in enumerate(detail.split("\n")):
                c.drawCentredString(x+BW/2, BY+14-j*9, ln)
            # arrow
            if i < len(stages)-1:
                ax = x+BW; ay = BY+BH/2
                c.setStrokeColor(MID_GRAY); c.setLineWidth(1.2); c.line(ax, ay, ax+ARR, ay)
                c.setFillColor(SLATE)
                p = c.beginPath()
                p.moveTo(ax+ARR, ay); p.lineTo(ax+ARR-5,ay+3); p.lineTo(ax+ARR-5,ay-3); p.close()
                c.drawPath(p, fill=1, stroke=0)

    def wrap(self, *_): return self.width, self.height


# ═══════════════════════════════════════════════════════════════════════════════
#  DIAGRAM 4 — Semantic Cache Decision Flow
# ═══════════════════════════════════════════════════════════════════════════════
class CacheDiagram(Flowable):
    def __init__(self, width, height=260):
        super().__init__()
        self.width  = width
        self.height = height

    def draw(self):
        c = self.canv
        W, H = self.width, self.height
        CX = W / 2

        BW, BH = 150, 22
        DW, DH = 140, 30

        def box(label, y, fill=NAVY, bw=BW, bh=BH):
            x = CX - bw/2
            c.setFillColor(fill); c.setStrokeColor(NAVY); c.setLineWidth(0.5)
            c.roundRect(x, y, bw, bh, 3, fill=1, stroke=1)
            c.setFillColor(WHITE); c.setFont("Helvetica-Bold", 7.5)
            c.drawCentredString(CX, y+bh/2+1, label)

        def diamond(label, y, fill=BLUE):
            hw, hh = DW/2, DH/2
            cx, cy = CX, y+hh
            c.setFillColor(fill); c.setStrokeColor(NAVY); c.setLineWidth(0.5)
            p = c.beginPath()
            p.moveTo(cx,cy+hh); p.lineTo(cx+hw,cy); p.lineTo(cx,cy-hh); p.lineTo(cx-hw,cy); p.close()
            c.drawPath(p,fill=1,stroke=1)
            c.setFillColor(WHITE); c.setFont("Helvetica-Bold", 7)
            c.drawCentredString(cx, cy+1, label)

        def dn(y1, y2, lbl=""):
            c.setStrokeColor(SLATE); c.setLineWidth(0.8); c.line(CX,y1,CX,y2+5)
            c.setFillColor(SLATE)
            p=c.beginPath(); p.moveTo(CX,y2); p.lineTo(CX-4,y2+7); p.lineTo(CX+4,y2+7); p.close()
            c.drawPath(p,fill=1,stroke=0)
            if lbl:
                c.setFillColor(SLATE); c.setFont("Helvetica-Oblique",6.5)
                c.drawString(CX+4, (y1+y2)/2, lbl)

        def side(label, y, xdir, fill):
            bw, bh = 105, 18
            x = CX + xdir*(DW/2+4) if xdir>0 else CX - xdir*(DW/2+4) - bw
            x = CX+(DW/2+4) if xdir>0 else CX-(DW/2+4)-bw
            c.setFillColor(fill); c.setStrokeColor(NAVY); c.setLineWidth(0.4)
            c.roundRect(x, y, bw, bh, 3, fill=1, stroke=1)
            c.setFillColor(WHITE); c.setFont("Helvetica-Bold", 6.5)
            c.drawCentredString(x+bw/2, y+bh/2+1, label)
            # horizontal connector
            sx = CX+(DW/2) if xdir>0 else CX-(DW/2)
            ex = x if xdir>0 else x+bw
            c.setStrokeColor(SLATE); c.setLineWidth(0.6); c.line(sx, y+bh/2, ex, y+bh/2)

        top = H - 16
        box("Query Input", top, NAVY)
        dn(top, top-22)
        box("Entity Extraction (LLM + regex)", top-44, INDIGO)
        dn(top-44, top-66)
        box("Embed  (all-MiniLM-L6-v2  384-dim)", top-88, TEAL)
        dn(top-88, top-110)
        box("ChromaDB  Top-3 Similarity Lookup", top-132, TEAL)
        dn(top-132, top-154)

        diamond("Entity Gate", top-186)
        dn(top-132-BH, top-157, "pass")

        side("MISS (entity mismatch)", top-180, 1, RED_SOFT)
        dn(top-186-DH, top-186-DH-18)

        diamond("Zone Decision", top-186-DH-20-DH)
        dn(top-186-DH, top-186-DH-22, "entities match")

        zone_y = top-186-DH-20-DH
        side("< 0.75  →  MISS", zone_y+6, -1, RED_SOFT)
        side("0.75–0.85  →  Suggest", zone_y-14, 1, AMBER)
        side("0.85–0.92  →  LLM Judge", zone_y-34, 1, BLUE)

        hit_y = zone_y - DH - 30
        dn(zone_y-DH, hit_y+BH, "≥ 0.92  HIT")
        box("CACHE HIT  —  ~50 ms, zero LLM calls", hit_y, GREEN)

    def wrap(self, *_): return self.width, self.height


# ═══════════════════════════════════════════════════════════════════════════════
#  PAGE TEMPLATES
# ═══════════════════════════════════════════════════════════════════════════════
def cover_page(cv, doc):
    cv.saveState()
    w, h = A4
    cv.setFillColor(NAVY); cv.rect(0, h-210, w, 210, fill=1, stroke=0)
    cv.setFillColor(BLUE);  cv.rect(0, h-215, w, 5, fill=1, stroke=0)

    cv.setFillColor(WHITE)
    cv.setFont("Helvetica-Bold", 30)
    cv.drawCentredString(w/2, h-82, "AgentMesh")
    cv.setFont("Helvetica", 17)
    cv.drawCentredString(w/2, h-112, "FAB Banking AI Agent Platform")
    cv.setFont("Helvetica", 11)
    cv.setFillColor(LIGHT_BLUE)
    cv.drawCentredString(w/2, h-145, "Technical Documentation  &  Development Record")
    cv.setFont("Helvetica", 9)
    cv.setFillColor(MID_GRAY)
    cv.drawCentredString(w/2, h-168, f"Version 2.0  ·  {date.today().strftime('%B %Y')}  ·  Confidential — Internal Use Only")

    chips = [("3","Microservices"),("4","AI Agents"),("7","RBAC Roles"),
             ("18","Data Tools"),("5","Banking Docs"),("3","Eval Layers"),("10+","Lessons")]
    cw, ch = 72, 52; gap = 10
    total = len(chips)*cw+(len(chips)-1)*gap
    sx = (w-total)/2; cy = h-296
    for i,(n,l) in enumerate(chips):
        cx = sx+i*(cw+gap)
        cv.setFillColor(LIGHT_GRAY); cv.setStrokeColor(MID_GRAY); cv.setLineWidth(0.4)
        cv.roundRect(cx, cy, cw, ch, 5, fill=1, stroke=1)
        cv.setFillColor(BLUE); cv.setFont("Helvetica-Bold",20)
        cv.drawCentredString(cx+cw/2, cy+ch-24, n)
        cv.setFillColor(SLATE); cv.setFont("Helvetica",7.5)
        cv.drawCentredString(cx+cw/2, cy+8, l)

    cv.setStrokeColor(MID_GRAY); cv.setLineWidth(0.5)
    cv.line(MARGIN, h-368, w-MARGIN, h-368)

    cv.setFillColor(SLATE); cv.setFont("Helvetica-Bold",9)
    cv.drawCentredString(w/2, h-388, "Built with:")
    techs = ["Python 3.13","Microsoft MAF","Groq LLM API","React 18 + TypeScript",
             "Qdrant Vector DB","ChromaDB","MySQL","OpenTelemetry","FastMCP","Starlette"]
    px = MARGIN; py = h-412
    for t in techs:
        tw = cv.stringWidth(t,"Helvetica",8)+14
        if px+tw > w-MARGIN: px=MARGIN; py-=20
        cv.setFillColor(LIGHT_BLUE); cv.setStrokeColor(BLUE); cv.setLineWidth(0.4)
        cv.roundRect(px, py, tw, 16, 4, fill=1, stroke=1)
        cv.setFillColor(NAVY); cv.setFont("Helvetica",8); cv.drawString(px+7, py+4, t)
        px += tw+8

    cv.setFillColor(NAVY); cv.rect(0, 0, w, 55, fill=1, stroke=0)
    cv.setFillColor(WHITE); cv.setFont("Helvetica",9)
    cv.drawString(MARGIN, 34, "First Abu Dhabi Bank  —  AI Centre of Excellence")
    cv.drawRightString(w-MARGIN, 34, f"Version 2.0  ·  {date.today().strftime('%B %Y')}")
    cv.setFont("Helvetica",8); cv.setFillColor(LIGHT_BLUE)
    cv.drawCentredString(w/2, 16, "CONFIDENTIAL — Internal Use Only")
    cv.restoreState()


def later_pages(cv, doc):
    cv.saveState()
    w, h = A4
    cv.setFillColor(NAVY); cv.rect(0, h-32, w, 32, fill=1, stroke=0)
    cv.setFillColor(WHITE); cv.setFont("Helvetica-Bold",8)
    cv.drawString(MARGIN, h-19, "AgentMesh — FAB Banking AI Agent Platform")
    cv.setFont("Helvetica",8)
    cv.drawRightString(w-MARGIN, h-19, "Technical Documentation")
    cv.setStrokeColor(MID_GRAY); cv.setLineWidth(0.4)
    cv.line(MARGIN, 30, w-MARGIN, 30)
    cv.setFillColor(SLATE); cv.setFont("Helvetica",7.5)
    cv.drawCentredString(w/2, 17, f"Page {doc.page}  ·  Confidential — Internal Use Only")
    cv.restoreState()


# ═══════════════════════════════════════════════════════════════════════════════
#  CONTENT SECTIONS
# ═══════════════════════════════════════════════════════════════════════════════

def toc():
    entries = [
        ("1.","Executive Summary"),
        ("2.","System Architecture"),
        ("3.","Processing Pipeline — Six Stages"),
        ("4.","Security & Compliance Layer"),
        ("5.","Agent Mesh Topology & Orchestration"),
        ("6.","Data & Knowledge Services"),
        ("7.","Semantic Response Cache"),
        ("8.","Conversation Memory & Multi-Turn"),
        ("9.","AI Reasoning Explainability"),
        ("10.","Human-in-the-Loop (HITL)"),
        ("11.","Observability & Audit Trail"),
        ("12.","Evaluation Framework"),
        ("13.","Development Lessons Learned"),
        ("14.","Project Structure"),
    ]
    out = [SP(8), P("Contents","h1"),
           HRFlowable(width="100%",thickness=1.5,color=BLUE,spaceBefore=2,spaceAfter=10)]
    for num,title in entries:
        row = Table([[P(num,"toc_h"), P(title,"toc_e")]],
                    colWidths=[28, CONTENT_W-28])
        row.setStyle(TableStyle([
            ("VALIGN",(0,0),(-1,-1),"TOP"),
            ("LEFTPADDING",(0,0),(-1,-1),0),("RIGHTPADDING",(0,0),(-1,-1),0),
            ("BOTTOMPADDING",(0,0),(-1,-1),3),("TOPPADDING",(0,0),(-1,-1),3),
        ]))
        out.append(row)
    out.append(PageBreak())
    return out


def sec1():
    out = sec_hdr("1","Executive Summary")
    out += [
        P("AgentMesh is a distributed AI agent orchestration platform built for First Abu Dhabi Bank (FAB) "
          "to deliver intelligent, compliant pricing assistance for corporate banking products. The system "
          "combines four specialised AI agents, a multi-layer security pipeline, structured data intelligence "
          "from a MySQL semantic layer, and regulatory document retrieval from a Qdrant vector database — "
          "all wired together with full audit logging, OpenTelemetry distributed tracing, and a React "
          "chat interface that streams responses in real time."),
        SP(6),
        sub("What It Solves"),
        P("Corporate pricing decisions at FAB require synthesising data from multiple systems — customer "
          "360 profiles, historical deal margins, regulatory concentration limits, CBUAE circulars, and "
          "internal policy documents — while enforcing strict role-based access controls and compliance "
          "rules. Analysts previously spent hours manually cross-referencing these sources. AgentMesh "
          "automates this synthesis in seconds while maintaining a full, immutable audit trail for "
          "regulatory review."),
        SP(6), sub("Platform Capabilities at a Glance"),
    ]
    data = [
        ["Capability","Detail"],
        ["Microservices","Agent Mesh Orchestrator · DataLayer-as-Service · RAG-as-Service"],
        ["AI Agents","ComplianceAgent · DataAgent · RAGAgent · PriceAssistAgent (primary orchestrator)"],
        ["User Roles","7 roles: customer · relationship_manager · branch_operations_officer · credit_officer · compliance_officer · operations_manager · platform_administrator"],
        ["Data Intelligence","18 MCP tools over MySQL fab_semantic views — customer 360, pricing, margins, RWA, non-compliant deals, win/loss, competitor analysis"],
        ["Knowledge Retrieval","5 FAB banking PDFs in Qdrant with hybrid dense+sparse retrieval, cross-encoder reranking, RAGAS evaluation"],
        ["Security","4 independent layers: regex guardrails → RBAC rules → LLM compliance gate → PII output redaction"],
        ["Semantic Cache","ChromaDB vector cache with entity gating, zone-based routing, LLM gray-zone judge, paraphrase augmentation — serves hits in ~50ms"],
        ["Conversation Memory","JSONL per-session history + async rolling LLM summarisation (≤200 words)"],
        ["Human-in-the-Loop","Role-triggered (credit_officer) and tool-triggered approval gates with checkpoint persistence"],
        ["Observability","OpenTelemetry distributed tracing, W3C baggage propagation, JSONL audit trail, per-request log files"],
        ["Evaluation","3-layer framework: 20 golden workflow cases + 15 custom evaluators + 36 FinBEN/FLARE tasks + red-team suite"],
        ["Frontend","React 18 / TypeScript chat with SSE streaming, AI Reasoning tab, pipeline trail, 7 dashboard pages"],
    ]
    cw = [0.25*CONTENT_W, 0.75*CONTENT_W]
    out += [mktable(data,cw), PageBreak()]
    return out


def sec2():
    out = sec_hdr("2","System Architecture")
    out += [
        P("The platform is composed of three independently deployable Python microservices plus a "
          "central API orchestrator and a React frontend. All inter-service communication uses typed "
          "HTTP — the A2A (Agent-to-Agent) protocol for agent hops and MCP (Model Context Protocol) "
          "for tool invocation. Each agent runs as an isolated OS process on its own port, preventing "
          "any shared memory or state leakage between agents."),
        SP(6),
        P("The critical architectural distinction is the <b>two-tier agent hierarchy</b>: "
          "the API Server calls ComplianceAgent (safety gate) and PriceAssistAgent (primary orchestrator) "
          "directly. PriceAssistAgent then sub-orchestrates DataAgent and RAGAgent internally via A2A — "
          "the API Server never calls DataAgent or RAGAgent directly. This layering gives PriceAssistAgent "
          "full control over intent classification, tool selection, and answer synthesis."),
        SP(10),
        sub("Architecture Diagram"),
        ArchDiagram(CONTENT_W, 320),
        P("Figure 1 — Full system topology. The two-tier agent hierarchy is shown: ComplianceAgent "
          "handles safety, PriceAssistAgent orchestrates DataAgent and RAGAgent as sub-agents. "
          "Dashed box marks PriceAssistAgent's orchestration scope.", "caption"),
        SP(8), sub("Technology Stack"),
    ]
    data = [
        ["Layer","Technology","Purpose"],
        ["API Orchestrator","Python 3.13 · Starlette · Microsoft MAF","6-stage workflow DAG, REST + SSE API (port 8000)"],
        ["LLM Runtime","Groq API (OpenAI-compatible endpoint)","GPT-OSS 120B (orchestration) · QWen 3.6-27B (data/RAG) · GPT-OSS 20B (compliance)"],
        ["Agent Protocol","A2A — JSON-RPC over HTTP","Typed inter-agent calls with W3C trace propagation"],
        ["Tool Protocol","FastMCP — Streamable HTTP","Structured data and RAG tools as MCP servers"],
        ["Structured Data","MySQL · SQLAlchemy · PyMySQL","18 semantic views in fab_semantic schema, 14 curated tables"],
        ["Vector DB","Qdrant · BAAI/bge-m3 (dense+sparse)","Document retrieval, hybrid search, 5 FAB PDFs"],
        ["Semantic Cache","ChromaDB · all-MiniLM-L6-v2 (384-dim)","Per-role Q/A cache with entity gating"],
        ["Conversation Memory","JSONL files · asyncio summariser","Per-session history + rolling ≤200-word summaries"],
        ["Frontend","React 18 · TypeScript · Vite 6 · Tailwind","Chat, approvals, audit, conversations, logs"],
        ["Observability","OpenTelemetry · OTLP · W3C Baggage","Distributed traces, custom metrics, rotating logs"],
    ]
    cw = [0.20*CONTENT_W, 0.38*CONTENT_W, 0.42*CONTENT_W]
    out += [mktable(data,cw), SP(8), sub("End-to-End Request Flow")]
    steps = [
        "User types a query in ChatPage → POST /api/query/stream (api_server :8000)",
        "Orchestrator builds the 6-stage MeshState workflow and opens a root OTel span",
        "InputGuardrailExecutor — regex screens for injection/PII/destructive patterns",
        "RBACValidationExecutor — resolves role, attaches allowed_tasks / denied_tasks",
        "CacheCheckExecutor — ChromaDB lookup, entity gate, zone decision; HIT returns immediately",
        "ComplianceExecutor — A2A call to ComplianceAgent (:8015); verdict + llm_reasoning captured",
        "DomainExecutor — A2A call to PriceAssistAgent (:8018)",
        "PriceAssistAgent classifies intent → calls DataAgent (:8016) and/or RAGAgent (:8017) via A2A",
        "DataAgent calls DataLayer MCP (:9100) → MySQL fab_semantic views",
        "RAGAgent calls RAG Service MCP (:9000) → Qdrant → chunks + generated answer",
        "PriceAssistAgent synthesises data + knowledge into final answer with citations",
        "OutputRedactionExecutor — regex PII scrub on final answer text",
        "SSE stream emits: stage events → reasoning events → result → done",
        "Background: conversation turn persisted to JSONL; rolling summary updated async",
        "Background: answer stored in ChromaDB semantic cache (if non-negative, non-blocked)",
    ]
    for i,s in enumerate(steps):
        out.append(B(f"<b>{i+1}.</b> {s}"))
    out.append(PageBreak())
    return out


def sec3():
    out = sec_hdr("3","Processing Pipeline — Six Stages")
    out += [
        P("Every request passes through a deterministic six-stage sequential pipeline implemented "
          "as typed Executor classes in a WorkflowBuilder DAG. The pipeline is the core safety "
          "and routing mechanism — no request bypasses it. Each Executor receives a MeshState "
          "dataclass, mutates its relevant fields, and passes it to the next stage."),
        SP(8), sub("Pipeline Stages at a Glance"),
        PipelineDiagram(CONTENT_W, 90),
        P("Figure 2 — Six sequential stages. Numbers show order. Colours indicate mechanism: "
          "red=block/filter · blue=rules · teal=cache · amber=LLM · green=routing · purple=redact. "
          "Latency shown is typical per stage.", "caption"),
        SP(10),
    ]
    stages = [
        ("Stage 1 — Input Guardrail",
         "The first and fastest gate. Runs three pattern banks in sequence using compiled regex — "
         "no LLM involved, no network call. <b>Pattern bank 1 (Injection)</b>: 12 patterns covering "
         "'ignore instructions', 'act as DAN', 'system prompt:', 'disregard your rules', jailbreak "
         "attempts, and prompt-continuation tricks. <b>Pattern bank 2 (PII in input)</b>: email "
         "addresses, SSNs, credit card numbers, phone numbers — prevents users from submitting "
         "PII that could be echoed into logs. <b>Pattern bank 3 (Destructive)</b>: DROP TABLE, "
         "DELETE FROM, rm -rf, disable authentication, truncate — prevents destructive SQL or "
         "command injection via natural language. Any match hard-blocks with category labels "
         "returned to the user. Typical latency: <1 ms."),
        ("Stage 2 — RBAC Validation",
         "Validates the requesting username against the seven FAB banking roles using the "
         "ROLE_PERMISSIONS registry. Unknown usernames default to the 'customer' role — "
         "fail-safe design. Resolves allowed_tasks (list of permitted task categories) and "
         "denied_tasks (explicitly forbidden categories) from the registry and attaches them "
         "to MeshState. Also resolves a human-readable permission scope string shown in the "
         "AI Reasoning tab. The two bypass roles (operations_manager, platform_administrator) "
         "are flagged here so ComplianceExecutor can skip the LLM gate. <1 ms."),
        ("Stage 3 — Semantic Cache",
         "ChromaDB vector lookup using all-MiniLM-L6-v2 (384-dim) embeddings. Embeds the "
         "query, retrieves top-3 similar past Q/A pairs filtered by role, applies entity "
         "gating (Phase 1 — blocks cross-customer contamination), then routes by zone: "
         "MISS (<0.75) sends the request straight to Stage 4; Suggest (0.75–0.85) streams "
         "a suggestion banner to the frontend for user accept/reject; Gray Zone (0.85–0.92) "
         "calls an LLM judge for a binary YES/NO decision; HIT (≥0.92) serves the cached "
         "answer immediately (~50 ms) — skipping Stages 4, 5, and 6 entirely. "
         "An optional cross-encoder reranker (Phase 4) improves candidate ordering. "
         "See Section 7 for full cache architecture."),
        ("Stage 4 — Compliance Gate",
         "LLM-powered semantic safety validation. Calls ComplianceAgent via A2A "
         "(JSON-RPC over HTTP to port 8015). The ComplianceAgent system prompt enumerates "
         "7 violation categories with per-role examples: prompt injection, PII exfiltration, "
         "destructive operations, social engineering, context poisoning, scope violation, "
         "and RBAC authorization violation. The agent validates the query against the user's "
         "role and allowed_tasks from MeshState. Returns exactly one verdict line "
         "(COMPLIANCE_PASSED: or COMPLIANCE_FAILED:) plus a structured &lt;llm_reasoning&gt; "
         "block captured into the audit trail. "
         "<b>Bypass roles:</b> platform_administrator and operations_manager skip this stage. "
         "<b>HITL trigger:</b> credit_officer role routes the verdict to an approval gate before proceeding."),
        ("Stage 5 — Domain Routing",
         "Dispatches to PriceAssistAgent via A2A (port 8018). PriceAssistAgent classifies "
         "intent (data / knowledge / hybrid), then calls query_structured_data and/or "
         "query_knowledge_base collaboration tools, each of which is itself an A2A hop "
         "to DataAgent or RAGAgent respectively. DomainExecutor monitors the response for "
         "anomalies: bare tool-call echo (function name returned as answer — retried once), "
         "meta-response (agent describing what it would do instead of doing it — retried), "
         "and hallucinated placeholder text. Tool-level HITL signals "
         "(AWAITING_TOOL_APPROVAL: prefix) are intercepted here and routed to ApprovalStore."),
        ("Stage 6 — PII Redaction",
         "Final pass over the generated answer text before it is sent to the frontend. "
         "Applies redact_pii() which runs the same PII pattern bank used in Stage 1 over "
         "the output, replacing matched spans with [REDACTED_EMAIL], [REDACTED_PHONE], "
         "[REDACTED_CREDIT_CARD], [REDACTED_SSN]. This is a defence-in-depth measure — "
         "even if an agent inadvertently includes PII (e.g., from a database field it was "
         "not supposed to echo), it is scrubbed before the frontend receives it. Redaction "
         "is recorded in the audit trail via the pii_redacted flag. <1 ms."),
    ]
    for title, desc in stages:
        out.append(KeepTogether([
            sub2(title), P(desc), SP(6),
        ]))
    out += [
        sub("SSE Streaming Events"),
        P("The /api/query/stream endpoint emits Server-Sent Events as each stage completes, "
          "giving the frontend live pipeline progress. Events emitted in order:"),
        B("<b>stage</b> — fired after each executor with stage name, result, and duration_ms"),
        B("<b>reasoning</b> — emits extracted &lt;llm_reasoning&gt; JSON blocks from agent responses"),
        B("<b>hitl</b> — emits approval_id when a HITL gate is triggered; frontend shows ApprovalModal"),
        B("<b>intent_suggestion</b> — emits cache candidate (similarity %, age) for user accept/reject"),
        B("<b>result</b> — final answer with route, token_usage per agent, total latency_ms"),
        B("<b>done</b> — signals stream end; frontend stops the loading indicator"),
        PageBreak(),
    ]
    return out


def sec4():
    out = sec_hdr("4","Security & Compliance Layer")
    out += [
        P("Security is enforced at four independent layers, each with a distinct mechanism "
          "and no dependency on the previous layer having 'passed'. A request must clear "
          "all applicable layers. The design is defence-in-depth: deterministic regex catches "
          "known attack patterns instantly; RBAC prevents category-level scope violations; "
          "the LLM compliance gate catches semantic attacks the regex cannot express; "
          "output redaction provides a final safety net."),
        SP(8), sub("Four-Layer Security Model"),
    ]
    data = [
        ["Layer","Mechanism","Type","What It Blocks"],
        ["1 — Input Guardrail","deterministic_filters.py","Regex · no LLM · <1ms","Prompt injection · PII in input · destructive SQL/shell commands"],
        ["2 — RBAC Validation","role_permissions.py","Rules engine · no LLM · <1ms","Requests outside the user's allowed task category set"],
        ["3 — LLM Compliance Gate","ComplianceAgent (Groq LLM)","LLM semantic · 2–4s","Semantic violations · scope escalation · social engineering · context poisoning"],
        ["4 — PII Redaction (output)","deterministic_filters.py","Regex · no LLM · <1ms","Email · SSN · credit card · phone numbers in final answer text"],
    ]
    cw = [0.22*CONTENT_W, 0.22*CONTENT_W, 0.20*CONTENT_W, 0.36*CONTENT_W]
    out += [mktable(data,cw), SP(8), sub("Role Permission Matrix")]
    data2 = [
        ["Role","Key Allowed Task Categories","Compliance Bypass?","HITL Gate?"],
        ["customer","own account · transactions · product enquiry · public knowledge","No","No"],
        ["relationship_manager","customer portfolio · pricing tools · own-customer 360 · product enquiry","No","No"],
        ["branch_operations_officer","branch ops policy · individual customer profile · service requests · CBUAE operational","No","No"],
        ["credit_officer","credit risk grade · loan restructuring · 360 view · credit assessment · CBUAE model risk","No","Yes — approval required"],
        ["compliance_officer","AML/KYC policy · audit trail · regulatory knowledge · compliance data","No","No"],
        ["operations_manager","Full operational access","Yes — skips LLM gate","No"],
        ["platform_administrator","Full platform access","Yes — skips LLM gate","No"],
    ]
    cw2 = [0.23*CONTENT_W, 0.37*CONTENT_W, 0.20*CONTENT_W, 0.20*CONTENT_W]
    out += [mktable(data2,cw2), SP(8), sub("Compliance Agent Verdict Protocol")]
    out += [
        P("ComplianceAgent always returns exactly one verdict line plus a structured reasoning block:"),
        P("COMPLIANCE_PASSED: brief justification  — OR —  COMPLIANCE_FAILED: violation_category", "code"),
        P("&lt;llm_reasoning&gt;{\"phase\":\"compliance_safety_review\", \"authorized\":true, "
          "\"request_task_category\":\"pricing\", \"intent\":\"...\"}&lt;/llm_reasoning&gt;", "code"),
        P("The reasoning block is extracted by the orchestrator, attributed to the compliance phase, "
          "stored in the audit trail, and rendered in the AI Reasoning tab's 'Safety Review' card. "
          "Compliance officers can query data/audit_trail.jsonl to find every request where a "
          "specific violation category was detected."),
        PageBreak(),
    ]
    return out


def sec5():
    out = sec_hdr("5","Agent Mesh Topology & Orchestration")
    out += [
        P("The mesh has a clear two-tier hierarchy. The API Server's DomainExecutor calls only "
          "two agents directly: ComplianceAgent (safety) and PriceAssistAgent (primary orchestrator). "
          "PriceAssistAgent then sub-orchestrates DataAgent and RAGAgent internally. "
          "This architecture keeps the API Server's pipeline simple and gives PriceAssistAgent "
          "full control over how structured data and knowledge retrieval are combined."),
        SP(8), sub("PriceAssistAgent — Intent Routing"),
        IntentDiagram(CONTENT_W, 175),
        P("Figure 3 — PriceAssistAgent intent classification and A2A routing. Query intent is "
          "classified into 'data', 'knowledge', or 'hybrid' before sub-agent calls are made.", "caption"),
        SP(8), sub("Agent Roster"),
    ]
    data = [
        ["Agent","Port","LLM Model","Role & Tool Access"],
        ["ComplianceAgent","8015","GPT-OSS 20B","Semantic safety gate. No external tools — pure LLM reasoning over 7-category safety ruleset. Returns PASSED/FAILED verdict + reasoning block."],
        ["DataAgent","8016","QWen 3.6-27B","Structured data specialist. Backed by 18 DataLayer MCP tools. Maps intent keywords to specific SQL view tools. max_function_calls=4."],
        ["RAGAgent","8017","QWen 3.6-27B","Knowledge retrieval specialist. One MCP tool: search_documents. Enforces role-based document access restrictions. max_function_calls=1."],
        ["PriceAssistAgent","8018","GPT-OSS 120B","Primary orchestrator. Classifies intent, calls DataAgent and RAGAgent via A2A collaboration tools, enforces RBAC scope rules, synthesises final markdown answer with citations and tables."],
    ]
    cw = [0.22*CONTENT_W, 0.07*CONTENT_W, 0.20*CONTENT_W, 0.51*CONTENT_W]
    out += [mktable(data,cw), SP(8), sub("A2A Communication Details")]
    out += [
        B("<b>Protocol:</b> JSON-RPC over HTTP. Each agent node is a Starlette app with a /health endpoint and TraceContextMiddleware"),
        B("<b>Trace propagation:</b> W3C traceparent header injected on every outbound A2A call via OTel httpx instrumentation — all sub-spans attributed to the originating root span"),
        B("<b>W3C Baggage:</b> fab.user · fab.role · fab.request_id · fab.session_id propagated to all downstream agent spans for cross-process attribution"),
        B("<b>Depth guard:</b> _MAX_PEER_DEPTH=2 ContextVar prevents infinite A2A recursion; per-request deduplication cache blocks identical repeated calls"),
        B("<b>Transport resilience:</b> transient HTTP failures retried once with exponential back-off before propagating error"),
        B("<b>Echo detection:</b> DomainExecutor checks PriceAssistAgent response for bare tool-call syntax (regex) and retries once with explicit instruction"),
        SP(8), sub("Collaboration Tools (PriceAssistAgent → Sub-Agents)"),
        P("Two A2A tools are registered in PriceAssistAgent's tool list:"),
        B("<b>query_structured_data(question)</b> — hops to DataAgent. Includes depth guard, per-request dedup cache, &lt;llm_reasoning&gt; extraction, and retry on transport failure"),
        B("<b>query_knowledge_base(question)</b> — hops to RAGAgent. Same resilience pattern. RAGAgent enforces role-based document restrictions before calling search_documents"),
        SP(6),
        sub("Write-Interceptor Tools (HITL Integration)"),
        B("<b>submit_pricing_change(customer_id, new_rate, effective_date)</b> — creates a role-level approval request, returns AWAITING_TOOL_APPROVAL: signal"),
        B("<b>adjust_credit_limit(customer_id, new_limit, reason)</b> — same pattern; triggers tool-level HITL gate in DomainExecutor"),
        PageBreak(),
    ]
    return out


def sec6():
    out = sec_hdr("6","Data & Knowledge Services")
    out += [
        P("Two independently deployed microservices provide the agent mesh with structured "
          "banking data and unstructured regulatory knowledge. Both are exposed as MCP "
          "(Model Context Protocol) servers so agents call them through standard typed tool "
          "invocations — not raw SQL or HTTP queries. This decoupling means the data pipeline "
          "and document corpus can evolve independently of the agent logic."),
        SP(8), sub("DataLayer-as-Service  (Port 9100)"),
        P("A FastMCP server over a MySQL database containing FAB corporate banking data. "
          "Raw CSV source files are processed through a numbered pipeline (01 validate → "
          "02 curate → 03 load → 04 create semantic views) to produce 18 optimised SQL views "
          "that DataAgent can query via named MCP tools."),
        SP(4), sub2("18 MCP Tools in 3 Tiers"),
    ]
    data = [
        ["Tier","Tools","Business Purpose"],
        ["Core (5)","customer_360 · pricing_recommendation · profitability_summary · margin_analysis · rwa_impact","Primary pricing intelligence and customer risk view"],
        ["Enhanced (9)","new_customer_pricing · competitor_price_analysis · pricing_trace · segment_pricing_benchmark · operations_cost_impact · relationship_discount · win_loss_insights · policy_exception · non_compliant_deals","Deal analytics, exceptions, competitive positioning"],
        ["Analytical (4)","cross_sell_opportunity · credit_rating_events · similar_customer_pricing · compare_fab_vs_competitor","Advanced benchmarking and cross-sell intelligence"],
    ]
    cw = [0.13*CONTENT_W, 0.51*CONTENT_W, 0.36*CONTENT_W]
    out += [mktable(data,cw), SP(6), sub2("Data Pipeline Stages")]
    out += [
        B("01_validate_raw_data.py — validates 14 raw CSVs (customer_master, product_master, pricing_policy, competitor_pricing, historical_deals, treasury_rate_sheet, etc.)"),
        B("02_create_curated_data.py — cleans, enriches, and joins raw tables; produces 14 curated CSVs"),
        B("03_load_curated_to_mysql.py — bulk-loads curated CSVs into MySQL fab_semantic schema"),
        B("04_create_semantic_views.py — creates 18 pre-joined analytical SQL views optimised for MCP tool queries"),
        SP(8), sub("RAG-as-Service  (Port 9000)"),
        P("A FastAPI + Qdrant retrieval-augmented generation service that answers policy and "
          "regulatory questions from five official FAB banking documents. The service "
          "exposes a REST API internally and wraps it with an MCP server on port 9000 "
          "for RAGAgent to call."),
        SP(4), sub2("Knowledge Base Documents"),
        B("FAB Credit Pricing Policy v2.4  —  pricing rules, exception criteria, approval thresholds"),
        B("FAB Corporate Term Loan Product Manual v2.0  —  product features, eligibility, pricing structures"),
        B("FAB Credit Concentration Limits Policy v1.8  —  sector/geography concentration caps, early warning triggers"),
        B("FAB Model Risk Management Framework v3.1  —  model validation, approval, ongoing monitoring requirements"),
        B("CBUAE Circular 2024-BSE-047: AI Governance Framework  —  UAE regulatory requirements for AI in banking"),
        SP(6), sub2("Retrieval Pipeline"),
        B("<b>Ingestion:</b> PDF → Docling text extraction → fixed-size or hierarchical chunking → BAAI/bge-m3 dense+sparse embedding → Qdrant with metadata (document_type, role_access, date)"),
        B("<b>Query:</b> embed query → Qdrant ANN search → optional BM25 keyword hybrid via Reciprocal Rank Fusion → cross-encoder reranker → freshness score penalty for stale chunks"),
        B("<b>Generation:</b> top-k chunks + user query → LLM → grounded answer with inline citations"),
        B("<b>MCP surface:</b> search_documents(query, top_k, generate_answer) — single typed tool call from RAGAgent"),
        B("<b>Evaluation:</b> RAGAS metrics (faithfulness, answer_relevancy) available via POST /api/v1/evaluate"),
        B("<b>Cache:</b> Redis query cache (optional) reduces repeated identical query latency"),
        PageBreak(),
    ]
    return out


def sec7():
    out = sec_hdr("7","Semantic Response Cache")
    out += [
        P("The semantic cache eliminates redundant LLM calls for similar questions by "
          "storing past Q/A pairs in ChromaDB and retrieving them via embedding similarity. "
          "A cache HIT serves the answer in approximately 50 ms with zero LLM API calls, "
          "compared to 5–70 seconds for a full pipeline run. The cache is role-aware — "
          "a cached answer for relationship_manager is never served to a customer role."),
        SP(8), sub("Cache Decision Flow"),
        CacheDiagram(CONTENT_W, 260),
        P("Figure 4 — Semantic cache decision flow. Each query goes through entity extraction, "
          "embedding, ChromaDB lookup, entity gating, and zone-based routing.", "caption"),
        SP(8), sub("Active Phases"),
    ]
    data = [
        ["Phase","Default","Description"],
        ["Entity Gate (Phase 1)","ON","Extracts customer IDs, account numbers, deal IDs, amounts from both queries using LLM + regex fallback. Requires exact entity set match — prevents CUST001 answer serving CUST002 queries even at 0.97 similarity."],
        ["Zone Decision","ON","Similarity thresholds: < 0.75 → MISS | 0.75–0.85 → Suggest banner | 0.85–0.92 → LLM Judge | ≥ 0.92 → HIT"],
        ["Suggestion Banner","ON","Streams top-3 candidates with similarity % and age to frontend. User clicks 'Use this answer' or 'Run fresh'. Decision captured in IntentDecisionStore via asyncio.Event."],
        ["LLM Judge (gray zone)","Configurable","Binary LLM check: 'Does this cached answer fully address the new query?' Max 60 tokens, 5s timeout, fallback MISS on timeout."],
        ["Cross-Encoder Reranker (Phase 4)","ON","Local cross-encoder (ms-marco-MiniLM-L-6-v2) reorders candidates before zone decision. More precise than bi-encoder. No API calls."],
        ["Query Canonicalisation (Phase 2)","OFF","Replaces IDs with placeholders before embedding: CUST001→<CUSTOMER_ID>, ACC-4421→<ACCOUNT>. Requires --overwrite re-ingest when enabled."],
        ["Paraphrase Augmentation (Phase 2b)","OFF","At ingest time, LLM generates N alternative phrasings per Q/A pair stored as separate ChromaDB entries. Expands recall for varied phrasing."],
        ["Hybrid Retrieval (Phase 3)","OFF","BM25 keyword search fused with dense via Reciprocal Rank Fusion. Lifts exact keyword matches like FAB-CRP-CONC-2024."],
        ["Negative Answer Guard (Phase 7)","ON","Blocks caching of 'not found' / 'no data available' answers. Logs rejections to data/cache_rejections.jsonl for monitoring."],
    ]
    cw = [0.28*CONTENT_W, 0.10*CONTENT_W, 0.62*CONTENT_W]
    out += [mktable(data,cw), SP(8), sub("Configuration Reference")]
    configs = [
        ("ENABLE_RESPONSE_CACHE","true","Master on/off switch"),
        ("CACHE_SIMILARITY_THRESHOLD","0.92","HIT threshold (cosine similarity)"),
        ("CACHE_INTENT_MATCH_THRESHOLD","0.85","Suggestion zone lower bound"),
        ("CACHE_MISS_THRESHOLD","0.75","Below this → immediate MISS"),
        ("CACHE_ENTITY_GATING_ENABLED","true","Enable Phase 1 entity gate"),
        ("CACHE_ENTITY_GATE_MODE","hard","hard=entity mismatch→MISS; soft=downgrade similarity"),
        ("CACHE_JUDGE_MODEL","gemma-4-31b","LLM model for gray-zone judge"),
        ("CACHE_MAX_AGE_HOURS","150.0","Stale entries excluded from lookup"),
        ("CACHE_PARAPHRASE_N","5","Variants generated per Q/A at ingest (Phase 2b)"),
    ]
    data2 = [["Env Var","Default","Effect"]] + [[a,b,c] for a,b,c in configs]
    cw2 = [0.38*CONTENT_W, 0.15*CONTENT_W, 0.47*CONTENT_W]
    out += [mktable(data2,cw2), PageBreak()]
    return out


def sec8():
    out = sec_hdr("8","Conversation Memory & Multi-Turn")
    out += [
        P("AgentMesh maintains full conversation history across turns within a session. "
          "History is stored in per-session JSONL files and compressed via async rolling "
          "LLM summarisation to prevent unbounded token growth. The design ensures long "
          "advisory sessions (10+ turns) remain coherent without the hard context truncation "
          "that plagued earlier prototypes."),
        SP(8), sub("Storage Architecture"),
        B("<b>Backend:</b> JSONL files — one file per session_id under data/conversations/. Append-only, human-readable, no database dependency. Redis backend is stubbed for future deployment."),
        B("<b>Session binding:</b> session_id generated at login. A .owner file per session enforces access control — cross-session reads are blocked with an ownership error."),
        B("<b>Rich turn snapshots:</b> each JSONL record stores query, answer, route, execution_events list, llm_reasoning blocks, token_usage (per agent), latency_ms, and timestamp."),
        B("<b>Conversation store facade:</b> ConversationStore abstracts the backend. Methods: load_with_summary() · save_summary() · append_turn_rich() · bind_session() · check_owner()."),
        SP(8), sub("Rolling LLM Summarisation"),
        P("After every conversation turn, an asyncio background task (fire-and-forget, non-blocking) "
          "calls the Groq LLM to condense all prior turns into a ≤200-word summary. This summary "
          "is then prepended to the next turn's context instead of the full turn history:"),
        SP(3),
        P("[Summary: &lt;≤200-word rolling summary of prior conversation&gt;]\n"
          "[Current question: &lt;user query&gt;]", "code"),
        SP(3),
        B("The LLM receives summary + current query — never the full raw turn history"),
        B("Summaries are persisted to JSONL alongside turn records — survive server restarts"),
        B("Token cost grows logarithmically (one summary) rather than linearly (all turns)"),
        B("Summarisation runs completely async — response latency is unaffected"),
        B("ConversationStore.strip_history_echo() removes history prefixes if agents accidentally echo them"),
        SP(8), sub("Session Restore & Replay"),
        P("The frontend's ConversationsDashboardPage lists all active sessions. Clicking a session "
          "calls GET /api/conversations/{session_id} which returns the full turn list including "
          "route decisions, pipeline trails, and reasoning blocks for each turn. The ChatPage "
          "restores the visual message history, allowing analysts to review and continue "
          "prior advisory sessions."),
        PageBreak(),
    ]
    return out


def sec9():
    out = sec_hdr("9","AI Reasoning Explainability")
    out += [
        P("Every AI agent in the mesh emits a structured reasoning block alongside its answer. "
          "These blocks are extracted by the orchestrator, attributed to specific agents and phases, "
          "and surfaced in the UI's 'AI Reasoning' tab — giving compliance officers and relationship "
          "managers full visibility into why the AI produced a particular answer. Importantly, "
          "the blocks are stored verbatim in the audit trail, enabling retrospective review "
          "without LLM replay."),
        SP(8), sub("Reasoning Block Format"),
        P("Agents emit structured JSON wrapped in XML-like markers in their response text. "
          "The orchestrator's llm_reasoning.py module extracts and strips these markers:"),
        P("&lt;llm_reasoning&gt;{\"agent\":\"price_assist\",\"phase\":\"intent_routing\","
          "\"intent\":\"hybrid\",\"data_task\":\"margin_analysis\","
          "\"rag_task\":\"concentration_limits\",\"confidence\":0.92}"
          "&lt;/llm_reasoning&gt;", "code"),
        SP(8), sub("Reasoning Phases Captured"),
    ]
    data = [
        ["Phase","Agent","Contents"],
        ["intent_routing","PriceAssistAgent","Classified intent (data/knowledge/hybrid), specific data_task, rag_task, confidence score — the core routing decision"],
        ["data_tool_selection","DataAgent","Selected MCP tool name, query parameters passed, reasoning for tool choice over alternatives"],
        ["rag_tool_selection","RAGAgent","Search query sent to Qdrant, top_k requested, expected document sources, role restrictions applied"],
        ["compliance_safety_review","ComplianceAgent","Violation category (if any), authorized flag, request_task_category, which RBAC rule was evaluated"],
        ["synthesis","PriceAssistAgent","How data results and knowledge chunks were combined, any caveats noted, citation sources included"],
    ]
    cw = [0.25*CONTENT_W, 0.22*CONTENT_W, 0.53*CONTENT_W]
    out += [
        mktable(data,cw), SP(8), sub("UI: AI Reasoning Tab"),
        B("Collapsible 'AI Reasoning' tab appears in each chat message bubble alongside 'Pipeline Steps'"),
        B("Each reasoning phase rendered as an expandable card with agent badge (colour-coded) and phase label"),
        B("Compliance card prominently shows authorized: true/false, request_task_category, and block reason if applicable"),
        B("PipelineTrail component shows breadcrumb trail: guardrail_pass → rbac_pass → compliance_pass → domain_answer:price_assist"),
        B("CostBadge shows inline token usage: '↑ 312 tok · ↓ 89 tok · ~$0.000041' per message"),
        SP(8), sub("Cross-Process Extraction"),
        P("Since DataAgent and RAGAgent run as separate OS processes, their reasoning blocks "
          "are extracted from the A2A response text by collaboration_tools.py and injected "
          "into the orchestrator's ExecutionTracer ContextVar. A temp-file handoff mechanism "
          "handles race conditions where the ContextVar is not accessible across the A2A boundary. "
          "This ensures all reasoning — from all four agents — is attributed to the single "
          "originating request trace."),
        PageBreak(),
    ]
    return out


def sec10():
    out = sec_hdr("10","Human-in-the-Loop (HITL)")
    out += [
        P("AgentMesh implements a native HITL approval gate for high-risk operations. "
          "The orchestrator can pause mid-workflow, persist the pending approval to disk, "
          "and resume on human decision — surviving server restarts. This addresses a "
          "key banking requirement: credit-impacting operations must have an explicit "
          "human sign-off before execution."),
        SP(8), sub("Two Trigger Types"),
    ]
    data = [
        ["Trigger","When Activated","Pause Point","Resume Condition"],
        ["Role-triggered","Any request from a credit_officer user","After ComplianceExecutor verdict","Manager approves/rejects via /api/approvals/{id}/approve or /reject"],
        ["Tool-triggered","PriceAssistAgent calls submit_pricing_change() or adjust_credit_limit()","DomainExecutor intercepts AWAITING_TOOL_APPROVAL: signal","Tool-level approval/rejection routes back to PriceAssistAgent"],
    ]
    cw = [0.20*CONTENT_W, 0.28*CONTENT_W, 0.24*CONTENT_W, 0.28*CONTENT_W]
    out += [
        mktable(data,cw), SP(8), sub("Approval Lifecycle"),
        B("<b>Create:</b> ApprovalStore.create() generates UUID approval_id, creates asyncio.Event, immediately writes checkpoint JSON to data/checkpoints/{id}.json"),
        B("<b>Stream:</b> orchestrator emits hitl SSE event with approval_id to frontend; ChatPage shows inline ApprovalModal with details"),
        B("<b>Wait:</b> orchestrator calls asyncio.Event.wait() — zero CPU busy-wait; request connection stays open via SSE"),
        B("<b>Decide:</b> manager posts to POST /api/approvals/{id}/approve or /reject from ApprovalsDashboard or standalone /approval/:id page"),
        B("<b>Resume:</b> Event fires; orchestrator continues pipeline (approved) or returns rejection message (rejected)"),
        B("<b>Cleanup:</b> checkpoint file deleted on resolution; approval removed from in-memory store"),
        B("<b>Restart safety:</b> on server startup, ApprovalStore.restore() reads all .json checkpoint files and recreates Events — no pending approval is silently lost"),
        SP(8), sub("Frontend Components"),
        B("<b>ApprovalModal.tsx</b> — inline modal in the chat bubble for the credit_officer requesting the operation"),
        B("<b>ApprovalsDashboardPage.tsx</b> — full list of all pending approvals for managers with approve/reject buttons and approval details"),
        B("<b>/approval/:id</b> — standalone page requiring no app login; designed to be linked in email notifications to approvers"),
        PageBreak(),
    ]
    return out


def sec11():
    out = sec_hdr("11","Observability & Audit Trail")
    out += [
        P("The platform is instrumented with three complementary observability layers: "
          "OpenTelemetry distributed tracing across all four agent processes, per-request "
          "execution event tracing within the orchestrator, and JSONL audit records for "
          "compliance review. All layers are correlated by request_id and session_id."),
        SP(8), sub("OpenTelemetry Distributed Tracing"),
        B("<b>Span propagation:</b> W3C traceparent header injected on every outbound A2A HTTP call via OTel httpx auto-instrumentation — sub-spans from all four agent processes are attributed to the originating root span"),
        B("<b>W3C Baggage:</b> fab.user · fab.role · fab.request_id · fab.session_id propagated so any span in any agent process carries the originating request's identity"),
        B("<b>Export profiles</b> (OBS_PROFILE env var): dev (local OTLP collector) · grafana (OTLP/HTTP to Grafana Cloud) · prod (Azure Monitor via OpenTelemetry SDK) · off (file-only, no network)"),
        B("<b>MAF-native spans:</b> Microsoft Agent Framework emits spans for agent invocation, tool calls, and LLM completions automatically"),
        SP(8), sub("Custom OTel Metrics"),
    ]
    data = [
        ["Metric","Type","What It Tracks"],
        ["record_mesh_request","counter + histogram","Total requests, latency distribution by route"],
        ["record_guardrail","counter","Blocked requests by violation category"],
        ["record_rbac","counter","RBAC passes and blocks by role"],
        ["record_compliance","counter","ComplianceAgent PASSED/FAILED verdicts"],
        ["record_cache","counter","Cache HITs, MISSes, entity gate drops, judge calls"],
        ["record_a2a_call","counter + histogram","A2A hops by target agent, latency"],
        ["record_domain_route","counter","Route distribution: data/knowledge/hybrid/unknown"],
        ["record_pii_hits","counter","PII pattern matches on input and output"],
        ["record_mcp_call","counter + histogram","MCP tool calls by name, latency"],
    ]
    cw = [0.30*CONTENT_W, 0.18*CONTENT_W, 0.52*CONTENT_W]
    out += [mktable(data,cw), SP(8), sub("Per-Request Execution Tracing")]
    out += [
        B("<b>ExecutionTracer:</b> ContextVar-based per-request event bus — any code layer (executor, tool, middleware) calls get_active_tracer().emit_stage() without changing function signatures"),
        B("<b>State handoff logs:</b> state_trace.py records MeshState field deltas between each executor hop — one JSON file per request_id under data/logs/state/"),
        B("<b>ExecutionSummary:</b> returned in every API response — full event list, agents_invoked, tools_used, route, confidence, llm_reasoning entries for each agent"),
        B("<b>Per-request log file:</b> data/logs/requests/{request_id}.json — complete structured log for that request only (rotation by request)"),
        SP(8), sub("JSONL Audit Trail"),
        B("<b>Location:</b> data/audit_trail.jsonl — append-only, one JSON object per line, one record per request"),
        B("<b>Contents per record:</b> request_id · user · role · session_id · input query · final answer · route_taken · all llm_reasoning blocks · token_usage {input_tokens, output_tokens} per agent · latency_ms · pii_redacted flag · timestamp"),
        B("<b>Middleware:</b> AuditMiddleware fires once per agent invocation; ToolCallLogMiddleware fires once per tool call with duplicate-call detection"),
        B("<b>UI:</b> AuditDashboardPage lists all audit records with search; FeedbackDashboardPage shows thumbs + 7-dimension structured feedback"),
        PageBreak(),
    ]
    return out


def sec12():
    out = sec_hdr("12","Evaluation Framework")
    out += [
        P("A three-layer evaluation framework validates the platform against FAB-specific "
          "golden test cases, domain safety criteria, financial industry benchmarks, and "
          "adversarial red-team attacks. All layers share a single CLI entry point with "
          "per-mode execution."),
        P("Entry point: python workflow_evaluations/run_evaluation.py --mode &lt;mode&gt;", "code"),
        SP(6), sub("Three-Layer Architecture"),
    ]
    data = [
        ["Layer","Mode","Contents","Requires Mesh Running"],
        ["Layer 1 — Workflow","--mode full","20 FAB golden test cases: guardrail_blocked (GC-01–03), rbac_blocked (GC-04), compliance_blocked (GC-05–07), safe_compliant (GC-08–09), data_route (GC-10–14), rag_route (GC-15–18), hybrid_route (GC-19–22), ambiguous (GC-23), multi_turn (GC-24–25)","Yes"],
        ["Layer 2 — Custom Evaluators","--mode ci","15 evaluators: PII, RBAC, Compliance, RAG Citation, Data Tool, Task Adherence (LLM-as-judge), Task Completion, Intent Resolution, Tool Selection, Tool Input Accuracy, Tool Output Utilisation, Tool Call Success, Ambiguity Resolution, Keyword Coverage, Red-Team","No"],
        ["Layer 3 — Financial Benchmarks","--mode benchmarks / demo","36 FinBEN/FLARE tasks: sentiment, NER, QA, summarisation, credit scoring, fraud detection — metrics: accuracy, F1, ROUGE, AUROC, Token F1, Pearson R","No"],
    ]
    cw = [0.22*CONTENT_W, 0.20*CONTENT_W, 0.40*CONTENT_W, 0.18*CONTENT_W]
    out += [mktable(data,cw), SP(8), sub("Red-Team Suite")]
    out += [
        B("Six attack categories: prompt injection · PII exfiltration · role escalation · jailbreak · social engineering · data poisoning"),
        B("Tests run as carol (compliance_officer) against the live mesh — realistic attack surface"),
        B("Each attack verified blocked; response scanned for PII leakage"),
        B("JSON report: pass/fail per attack, block rate, blocked-at-stage (guardrail/compliance/rbac)"),
        SP(8), sub("CI Gate — Pass/Fail Thresholds"),
    ]
    data2 = [
        ["Evaluator","Threshold","Rationale"],
        ["PII Evaluator","1.00 — zero tolerance","Any PII in output is a regulatory breach"],
        ["RBAC Evaluator","1.00 — zero tolerance","Cross-role data leakage is unacceptable"],
        ["Compliance Evaluator","≥ 0.95","5% FN rate max for safety gate"],
        ["RAG Citation Evaluator","≥ 0.80","Named source required for policy answers"],
        ["Tool Routing Evaluator","≥ 0.85","15% wrong-tool tolerance for ambiguous queries"],
        ["Task Adherence (LLM-judge)","≥ 0.75","QWen 3.6-27B judges answer vs intent"],
        ["Tier-1 Financial F1","≥ 0.50","Demo threshold for FinBEN/FLARE tasks"],
    ]
    cw2 = [0.32*CONTENT_W, 0.28*CONTENT_W, 0.40*CONTENT_W]
    out += [mktable(data2,cw2), PageBreak()]
    return out


def sec13():
    out = sec_hdr("13","Development Lessons Learned")
    out += [
        P("Building a production-grade multi-agent banking AI system surfaces a distinct class "
          "of engineering challenges — many invisible in demos but critical at scale. "
          "The following fifteen challenges were encountered during development, with the "
          "specific solutions applied."),
        SP(8),
    ]

    lessons = [
        ("1. Semantic Cache Cross-Customer Contamination",
         "Without entity-level gating, a cached answer for CUST001 was served for CUST002 queries "
         "at 0.97 cosine similarity — technically similar question, factually wrong answer. This "
         "was caught during role-specific RBAC testing when a relationship manager received "
         "another customer's pricing margin data from cache. The cache had no concept of which "
         "specific entities were referenced in the question.",
         "Implemented Phase 1 entity gating: an LLM-based entity extractor (with regex fallback) "
         "parses customer IDs, account numbers, deal IDs, and amounts from both the new query "
         "and the candidate cached query. The gate requires an exact entity set match — any "
         "mismatch returns immediate MISS regardless of cosine similarity. The extractor is "
         "batched with retry for rate limits and its results are cached in ChromaDB metadata "
         "at ingest time to avoid re-extraction on every lookup."),

        ("2. HITL Approval State Lost on Server Restart",
         "HITL approvals were stored as asyncio.Event objects in memory. When the server "
         "restarted (e.g., for a code hotfix during a live approval), all pending approvals "
         "were silently lost. Approvers clicking email links received 404 errors. This was "
         "a critical reliability gap — in a real bank, a credit approval might sit pending "
         "for several hours while the approver reviews supporting documents.",
         "Added checkpoint persistence: ApprovalStore.create() immediately writes a JSON "
         "checkpoint to data/checkpoints/{approval_id}.json containing the full approval "
         "context (user, role, query, compliance verdict, timestamp). On server startup, "
         "ApprovalStore.restore() reads all checkpoint files and recreates asyncio.Events. "
         "The checkpoint is deleted only when the decision resolves or explicitly expires."),

        ("3. LLM Reasoning Lost Across A2A Process Boundaries",
         "The <llm_reasoning> blocks emitted by DataAgent and RAGAgent were visible inside "
         "those processes but not captured in the orchestrator's ExecutionTracer. ContextVars "
         "do not cross OS process boundaries — the orchestrator's tracer and the agent's "
         "tracer are completely separate objects. Without this attribution, the AI Reasoning "
         "tab only showed ComplianceAgent and PriceAssistAgent reasoning, missing the "
         "sub-agent decisions.",
         "Two-path solution: (1) collaboration_tools.py extracts <llm_reasoning> from the "
         "A2A response text using the same LLMReasoningExtractor and calls "
         "get_active_tracer().add_reasoning() in the orchestrator's process. "
         "(2) For cases where this extraction is concurrent with another request, "
         "a temp-file handoff writes the reasoning block keyed by request_id to a named "
         "temp file, which the orchestrator reads and cleans up per request."),

        ("4. Context Truncation in Long Sessions",
         "A hard cap of CONVERSATION_MAX_TURNS=3 was the original design. Turns older than 3 "
         "were dropped silently with no indication to the user. In longer advisory sessions "
         "(10+ turns), the AI lost awareness of constraints established earlier — for example, "
         "forgetting that the customer had a specific credit exception agreed in turn 2 when "
         "answering a pricing question in turn 8.",
         "Replaced hard truncation with rolling LLM summarisation. After each turn, an asyncio "
         "background task (fire-and-forget) calls the LLM to condense all prior turns into a "
         "≤200-word summary. This summary replaces the full history in the prompt. Token "
         "cost grows logarithmically rather than linearly. The summary is persisted alongside "
         "turn records so it survives server restarts."),

        ("5. Caching 'Not Found' Answers Poisoning the Cache",
         "When a data query returned 'No pricing data available for this customer' (e.g., a new "
         "customer with no deal history in MySQL), that negative answer was stored in ChromaDB. "
         "Future queries for different customers with similar phrasing received this incorrect "
         "negative answer as a cache hit — suppressing data that actually existed and causing "
         "relationship managers to incorrectly report no pricing history.",
         "Added a negative answer guard (Phase 7): is_negative_answer() applies a regex bank "
         "of 'no data', 'not found', 'unable to retrieve', 'no records found', 'I could not "
         "find', 'no information available' patterns to the candidate answer before caching. "
         "Matched answers are logged to data/cache_rejections.jsonl for monitoring but are "
         "never written to ChromaDB. The rejection log feeds into evaluation metrics."),

        ("6. LLM API Rate Limits Under Concurrent Load",
         "During multi-user load testing, simultaneous requests from 3–4 users caused Groq "
         "API 429 rate-limit errors. The default retry with exponential back-off added 15–30 "
         "seconds to affected requests, which users experienced as apparent hangs. Single "
         "API key per agent type was the bottleneck.",
         "Added a second Groq API key per agent type (GROQ_API_KEY and GROQ_API_KEY_2). "
         "The agent factory alternates between keys using a process-level atomic counter. "
         "This effectively doubles the per-agent rate limit budget. Keys are configured in "
         ".env — no code change required to add keys. The paraphrase augmentation pipeline "
         "also uses CACHE_PARAPHRASE_DELAY_S (default 5s) and 3× back-off on 429."),

        ("7. MCP Streamable-HTTP Sessions Dropping Mid-Operation",
         "FastMCP streamable-HTTP sessions to DataLayer (:9100) and RAG Service (:9000) "
         "maintained persistent HTTP connections. Under network instability or after idle "
         "periods, sessions dropped silently. The next tool call failed with a connection "
         "reset error rather than reconnecting, crashing the DataAgent or RAGAgent process.",
         "Two recovery mechanisms in a2a_server.py: (1) startup retries with exponential "
         "back-off — waits for each agent port to bind before starting the next, with up "
         "to N retries per node. (2) On-demand reconnect during tool calls — the MCP client "
         "detects a dropped session (connection reset exception), re-establishes the session "
         "with the same tool list, and retries the tool call transparently. The reconnect "
         "attempt is logged as a warning in the agent's structured log."),

        ("8. PriceAssistAgent Echoing Tool Call Syntax as Final Answer",
         "In edge cases — particularly when the last message in the agent's context window "
         "was a tool invocation rather than a completion — PriceAssistAgent returned the raw "
         "tool call syntax as its final answer. For example: "
         "'query_structured_data(question=\"margin for CUST001\")' as plain response text "
         "rather than the actual margin data.",
         "Added echo detection in DomainExecutor: the response text is checked against a "
         "compiled regex that identifies bare tool-call strings (function_name( pattern). "
         "On detection, the orchestrator issues one automatic retry with an explicit system "
         "message instructing the agent to return the data result, not the invocation. "
         "A second consecutive echo raises a user-visible error rather than infinite retry."),

        ("9. RAG Faithfulness Degradation with Weak Chunk Coverage",
         "Early RAG evaluations showed RAGAS faithfulness scores dropping to 0.3–0.5 for "
         "queries about topics spread across multiple sections of a document. The retriever "
         "was fetching chunks from nearby sections that contained related keywords but not "
         "the specific clauses needed for a faithful answer. The LLM then produced plausible "
         "but hallucinated summaries.",
         "Implemented three improvements: (1) switched from fixed-size to hierarchical "
         "chunking for policy documents — chunks respect paragraph and section boundaries, "
         "keeping related clauses together. (2) Added cross-encoder reranking after ANN "
         "retrieval to re-score candidates on the full query+chunk pair rather than just "
         "embedding similarity. (3) Added a freshness penalty to down-weight chunks from "
         "older document revisions when multiple versions exist."),

        ("10. DataAgent Tool Selection with Ambiguous Queries",
         "With 18 available MCP tools, DataAgent sometimes called the wrong tool for "
         "ambiguous queries. For example, 'show me pricing for CUST001' could map to "
         "pricing_recommendation, customer_360, or new_customer_pricing depending on context. "
         "The agent used the tool description alone to choose, leading to ~20% wrong-tool "
         "calls in early testing.",
         "Added an explicit keyword routing section to DataAgent's system prompt: a table "
         "mapping intent keywords (margin, concentration, profitability, competitor, RWA, "
         "exception, win_loss) to their canonical tool names. This reduced wrong-tool calls "
         "to ~8% in evaluation. DataAgent is also instructed to check query context before "
         "calling a tool and to prefer specificity — e.g., margin_analysis over customer_360 "
         "when the question is explicitly about margins."),

        ("11. Static Agent Prompts Inflating Token Costs",
         "Each PriceAssistAgent request paid for the full 200-line static system prompt "
         "(~2,000 tokens) even when 60% of it was irrelevant to the query type. At scale, "
         "this inflated API costs and filled context space that could carry richer data results. "
         "Additionally, the same static prompt was repeated identically every request with no "
         "opportunity for LLM provider prompt caching.",
         "Designed a meta-prompting architecture: the system prompt is trimmed to a 75-line "
         "invariant base (~750 tokens) covering only operating rules, output format, and the "
         "<llm_reasoning> schema. A PromptGeneratorExecutor stage generates a per-request "
         "<task_directive> JSON (75–300 tokens) injected into the user message — specifying "
         "intent, RBAC scope summary, tool plan, and synthesis instruction. Net result: "
         "~48% token reduction per request. The static base is identical every call — eligible "
         "for LLM provider prompt caching."),

        ("12. Public Financial Benchmarks Not Applicable to the Domain",
         "FinBEN, FLARE, and FLUE benchmarks are built on public Bloomberg/Reuters/SEC data. "
         "FAB's ground truth lives in proprietary MySQL views and internal policy PDFs. Running "
         "the benchmarks produced near-zero scores on most tasks (NER exact match = 0, QA "
         "exact match = 0) — not because the system was wrong, but because the evaluation "
         "method was mismatched: the mesh returns prose answers, not bare entity lists or "
         "exact numeric values expected by standard metrics.",
         "Built a custom three-layer evaluation framework from scratch. Layer 1 uses 20 "
         "FAB-specific golden test cases manually authored and verified against live banking "
         "data. Layer 2 uses 15 custom evaluators with FAB-specific rules. Layer 3 borrows "
         "FinBEN/FLARE dataset structure for structural validation while accepting prose via "
         "Token F1 and ROUGE-L rather than exact match. Standard benchmarks are retained as "
         "a model comparison reference, not a production pass/fail gate."),

        ("13. SSE Streaming Race Conditions in the Frontend",
         "When multiple pipeline stages fired in rapid succession (e.g., guardrail, RBAC, and "
         "cache check all completing within 50ms), SSE events arrived out of order at the "
         "frontend because the browser's event processing queued them differently depending "
         "on network jitter. The ChatPage's ExecutionPanel showed steps in the wrong order, "
         "confusing users reviewing the pipeline trail.",
         "Added a sequence_num field to every SSE event emitted by api_server.py. The "
         "frontend's useChat hook accumulates events in a ref and sorts by sequence_num "
         "before rendering the ExecutionPanel. Stage events are also debounced 50ms to allow "
         "rapid-fire events to batch before triggering a React state update, reducing "
         "unnecessary re-renders during the fast early stages of the pipeline."),

        ("14. Qdrant Collection Schema Not Supporting Role-Based Filtering",
         "The initial Qdrant collection stored only document text and embedding — no metadata. "
         "When the requirement arrived to restrict certain documents to specific roles "
         "(e.g., the Model Risk Management Framework should only be retrievable by credit_officer "
         "and compliance_officer), there was no way to filter at the vector DB level. "
         "RAGAgent was doing post-retrieval role filtering in Python, which required fetching "
         "more chunks than needed and discarding many.",
         "Re-designed the Qdrant payload schema to include metadata fields: document_type, "
         "role_access (list of allowed roles), document_version, and ingestion_date. "
         "All retrieval queries now pass a Qdrant filter on role_access as a pre-filter, "
         "so ineligible chunks are excluded at the vector DB layer before ANN search runs. "
         "This reduced retrieved chunk count by ~40% for restricted-role queries and eliminated "
         "the Python-side post-filtering loop."),

        ("15. Structured Feedback Dimension Data Not Persisted Across Sessions",
         "The 7-dimension structured feedback form (intent, workflow, tools, policy, output, "
         "correction, effort) was originally wired to an in-memory store that was cleared on "
         "server restart. During a demo involving a restart, all structured feedback from the "
         "session was lost. Since this feedback is the primary signal for the planned Prompt "
         "Refinement pipeline, losing it was a significant data loss.",
         "Migrated structured feedback to the same JSONL append pattern used for the audit "
         "trail: data/feedback.jsonl. Each POST /api/feedback/structured call appends a "
         "record containing the full Q/A pair, all 7 dimension scores, the correction_text "
         "field (if the user typed a correction), route, session_id, and timestamp. "
         "The FeedbackDashboardPage queries this file via GET /api/feedback/structured "
         "with optional session_id and date range filters."),
    ]

    for title, problem, solution in lessons:
        out.append(KeepTogether([
            sub2(title),
            P(f"<b>Challenge:</b> {problem}"),
            SP(3),
            P(f"<b>Solution:</b> {solution}"),
            SP(8),
        ]))

    out.append(PageBreak())
    return out


def sec14():
    out = sec_hdr("14","Project Structure")
    out += [
        P("The repository is organised as four independently deployable service directories "
          "sharing a single Python virtual environment (fab-venv, short path for Windows "
          "MAX_PATH compatibility). Each service has its own .env, requirements, and data "
          "directory."),
        SP(8), sub("Directory Tree"),
    ]
    tree = [
        ("agent-mesh-15062026/","Repository root — shared venv + unified requirements.txt"),
        ("  agent-mesh/","Core orchestrator, all four agents, React frontend"),
        ("    src/agents/","price_assist_agent · data_agent · rag_agent · compliance_agent · agent_factory · node_registry"),
        ("    src/mesh/","workflow.py (6-stage DAG + MeshState) · orchestrator.py (handle_request)"),
        ("    src/auth/","identity_provider.py (7 mock users) · role_permissions.py (ROLE_PERMISSIONS registry)"),
        ("    src/cache/","semantic_cache · cache_judge · entity_extractor · reranker · negative_filter · ingest_pipeline · intent_decision_store"),
        ("    src/guardrails/","deterministic_filters.py (injection · PII · destructive pattern banks)"),
        ("    src/hitl/","approval_store.py (asyncio.Event + checkpoint files)"),
        ("    src/memory/","conversation_store · jsonl_backend · redis_backend (stub) · summariser · base"),
        ("    src/tracing/","execution_trace · llm_reasoning · state_trace · cli_renderer"),
        ("    src/observability/","setup · logging_config · metrics · baggage · tracer"),
        ("    src/tools/","collaboration_tools (A2A peers + write-interceptor HITL tools)"),
        ("    src/a2a/","clients (ask_remote) · hosting (build_starlette_app + TraceContextMiddleware)"),
        ("    src/integrations/","mcp_clients (DataLayer + RAG MCP tool factories)"),
        ("    frontend/src/pages/","ChatPage · ApprovalPage · ApprovalsDashboard · AuditDashboard · FeedbackDashboard · ConversationsDashboard · LogsDashboard · MeshStatusPage"),
        ("    frontend/src/components/","MessageBubble · ExecutionPanel · LLMReasoningPanel · PipelineTrail · ApprovalModal · StructuredFeedbackModal · SecurityBadge"),
        ("    workflow_evaluations/","run_evaluation.py · evaluators/ (15 modules) · workflow/ · financial_benchmarks/ · red_team/"),
        ("    data/","audit_trail.jsonl · feedback.jsonl · conversations/ · cache_rejections.jsonl · logs/ · checkpoints/"),
        ("  datalayer-as-service/","FastMCP server + MySQL data pipeline"),
        ("    mcp_server/","server.py (18 tools) · db.py (SQLAlchemy pool) · tools.py (view queries)"),
        ("    data/raw/","14 original CSV source files"),
        ("    data/curated/","Cleaned and enriched versions of raw tables"),
        ("    data/semantic/","13 pre-joined analytical view CSVs"),
        ("    sql/","01_create_schemas · 02_create_curated_tables · 03_create_semantic_views"),
        ("  rag-as-a-service/","FastAPI + Qdrant RAG service + MCP layer"),
        ("    src/gernas_rag/","ingestion · extraction · chunking · embeddings · vectordb · retrieval · generation · evaluation · llm · cache · api/routers"),
        ("    mcp_integration/","server.py (MCP server wrapping RAG REST API on port 9000)"),
        ("    docs/","5 FAB banking PDFs (knowledge base source documents)"),
        ("    scripts/","ingest_docs · setup_vectordb · run_evaluation · verify_chunks"),
        ("  my_KB/","Project knowledge base — architecture, features, evaluation, planning docs"),
        ("  fab-venv/","Shared Python 3.13 virtual environment (short path for Windows MAX_PATH)"),
        ("  requirements.txt","Unified pinned requirements covering all three services"),
    ]
    ts = TableStyle([
        ("BACKGROUND",    (0,0),(-1,0), NAVY),
        ("TEXTCOLOR",     (0,0),(-1,0), WHITE),
        ("FONTNAME",      (0,0),(-1,0), "Helvetica-Bold"),
        ("FONTSIZE",      (0,0),(-1,0), 9),
        ("ROWBACKGROUNDS",(0,1),(-1,-1),[WHITE,LIGHT_GRAY]),
        ("FONTSIZE",      (0,1),(-1,-1), 7.5),
        ("TOPPADDING",    (0,0),(-1,-1), 3),("BOTTOMPADDING",(0,0),(-1,-1),3),
        ("LEFTPADDING",   (0,0),(-1,-1), 5),("RIGHTPADDING",(0,0),(-1,-1),5),
        ("GRID",          (0,0),(-1,-1), 0.3, MID_GRAY),
        ("VALIGN",        (0,0),(-1,-1),"MIDDLE"),
    ])
    rows = [[Paragraph("Path / File", ParagraphStyle("TH",fontName="Helvetica-Bold",fontSize=9,textColor=WHITE,leading=12)),
             Paragraph("Description", ParagraphStyle("TH2",fontName="Helvetica-Bold",fontSize=9,textColor=WHITE,leading=12))]]
    for path, desc in tree:
        indent = len(path) - len(path.lstrip(" "))
        rows.append([
            Paragraph(path.strip(), ParagraphStyle("TP",fontName="Courier",fontSize=7,
                textColor=DARK_TEXT,leading=10,leftIndent=indent*3)),
            Paragraph(desc, ParagraphStyle("TD",fontName="Helvetica",fontSize=7.5,
                textColor=SLATE,leading=10)),
        ])
    t = Table(rows, colWidths=[0.40*CONTENT_W, 0.60*CONTENT_W], repeatRows=1)
    t.setStyle(ts)
    out.append(t)
    return out


# ═══════════════════════════════════════════════════════════════════════════════
#  BUILD
# ═══════════════════════════════════════════════════════════════════════════════
def build():
    out_path = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                            "AgentMesh_Technical_Documentation.pdf")
    doc = SimpleDocTemplate(
        out_path, pagesize=A4,
        leftMargin=MARGIN, rightMargin=MARGIN,
        topMargin=MARGIN+10, bottomMargin=MARGIN,
        title="AgentMesh Technical Documentation",
        author="FAB AI Centre of Excellence",
        subject="AgentMesh — FAB Banking AI Agent Platform",
    )
    story = [PageBreak()]  # cover drawn by onFirstPage
    story += toc()
    story += sec1()
    story += sec2()
    story += sec3()
    story += sec4()
    story += sec5()
    story += sec6()
    story += sec7()
    story += sec8()
    story += sec9()
    story += sec10()
    story += sec11()
    story += sec12()
    story += sec13()
    story += sec14()
    doc.build(story, onFirstPage=cover_page, onLaterPages=later_pages)
    print(f"PDF generated → {out_path}")


if __name__ == "__main__":
    build()
