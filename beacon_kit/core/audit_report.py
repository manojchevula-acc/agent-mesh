"""Session audit / regulatory report generator for beacon_kit.

Produces a high-level, audit-oriented **PDF** for a single session, written to
``{OTEL_FILE_OUTPUT_DIR}/audit_regulatory/``. Unlike the operational HTML report
(``html_reporter.py``), this is a regulator-facing narrative: a hybrid
stage->span flow tree of the whole session from start to end, plus a compliance
chain-integrity attestation. Only high-level information is shown — payloads are
never included (Beacon stores hashes only).

Usage (Python API):
    from beacon_kit import generate_audit_report, TelemetryConfig
    config = TelemetryConfig.from_env(service_name="my-service")
    generate_audit_report(config=config, session_id="alice_37ce2a8d")

Usage (CLI, after pip install -e beacon_kit/):
    beacon-audit-report --dir ../observability --session alice_37ce2a8d --out audit.pdf

If ``reportlab`` is not installed, a styled ``.html`` fallback is written instead
and a warning is logged — the host application is never affected.
"""
from __future__ import annotations

import argparse
import logging
import os
from datetime import datetime, timezone
from glob import glob
from typing import Any

from beacon_kit.core.html_reporter import (
    load_all_sources,
    load_jsonl,
    _norm_tid,
    _esc,
    _ts,
)
from beacon_kit.compliance.chain import verify_chain

_log = logging.getLogger("beacon_kit")

try:  # optional dependency — PDF rendering
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
    from reportlab.lib.units import mm
    from reportlab.platypus import (
        SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, HRFlowable,
    )
    _HAVE_REPORTLAB = True
except Exception:  # pragma: no cover - environment without reportlab
    _HAVE_REPORTLAB = False


# ─── Session id candidate attribute keys (set into spans via baggage) ────────────
_SESSION_ATTR_KEYS = ("beacon.session_id", "fab.session_id", "session_id", "session.id")
_USER_ATTR_KEYS = ("beacon.user", "fab.user", "user", "user.id")
_ROLE_ATTR_KEYS = ("fab.role", "beacon.role", "role", "user.role")
_QUERY_ATTR_KEYS = ("query", "fab.query", "input", "prompt", "question")

# Canonical business-stage order for the hybrid stage->span tree
_STAGE_ORDER = [
    "Input Guardrail", "RBAC", "Cache", "Compliance",
    "Domain / Agent", "Tool / MCP", "LLM", "A2A", "Output", "Other",
]

_ROOT_SENTINELS = ("", "none", "0x0000000000000000", "0")


# ─── Small helpers ───────────────────────────────────────────────────────────────

def _attrs(rec: dict) -> dict:
    a = rec.get("attributes")
    return a if isinstance(a, dict) else {}


def _first_attr(rec: dict, keys: tuple[str, ...]) -> str:
    a = _attrs(rec)
    for k in keys:
        v = a.get(k)
        if v not in (None, ""):
            return str(v)
    return ""


def _is_root(sp: dict) -> bool:
    pid = str(sp.get("parent_span_id") or "").strip().lower()
    return pid in _ROOT_SENTINELS


def _parse_ts(value: Any) -> datetime | None:
    """Parse either the OTEL '...Z' or compliance '+00:00' ISO format."""
    if not value:
        return None
    s = str(value).strip()
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        try:
            return datetime.strptime(s[:19], "%Y-%m-%dT%H:%M:%S").replace(tzinfo=timezone.utc)
        except ValueError:
            return None


def _span_start_dt(sp: dict) -> datetime | None:
    ms = sp.get("start_ms")
    if isinstance(ms, (int, float)):
        try:
            return datetime.fromtimestamp(ms / 1000.0, tz=timezone.utc)
        except (ValueError, OverflowError, OSError):
            pass
    return _parse_ts(sp.get("ts") or sp.get("timestamp"))


def _span_start_key(sp: dict) -> float:
    ms = sp.get("start_ms")
    if isinstance(ms, (int, float)):
        return float(ms)
    dt = _parse_ts(sp.get("ts") or sp.get("timestamp"))
    return dt.timestamp() * 1000.0 if dt else 0.0


def _dur(sp: dict) -> float:
    d = sp.get("duration_ms")
    if d is None:
        d = sp.get("latency_ms")
    try:
        return float(d)
    except (TypeError, ValueError):
        return 0.0


def _stage_of(sp: dict) -> str:
    name = str(sp.get("name") or "").lower()
    a = _attrs(sp)

    def has(*subs: str) -> bool:
        return any(s in name for s in subs)

    if has("guardrail", "input.screen", "input_screen", "sanit"):
        return "Input Guardrail"
    if has("rbac", "authz", "authoriz", "permission"):
        return "RBAC"
    if has("cache"):
        return "Cache"
    if has("compliance", "guardrail.check"):
        return "Compliance"
    if has("a2a"):
        return "A2A"
    if has("mcp"):
        return "Tool / MCP"
    if has("tool", "function"):
        return "Tool / MCP"
    if has("llm", "chat", "completion", "model.call"):
        return "LLM"
    if has("redact", "output"):
        return "Output"
    if has("domain", "agent") or a.get("agent.name") or str(sp.get("kind") or "").upper() == "SERVER":
        return "Domain / Agent"
    return "Other"


def _classify_compliance(rec: dict) -> str:
    if "event_type" in rec:
        return "compliance"
    if "required_role" in rec or "actual_role" in rec:
        return "rbac"
    if "rule_id" in rec or "action" in rec:
        return "guardrail"
    return "compliance"


# ─── Session resolution & model building ─────────────────────────────────────────

def _resolve_session(data: dict[str, list[dict]], session_id: str) -> dict:
    """Filter loaded signals to one session; returns a scoping/model dict."""
    spans = data.get("traces", [])
    logs = data.get("logs", [])
    compliance = data.get("compliance", [])
    metrics = data.get("metrics", [])

    trace_ids: set[str] = set()
    scoping = "time_window"
    if session_id:
        for sp in spans:
            a = _attrs(sp)
            if any(str(a.get(k, "")) == session_id for k in _SESSION_ATTR_KEYS):
                tid = _norm_tid(sp.get("trace_id"))
                if tid:
                    trace_ids.add(tid)
        if trace_ids:
            scoping = "session_id"

    if trace_ids:
        def _in(rec: dict) -> bool:
            return _norm_tid(rec.get("trace_id")) in trace_ids
        f_spans = [s for s in spans if _in(s)]
        f_logs = [l for l in logs if _in(l)]
        f_comp = [c for c in compliance if _in(c)]
    else:
        f_spans, f_logs, f_comp = spans, logs, compliance

    return {
        "session_id": session_id,
        "scoping": scoping,
        "spans": f_spans,
        "logs": f_logs,
        "compliance": f_comp,
        "metrics": metrics,  # time-window only (metrics carry no trace_id)
    }


def _build_turns(spans: list[dict], logs: list[dict], compliance: list[dict]) -> list[dict]:
    """Group spans by trace_id (one turn per trace) and classify into stages."""
    by_trace: dict[str, list[dict]] = {}
    for sp in spans:
        by_trace.setdefault(_norm_tid(sp.get("trace_id")) or "_no_trace", []).append(sp)

    # index side-signals by span_id / trace_id
    err_logs_by_span: dict[str, list[dict]] = {}
    for lg in logs:
        if str(lg.get("level") or "").upper() in ("ERROR", "CRITICAL", "FATAL"):
            err_logs_by_span.setdefault(_norm_tid(lg.get("span_id")), []).append(lg)
    comp_by_trace: dict[str, list[dict]] = {}
    for c in compliance:
        comp_by_trace.setdefault(_norm_tid(c.get("trace_id")), []).append(c)

    turns: list[dict] = []
    for tid, tspans in by_trace.items():
        tspans_sorted = sorted(tspans, key=_span_start_key)
        root = next((s for s in tspans_sorted if _is_root(s)), tspans_sorted[0] if tspans_sorted else {})
        label = ""
        user = role = ""
        for s in tspans_sorted:
            label = label or _first_attr(s, _QUERY_ATTR_KEYS)
            user = user or _first_attr(s, _USER_ATTR_KEYS)
            role = role or _first_attr(s, _ROLE_ATTR_KEYS)
        if len(label) > 140:
            label = label[:137] + "..."

        # group into stages preserving canonical order
        stage_map: dict[str, list[dict]] = {}
        for s in tspans_sorted:
            stage_map.setdefault(_stage_of(s), []).append(s)
        stages = []
        n_err = 0
        _MAX_ROWS = 8  # keep the flow high-level; collapse noisy library spans
        for st in _STAGE_ORDER:
            if st not in stage_map:
                continue
            rows = []
            for s in stage_map[st]:
                status = str(s.get("status") or s.get("result") or "").upper()
                if status in ("ERROR", "FAILURE", "FAIL", "FAILED"):
                    n_err += 1
                rows.append({
                    "name": s.get("name") or "span",
                    "status": status or "UNSET",
                    "dur": _dur(s),
                    "errors": err_logs_by_span.get(_norm_tid(s.get("span_id")), []),
                })
            more = max(0, len(rows) - _MAX_ROWS)
            stages.append({"stage": st, "rows": rows[:_MAX_ROWS], "more": more})

        starts = [d for d in (_span_start_dt(s) for s in tspans_sorted) if d]
        start = min(starts) if starts else None
        end = None
        if starts:
            end = max(
                (_span_start_dt(s) or start) for s in tspans_sorted
            )
        turns.append({
            "trace_id": tid,
            "label": label,
            "user": user,
            "role": role,
            "start": start,
            "end": end,
            "stages": stages,
            "compliance": comp_by_trace.get(tid, []),
            "n_err": n_err,
            "n_spans": len(tspans_sorted),
        })

    turns.sort(key=lambda t: (t["start"] or datetime.max.replace(tzinfo=timezone.utc)))
    return turns


def _verify_global_chain(otel_dir: str) -> dict:
    """Verify the FULL compliance chain from the store (not just the session subset).

    The chain hash of every ComplianceEvent depends on the previous event across
    the entire store, so integrity must be verified over the complete ordered
    sequence — a session subset would always fail.
    """
    events: list[dict] = []
    comp_dir = os.path.join(otel_dir, "compliance") if otel_dir else ""
    if comp_dir and os.path.isdir(comp_dir):
        for path in sorted(glob(os.path.join(comp_dir, "*.jsonl"))):
            for rec in load_jsonl(path):
                if rec.get("chain_hash"):
                    events.append(rec)
    if not events:
        return {"status": "N/A", "count": 0, "first": "", "last": ""}
    ok = verify_chain(events)
    return {
        "status": "PASS" if ok else "FAIL",
        "count": len(events),
        "first": str(events[0].get("chain_hash", ""))[:16],
        "last": str(events[-1].get("chain_hash", ""))[:16],
    }


def _num(v: Any) -> float:
    return float(v) if isinstance(v, (int, float)) else 0.0


def _usage_summary(spans: list[dict], metrics: list[dict]) -> dict:
    total_tokens = 0.0
    total_cost = 0.0
    for s in spans:
        a = _attrs(s)
        # prefer gen_ai.* usage convention; fall back to beacon llm.* keys (avoid double count)
        if "gen_ai.usage.input_tokens" in a or "gen_ai.usage.output_tokens" in a:
            total_tokens += _num(a.get("gen_ai.usage.input_tokens")) + _num(a.get("gen_ai.usage.output_tokens"))
        elif "llm.input_tokens" in a or "llm.output_tokens" in a:
            total_tokens += _num(a.get("llm.input_tokens")) + _num(a.get("llm.output_tokens"))
        elif any("total_tokens" in k.lower() for k in a):
            total_tokens += sum(_num(v) for k, v in a.items() if "total_tokens" in k.lower())
        for k, v in a.items():
            if "cost" in k.lower():
                total_cost += _num(v)
    calls = 0.0
    for m in metrics:
        if str(m.get("name") or "").endswith(".calls.total"):
            val = m.get("value")
            if isinstance(val, (int, float)):
                calls += float(val)
    root_durs = sorted(_dur(s) for s in spans if _is_root(s))
    avg = sum(root_durs) / len(root_durs) if root_durs else 0.0
    p95 = root_durs[min(len(root_durs) - 1, int(len(root_durs) * 0.95))] if root_durs else 0.0
    return {
        "tokens": int(total_tokens),
        "cost": round(total_cost, 4),
        "calls": int(calls),
        "avg_ms": round(avg, 1),
        "p95_ms": round(p95, 1),
    }


def _fmt_dt(dt: datetime | None) -> str:
    return dt.strftime("%Y-%m-%d %H:%M:%S UTC") if dt else "—"


def _build_model(session: dict, config: Any, otel_dir: str) -> dict:
    turns = _build_turns(session["spans"], session["logs"], session["compliance"])
    chain = _verify_global_chain(otel_dir)
    usage = _usage_summary(session["spans"], session["metrics"])

    all_starts = [t["start"] for t in turns if t["start"]]
    all_ends = [t["end"] for t in turns if t["end"]]
    session_start = min(all_starts) if all_starts else None
    session_end = max(all_ends) if all_ends else None
    duration_s = (session_end - session_start).total_seconds() if (session_start and session_end) else 0.0

    n_guardrail = sum(
        1 for t in turns for c in t["compliance"]
        if _classify_compliance(c) == "guardrail" and c.get("triggered")
    )
    n_comp_events = sum(len(t["compliance"]) for t in turns)
    n_err = sum(t["n_err"] for t in turns)
    user = next((t["user"] for t in turns if t["user"]), "")
    role = next((t["role"] for t in turns if t["role"]), "")
    service = getattr(config, "service_name", "") or ""
    env = getattr(config, "environment", "") or ""
    # environment can also come off span resource
    for s in session["spans"]:
        res = s.get("resource")
        if isinstance(res, dict):
            env = env or str(res.get("deployment.environment", ""))
            service = service or str(res.get("service.name", ""))
            break

    return {
        "session_id": session["session_id"] or "(whole run)",
        "scoping": session["scoping"],
        "service": service or "unknown-service",
        "environment": env or "unknown",
        "generated": datetime.now(timezone.utc),
        "session_start": session_start,
        "session_end": session_end,
        "duration_s": round(duration_s, 1),
        "n_turns": len(turns),
        "n_guardrail": n_guardrail,
        "n_comp_events": n_comp_events,
        "n_err": n_err,
        "turns": turns,
        "chain": chain,
        "usage": usage,
    }


# ─── PDF rendering ────────────────────────────────────────────────────────────────

def _status_hex(status: str) -> str:
    s = status.upper()
    if s in ("OK", "PASS", "PASSED", "ALLOW", "SUCCESS"):
        return "#1a7f37"
    if s in ("ERROR", "FAIL", "FAILED", "FAILURE", "BLOCK", "DENY"):
        return "#b42318"
    if s in ("WARN", "WARNING", "ESCALATE"):
        return "#b54708"
    return "#475467"


def _render_pdf(model: dict, out_path: str) -> None:
    os.makedirs(os.path.dirname(os.path.abspath(out_path)), exist_ok=True)
    doc = SimpleDocTemplate(
        out_path, pagesize=A4,
        leftMargin=18 * mm, rightMargin=18 * mm, topMargin=16 * mm, bottomMargin=16 * mm,
        title="Session Audit & Regulatory Report",
    )
    ss = getSampleStyleSheet()
    h1 = ParagraphStyle("h1", parent=ss["Title"], fontSize=18, spaceAfter=4)
    h2 = ParagraphStyle("h2", parent=ss["Heading2"], fontSize=12, spaceBefore=10, spaceAfter=4,
                        textColor=colors.HexColor("#1f2d3d"))
    turn_h = ParagraphStyle("turn", parent=ss["Heading3"], fontSize=11, spaceBefore=8, spaceAfter=2,
                            textColor=colors.HexColor("#0b4f9c"))
    stage_h = ParagraphStyle("stage", parent=ss["Normal"], fontSize=9.5, leftIndent=8, spaceBefore=3,
                            textColor=colors.HexColor("#344054"), fontName="Helvetica-Bold")
    span_p = ParagraphStyle("span", parent=ss["Normal"], fontSize=9, leftIndent=22, leading=12)
    sub_p = ParagraphStyle("sub", parent=ss["Normal"], fontSize=8, leftIndent=36, leading=11,
                        textColor=colors.HexColor("#667085"))
    small = ParagraphStyle("small", parent=ss["Normal"], fontSize=8, textColor=colors.HexColor("#667085"))

    story: list[Any] = []
    story.append(Paragraph("Session Audit &amp; Regulatory Report", h1))
    story.append(Paragraph(
        f"Service: <b>{_esc(model['service'])}</b> &nbsp;|&nbsp; Environment: <b>{_esc(model['environment'])}</b> "
        f"&nbsp;|&nbsp; Generated: {_esc(_fmt_dt(model['generated']))}", small))
    story.append(HRFlowable(width="100%", thickness=0.8, color=colors.HexColor("#d0d5dd"), spaceBefore=6, spaceAfter=8))

    # 1. Header / summary block
    hdr_rows = [
        ["Session ID", model["session_id"], "Turns", str(model["n_turns"])],
        ["User / Role", f"{model.get('turns') and (next((t['user'] for t in model['turns'] if t['user']), '') or '—')} / "
                        f"{next((t['role'] for t in model['turns'] if t['role']), '') or '—'}",
         "Guardrail triggers", str(model["n_guardrail"])],
        ["Start", _fmt_dt(model["session_start"]), "Compliance events", str(model["n_comp_events"])],
        ["End", _fmt_dt(model["session_end"]), "Errors", str(model["n_err"])],
        ["Duration", f"{model['duration_s']} s", "Scoping", model["scoping"]],
    ]
    t = Table(hdr_rows, colWidths=[28 * mm, 66 * mm, 34 * mm, 26 * mm])
    t.setStyle(TableStyle([
        ("FONTSIZE", (0, 0), (-1, -1), 8.5),
        ("BACKGROUND", (0, 0), (0, -1), colors.HexColor("#f2f4f7")),
        ("BACKGROUND", (2, 0), (2, -1), colors.HexColor("#f2f4f7")),
        ("FONTNAME", (0, 0), (0, -1), "Helvetica-Bold"),
        ("FONTNAME", (2, 0), (2, -1), "Helvetica-Bold"),
        ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#e4e7ec")),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("LEFTPADDING", (0, 0), (-1, -1), 5), ("RIGHTPADDING", (0, 0), (-1, -1), 5),
        ("TOPPADDING", (0, 0), (-1, -1), 3), ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
    ]))
    story.append(t)

    # 2. Compliance chain attestation
    ch = model["chain"]
    color = {"PASS": "#1a7f37", "FAIL": "#b42318"}.get(ch["status"], "#475467")
    story.append(Paragraph("Compliance Chain Attestation", h2))
    story.append(Paragraph(
        f"Tamper-evidence check over the full audit chain: "
        f"<font color='{color}'><b>{ch['status']}</b></font> "
        f"({ch['count']} chained events). "
        f"First hash <font face='Courier'>{_esc(ch['first']) or '—'}</font>, "
        f"last hash <font face='Courier'>{_esc(ch['last']) or '—'}</font>.", small))

    # 3. Session flow tree (hybrid stages over spans)
    story.append(Paragraph("Session Flow (start → end)", h2))
    if not model["turns"]:
        story.append(Paragraph("No spans found for this session.", small))
    for i, turn in enumerate(model["turns"], 1):
        ctx = f" — {_esc(turn['label'])}" if turn["label"] else ""
        story.append(Paragraph(
            f"Turn {i}{ctx} "
            f"<font size=7 color='#98a2b3'>[trace {_esc(turn['trace_id'][:12])} · "
            f"{_fmt_dt(turn['start'])} · {turn['n_spans']} spans]</font>", turn_h))
        for st in turn["stages"]:
            story.append(Paragraph(_esc(st["stage"]), stage_h))
            for row in st["rows"]:
                story.append(Paragraph(
                    f"• {_esc(row['name'])} "
                    f"<font color='{_status_hex(row['status'])}'>[{_esc(row['status'])}]</font> "
                    f"<font color='#98a2b3'>{row['dur']:.1f} ms</font>", span_p))
                for el in row["errors"][:3]:
                    story.append(Paragraph(f"↳ error: {_esc(str(el.get('msg') or '')[:160])}", sub_p))
            if st.get("more"):
                story.append(Paragraph(f"<font color='#98a2b3'>… +{st['more']} more spans</font>", span_p))
        # per-turn compliance/guardrail decisions
        for c in turn["compliance"]:
            kind = _classify_compliance(c)
            if kind == "guardrail":
                desc = f"guardrail {_esc(c.get('rule_id') or '')} → {_esc(c.get('action') or '')}" \
                    + (" (triggered)" if c.get("triggered") else "")
            elif kind == "rbac":
                desc = f"RBAC {_esc(c.get('resource') or '')}: {_esc(c.get('actual_role') or '')} → {_esc(c.get('outcome') or '')}"
            else:
                desc = f"compliance {_esc(c.get('event_type') or '')} → {_esc(c.get('outcome') or '')}"
            story.append(Paragraph(f"⚖ {desc}", sub_p))

    # 4. Decision log table
    story.append(Paragraph("Decision Log (compliance, guardrail &amp; access events)", h2))
    rows = [["Time", "Type", "Detail", "Outcome/Action", "Trace"]]
    for turn in model["turns"]:
        for c in turn["compliance"]:
            kind = _classify_compliance(c)
            if kind == "guardrail":
                detail = str(c.get("rule_id") or "")
                outcome = str(c.get("action") or "") + (" ⚠" if c.get("triggered") else "")
            elif kind == "rbac":
                detail = f"{c.get('resource','')} ({c.get('actual_role','')}/{c.get('required_role','')})"
                outcome = str(c.get("outcome") or "")
            else:
                detail = str(c.get("event_type") or "")
                outcome = str(c.get("outcome") or "")
            rows.append([
                _ts(c.get("timestamp"))[:19], kind, detail[:40], outcome[:18],
                _norm_tid(c.get("trace_id"))[:10],
            ])
    if len(rows) == 1:
        rows.append(["—", "—", "no compliance events in session", "—", "—"])
    dt = Table(rows, colWidths=[34 * mm, 20 * mm, 55 * mm, 30 * mm, 22 * mm], repeatRows=1)
    dt.setStyle(TableStyle([
        ("FONTSIZE", (0, 0), (-1, -1), 7.5),
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#1f2d3d")),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("GRID", (0, 0), (-1, -1), 0.3, colors.HexColor("#e4e7ec")),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#f9fafb")]),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("LEFTPADDING", (0, 0), (-1, -1), 4), ("RIGHTPADDING", (0, 0), (-1, -1), 4),
        ("TOPPADDING", (0, 0), (-1, -1), 2), ("BOTTOMPADDING", (0, 0), (-1, -1), 2),
    ]))
    story.append(dt)

    # 5. Usage summary
    u = model["usage"]
    story.append(Paragraph("Usage Summary", h2))
    us = Table([
        ["LLM tokens", str(u["tokens"]), "Est. cost", str(u["cost"])],
        ["Call count", str(u["calls"]), "Avg latency", f"{u['avg_ms']} ms"],
        ["p95 latency", f"{u['p95_ms']} ms", "", ""],
    ], colWidths=[28 * mm, 40 * mm, 28 * mm, 40 * mm])
    us.setStyle(TableStyle([
        ("FONTSIZE", (0, 0), (-1, -1), 8.5),
        ("FONTNAME", (0, 0), (0, -1), "Helvetica-Bold"),
        ("FONTNAME", (2, 0), (2, -1), "Helvetica-Bold"),
        ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#e4e7ec")),
        ("TOPPADDING", (0, 0), (-1, -1), 3), ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
        ("LEFTPADDING", (0, 0), (-1, -1), 5),
    ]))
    story.append(us)

    # 6. Footer
    story.append(Spacer(1, 8))
    story.append(HRFlowable(width="100%", thickness=0.6, color=colors.HexColor("#d0d5dd"), spaceAfter=4))
    scope_note = ("scoped by explicit session_id"
                  if model["scoping"] == "session_id"
                  else "scoped by ±10-minute time window (no session_id attribute found in spans)")
    story.append(Paragraph(
        f"Report {scope_note}. Metrics are time-window scoped (no per-trace id). "
        f"Payloads are never stored — only SHA-256 hashes (privacy by design). "
        f"Generated by beacon_kit audit_report.", small))

    doc.build(story)


# ─── HTML fallback (when reportlab is unavailable) ───────────────────────────────

def _render_html_fallback(model: dict, out_path: str) -> None:
    os.makedirs(os.path.dirname(os.path.abspath(out_path)), exist_ok=True)
    ch = model["chain"]
    parts = [
        "<!doctype html><meta charset='utf-8'><title>Session Audit Report</title>",
        "<style>body{font:14px/1.5 system-ui,Segoe UI,Arial;margin:32px;color:#1f2d3d}"
        "h1{font-size:22px}h2{font-size:16px;margin-top:22px;border-bottom:1px solid #e4e7ec;padding-bottom:4px}"
        ".turn{color:#0b4f9c;font-weight:600;margin-top:14px}.stage{font-weight:600;margin-left:12px;margin-top:6px}"
        ".span{margin-left:28px}.sub{margin-left:44px;color:#667085;font-size:12px}"
        "table{border-collapse:collapse;width:100%;font-size:12px}td,th{border:1px solid #e4e7ec;padding:4px 6px;text-align:left}"
        "th{background:#1f2d3d;color:#fff}.pass{color:#1a7f37;font-weight:700}.fail{color:#b42318;font-weight:700}"
        ".mut{color:#667085;font-size:12px}</style>",
        f"<h1>Session Audit &amp; Regulatory Report</h1>",
        f"<p class='mut'>Service <b>{_esc(model['service'])}</b> | Env <b>{_esc(model['environment'])}</b> | "
        f"Generated {_esc(_fmt_dt(model['generated']))}</p>",
        "<h2>Summary</h2><table>"
        f"<tr><th>Session</th><td>{_esc(model['session_id'])}</td><th>Turns</th><td>{model['n_turns']}</td></tr>"
        f"<tr><th>Start</th><td>{_fmt_dt(model['session_start'])}</td><th>Guardrail triggers</th><td>{model['n_guardrail']}</td></tr>"
        f"<tr><th>End</th><td>{_fmt_dt(model['session_end'])}</td><th>Compliance events</th><td>{model['n_comp_events']}</td></tr>"
        f"<tr><th>Duration</th><td>{model['duration_s']} s</td><th>Errors</th><td>{model['n_err']}</td></tr></table>",
        f"<h2>Compliance Chain Attestation</h2><p>Status: "
        f"<span class='{'pass' if ch['status']=='PASS' else 'fail'}'>{ch['status']}</span> "
        f"({ch['count']} events). first {_esc(ch['first']) or '—'} · last {_esc(ch['last']) or '—'}</p>",
        "<h2>Session Flow (start → end)</h2>",
    ]
    if not model["turns"]:
        parts.append("<p class='mut'>No spans found for this session.</p>")
    for i, turn in enumerate(model["turns"], 1):
        ctx = f" — {_esc(turn['label'])}" if turn["label"] else ""
        parts.append(f"<div class='turn'>Turn {i}{ctx} <span class='mut'>[trace {_esc(turn['trace_id'][:12])} · {_fmt_dt(turn['start'])}]</span></div>")
        for st in turn["stages"]:
            parts.append(f"<div class='stage'>{_esc(st['stage'])}</div>")
            for row in st["rows"]:
                parts.append(f"<div class='span'>• {_esc(row['name'])} [{_esc(row['status'])}] <span class='mut'>{row['dur']:.1f} ms</span></div>")
                for el in row["errors"][:3]:
                    parts.append(f"<div class='sub'>↳ error: {_esc(str(el.get('msg') or '')[:160])}</div>")
            if st.get("more"):
                parts.append(f"<div class='span mut'>… +{st['more']} more spans</div>")
        for c in turn["compliance"]:
            kind = _classify_compliance(c)
            parts.append(f"<div class='sub'>⚖ {kind}: {_esc(str(c.get('event_type') or c.get('rule_id') or c.get('resource') or ''))} → {_esc(str(c.get('outcome') or c.get('action') or ''))}</div>")
    u = model["usage"]
    parts.append(
        f"<h2>Usage Summary</h2><table><tr><th>LLM tokens</th><td>{u['tokens']}</td><th>Est. cost</th><td>{u['cost']}</td></tr>"
        f"<tr><th>Calls</th><td>{u['calls']}</td><th>Avg latency</th><td>{u['avg_ms']} ms</td></tr>"
        f"<tr><th>p95 latency</th><td>{u['p95_ms']} ms</td><th></th><td></td></tr></table>")
    scope_note = ("scoped by explicit session_id" if model["scoping"] == "session_id"
                  else "scoped by ±10-minute time window (no session_id attribute found)")
    parts.append(f"<p class='mut'>Report {scope_note}. Payloads are never stored — hashes only. beacon_kit audit_report (HTML fallback; install reportlab for PDF).</p>")
    with open(out_path, "w", encoding="utf-8") as f:
        f.write("".join(parts))


# ─── Public API ───────────────────────────────────────────────────────────────────

def generate_audit_report(
    config: Any = None,
    session_id: str = "",
    otel_dir: str = "",
    out_path: str = "",
) -> str:
    """Generate a session audit/regulatory PDF into {otel_dir}/audit_regulatory/.

    Returns the absolute path written. Falls back to .html if reportlab is absent.
    """
    otel_dir = otel_dir or getattr(config, "file_output_dir", "") or os.getenv("OTEL_FILE_OUTPUT_DIR", "")
    otel_dir = os.path.abspath(otel_dir) if otel_dir else ""
    session_ts = getattr(config, "_file_ts", "") if config is not None else ""
    if not session_id and config is not None:
        session_id = getattr(config, "file_session_id", "") or ""

    ext = "pdf" if _HAVE_REPORTLAB else "html"
    if not out_path:
        if config is not None and hasattr(config, "audit_report_path"):
            out_path = config.audit_report_path(ext)
        else:
            ts = session_ts or datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
            base = otel_dir or "."
            out_path = os.path.join(base, "audit_regulatory", f"{ts}_audit.{ext}")

    data = load_all_sources(otel_dir, session_ts)
    session = _resolve_session(data, session_id)
    model = _build_model(session, config, otel_dir)

    if _HAVE_REPORTLAB and out_path.lower().endswith(".pdf"):
        _render_pdf(model, out_path)
    else:
        if out_path.lower().endswith(".pdf"):
            out_path = out_path[:-4] + ".html"
            _log.warning("reportlab not installed - writing HTML audit report instead: %s", out_path)
        _render_html_fallback(model, out_path)
    return os.path.abspath(out_path)


def main() -> None:
    parser = argparse.ArgumentParser(prog="beacon-audit-report",
                                    description="Generate a session audit/regulatory report from beacon_kit JSONL.")
    parser.add_argument("--dir", dest="otel_dir", default="", help="observability root dir (traces/logs/metrics/compliance)")
    parser.add_argument("--session", dest="session_id", default="", help="explicit session_id to scope the report")
    parser.add_argument("--out", dest="out_path", default="", help="output file path (.pdf; .html fallback)")
    args = parser.parse_args()
    path = generate_audit_report(otel_dir=args.otel_dir, session_id=args.session_id, out_path=args.out_path)
    fmt = "PDF" if path.lower().endswith(".pdf") else "HTML"
    print(f"[beacon-audit-report] {fmt} audit report written to: {path}")


if __name__ == "__main__":
    main()
