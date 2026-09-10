"""HTML observability report generator for beacon_kit.

Reads all JSONL sources written by FileSpanExporter / FileMetricExporter /
FileLogExporter / compliance events and produces a single self-contained
HTML file with tabs, live filtering, and parent-child span trees.

Usage (Python API):
    from beacon_kit import generate_html_report, TelemetryConfig
    config = TelemetryConfig.from_env(service_name="my-service")
    generate_html_report(config=config, out_path="report.html")

Usage (CLI, after pip install -e beacon_kit/):
    beacon-report --dir ../observability --out report.html
"""
from __future__ import annotations

import argparse
import json
import os
import re
from datetime import datetime, timezone
from glob import glob
from typing import Any


# ─── JSONL loading ─────────────────────────────────────────────────────────────

def load_jsonl(path: str) -> list[dict]:
    """Read a JSONL file, silently skipping malformed lines and missing files."""
    records: list[dict] = []
    try:
        with open(path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    try:
                        records.append(json.loads(line))
                    except json.JSONDecodeError:
                        pass
    except (FileNotFoundError, PermissionError, OSError):
        pass
    return records


_TS_PREFIX_RE = re.compile(r'^(\d{8}_\d{6})_')


def _session_iso(session_ts: str) -> str:
    """Convert '20260909_111300' → '2026-09-09T11:13:00' for ISO timestamp comparison."""
    if len(session_ts) < 15:
        return ""
    return (
        f"{session_ts[:4]}-{session_ts[4:6]}-{session_ts[6:8]}"
        f"T{session_ts[9:11]}:{session_ts[11:13]}:{session_ts[13:15]}"
    )


def _ts_in_window(file_ts: str, session_ts: str, window_minutes: int = 10) -> bool:
    """True if file_ts is within window_minutes of session_ts.

    Handles the multi-process mesh case where each a2a node has a slightly
    different startup timestamp (seconds apart), so an exact prefix match would
    miss companion nodes. Old sessions (minutes away) are excluded.
    """
    try:
        fn_dt = datetime.strptime(file_ts, "%Y%m%d_%H%M%S")
        sess_dt = datetime.strptime(session_ts, "%Y%m%d_%H%M%S")
        return abs((fn_dt - sess_dt).total_seconds()) <= window_minutes * 60
    except (ValueError, OverflowError):
        return True


def _glob_signal_dir(base_dir: str, signal: str, source_type: str, session_ts: str = "") -> list[dict]:
    """Read *.jsonl files from {base_dir}/{signal}/ and tag each record.

    When session_ts is given:
    - Timestamped files (e.g. 20260909_114012_service.jsonl): included only if
      their timestamp is within 10 minutes of session_ts (captures all mesh nodes
      from the same run while excluding old sessions).
    - Rolling files (e.g. compliance_audit.jsonl): always read, but records whose
      timestamp predates session_ts are skipped.
    """
    signal_dir = os.path.join(base_dir, signal)
    session_iso = _session_iso(session_ts)
    records: list[dict] = []
    for path in sorted(glob(os.path.join(signal_dir, "*.jsonl"))):
        filename = os.path.basename(path)
        m = _TS_PREFIX_RE.match(filename)
        if session_ts and m and not _ts_in_window(m.group(1), session_ts):
            continue  # timestamped file from a different session — skip entirely
        for rec in load_jsonl(path):
            if session_ts and not m and session_iso:
                rec_ts = str(rec.get("timestamp") or rec.get("ts") or "")[:19]
                if rec_ts and rec_ts < session_iso:
                    continue  # rolling file — skip records before this session
            rec["_source_type"] = source_type
            rec["_source_file"] = filename
            records.append(rec)
    return records


def load_all_sources(
    otel_dir: str = "",
    session_ts: str = "",
) -> dict[str, list[dict]]:
    """Load JSONL observability sources exclusively from otel_dir.

    Only reads files inside the observability folder (traces/, metrics/, logs/,
    compliance/). Nothing outside that folder is ever touched.

    Session scoping (when session_ts is provided):
    - Timestamped JSONL files (e.g. 20260909_114012_service.jsonl) are included
      only when their timestamp is within 10 minutes of session_ts, covering all
      mesh nodes that started together.
    - Rolling files (e.g. compliance_audit.jsonl) are read but records before
      session_ts are dropped.

    Pass session_ts="" for full-history mode (no filtering).

    Returns a dict with keys: audit, traces, metrics, logs, compliance.
    audit is always an empty list (kept for backward-compat with build_request_index).
    """
    data: dict[str, list[dict]] = {
        "audit": [],
        "traces": [],
        "metrics": [],
        "logs": [],
        "compliance": [],
    }
    if otel_dir and os.path.isdir(otel_dir):
        data["traces"].extend(_glob_signal_dir(otel_dir, "traces", "otel_span", session_ts))
        data["metrics"].extend(_glob_signal_dir(otel_dir, "metrics", "otel_metric", session_ts))
        data["logs"].extend(_glob_signal_dir(otel_dir, "logs", "otel_log", session_ts))
        data["compliance"].extend(_glob_signal_dir(otel_dir, "compliance", "compliance", session_ts))
    return data


# ─── Request index ──────────────────────────────────────────────────────────────

def _norm_tid(tid: Any) -> str:
    """Normalize a trace_id to a lowercase hex string without 0x prefix."""
    if not tid:
        return ""
    s = str(tid).strip()
    if s.startswith("0x"):
        s = s[2:]
    return s.lower().lstrip("0") or "0"


def build_request_index(data: dict[str, list[dict]]) -> dict[str, dict]:
    """Group all records by trace_id.

    Each entry holds:
        trace_id   str
        audit      list[dict]   — from audit_trail
        spans      list[dict]   — from otel traces + legacy trace_log
        logs       list[dict]   — from otel logs
        compliance list[dict]   — from compliance / guardrail events
    """
    index: dict[str, dict] = {}

    def _get(tid: str) -> dict:
        tid = tid or "_no_trace"
        if tid not in index:
            index[tid] = {"trace_id": tid, "audit": [], "spans": [], "logs": [], "compliance": []}
        return index[tid]

    for rec in data["audit"]:
        _get(_norm_tid(rec.get("trace_id")))["audit"].append(rec)
    for rec in data["traces"]:
        _get(_norm_tid(rec.get("trace_id")))["spans"].append(rec)
    for rec in data["logs"]:
        _get(_norm_tid(rec.get("trace_id")))["logs"].append(rec)
    for rec in data["compliance"]:
        _get(_norm_tid(rec.get("trace_id")))["compliance"].append(rec)

    return index


# ─── HTML helpers ───────────────────────────────────────────────────────────────

def _esc(s: Any) -> str:
    return (
        str(s)
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


def _ts(ts: Any) -> str:
    if not ts:
        return ""
    s = str(ts)
    if "." in s:
        s = s[: s.index(".") + 4]
    return s.replace("T", " ").replace("+00:00", "Z").replace("Z", " UTC")


def _badge(status: Any) -> str:
    s = str(status or "").upper()
    if s in ("SUCCESS", "OK", "PASS", "PASSED", "ALLOW"):
        cls = "bs"
    elif s in ("ERROR", "FAILURE", "FAIL", "FAILED", "BLOCK", "DENY"):
        cls = "be"
    elif s in ("WARN", "WARNING"):
        cls = "bw"
    else:
        cls = "bi"
    return f'<span class="b {cls}">{_esc(s or "—")}</span>'


def _level_badge(level: Any) -> str:
    s = str(level or "INFO").upper()
    mapping = {"ERROR": "be", "WARNING": "bw", "WARN": "bw", "DEBUG": "bd", "INFO": "bi"}
    return f'<span class="b {mapping.get(s, "bi")}">{_esc(s)}</span>'


def _json_pre(rec: dict) -> str:
    clean = {k: v for k, v in rec.items() if not k.startswith("_")}
    return f'<pre class="jp hidden">{_esc(json.dumps(clean, default=str, indent=2))}</pre>'


# ─── Span tree renderer ─────────────────────────────────────────────────────────

def _span_tree(spans: list[dict]) -> str:
    if not spans:
        return ""
    children: dict[str, list[dict]] = {}
    roots: list[dict] = []
    for sp in spans:
        pid = str(sp.get("parent_span_id") or "").strip()
        if not pid or pid in ("None", "0x0000000000000000", "0"):
            roots.append(sp)
        else:
            children.setdefault(pid, []).append(sp)
    if not roots:
        roots = spans

    def _row(sp: dict, depth: int) -> str:
        name = _esc(sp.get("name") or sp.get("event_type") or "span")
        dur = sp.get("duration_ms") or sp.get("latency_ms")
        dur_s = f"{dur:.1f}ms" if isinstance(dur, (int, float)) else str(dur or "")
        sid = str(sp.get("span_id") or "").strip()
        status = sp.get("status") or sp.get("result") or ""
        indent = depth * 22
        html = (
            f'<div class="sr" style="margin-left:{indent}px">'
            f'<span class="sn">{name}</span>'
            f'{_badge(status)}'
            f'<span class="sd">{_esc(dur_s)}</span>'
            f'<button class="tb" onclick="td(this)">JSON</button>'
            f'{_json_pre(sp)}'
            f"</div>"
        )
        for child in children.get(sid, []):
            html += _row(child, depth + 1)
        return html

    return '<div class="st">' + "".join(_row(r, 0) for r in roots) + "</div>"


# ─── Tab content renderers ──────────────────────────────────────────────────────

def _mini_log_rows(logs: list[dict], cap: int = 20) -> str:
    rows = []
    for r in logs[:cap]:
        rows.append(
            f'<div class="mr">'
            f'{_level_badge(r.get("level"))}'
            f'<span class="dim">{_esc(_ts(r.get("ts") or r.get("timestamp")))}</span>'
            f'<span class="lm">{_esc(str(r.get("msg") or r.get("message") or "")[:300])}</span>'
            f"</div>"
        )
    if len(logs) > cap:
        rows.append(f'<div class="mr dim">… {len(logs) - cap} more entries</div>')
    return "".join(rows)


def _mini_compliance_rows(recs: list[dict]) -> str:
    rows = []
    for r in recs:
        triggered = r.get("triggered")
        rule = _esc(r.get("rule_id") or r.get("event_type") or "?")
        action = _esc(r.get("action") or r.get("outcome") or "?")
        badge = '<span class="b be">TRIGGERED</span>' if triggered else '<span class="b bs">OK</span>'
        rows.append(
            f'<div class="mr">'
            f'{badge}<strong>{rule}</strong>'
            f'<span class="dim">{action} · {_esc(_ts(r.get("timestamp")))}</span>'
            f'<button class="tb" onclick="td(this)">JSON</button>'
            f'{_json_pre(r)}'
            f"</div>"
        )
    return "".join(rows)


def _mini_audit_rows(recs: list[dict]) -> str:
    rows = []
    for r in recs:
        rows.append(
            f'<div class="mr">'
            f'{_badge(r.get("status"))}'
            f'<strong>{_esc(r.get("agent_name") or "?")}</strong>'
            f'<span class="dim">{_esc(r.get("latency_ms") or "")}ms · {_esc(r.get("total_tokens") or "")} tokens</span>'
            f'<button class="tb" onclick="td(this)">JSON</button>'
            f'{_json_pre(r)}'
            f"</div>"
        )
    return "".join(rows)


def _render_requests(index: dict) -> tuple[str, list[str], list[str]]:
    all_agents: set[str] = set()
    all_statuses: set[str] = set()
    rows: list[str] = []
    idx = 0

    def _sort_key(e: dict) -> str:
        if e["audit"]:
            return e["audit"][0].get("timestamp") or ""
        if e["spans"]:
            return e["spans"][0].get("ts") or e["spans"][0].get("timestamp") or ""
        return ""

    entries = sorted(index.values(), key=_sort_key, reverse=True)

    for entry in entries:
        tid = entry["trace_id"]
        audit = entry["audit"]
        spans = entry["spans"]
        logs = entry["logs"]
        comp = entry["compliance"]

        if audit:
            first = audit[0]
            ts_v = _esc(_ts(first.get("timestamp")))
            user = _esc(first.get("user") or "—")
            agents = list({r.get("agent_name") for r in audit if r.get("agent_name")})
            status = str(first.get("status") or "—")
            total_ms: float = sum(float(r.get("latency_ms") or 0) for r in audit)
            total_tok = sum(int(r.get("total_tokens") or 0) for r in audit)
        elif spans:
            first = spans[0]
            ts_v = _esc(_ts(first.get("ts") or first.get("timestamp")))
            user = "—"
            agents = list({s.get("name") for s in spans if s.get("name")})[:3]
            status = str(first.get("status") or "—")
            total_ms = sum(float(s.get("duration_ms") or 0) for s in spans if s.get("_source_type") == "otel_span")
            total_tok = 0
        else:
            continue

        for a in agents:
            all_agents.add(a)
        all_statuses.add(status.upper())

        triggered = [c for c in comp if c.get("triggered")]
        comp_badge = (
            f'<span class="b be cf" title="{len(triggered)} guardrail(s) triggered">⚠ {len(triggered)}</span>'
            if triggered else ""
        )

        agents_str = _esc(", ".join(agents[:3])) + (f" +{len(agents)-3}" if len(agents) > 3 else "")
        dur_s = f"{total_ms:.0f}ms" if total_ms else "—"
        tok_s = f"{total_tok:,}" if total_tok else "—"
        tid_short = _esc(tid[:20] + "…" if len(tid) > 20 else tid)
        did = f"dr{idx}"
        idx += 1
        filter_text = _esc(f"{tid} {user} {' '.join(agents)} {status}".lower())
        agent0 = _esc(agents[0] if agents else "")

        gantt = _render_gantt_bars(spans)
        detail_html = (
            f'<div class="dp">'
            f'<div class="ds"><h4>Trace ID</h4><code>{_esc(tid)}</code></div>'
            + (f'<div class="ds"><h4>Span Timeline ({len(spans)} spans)</h4>{gantt if gantt else _span_tree(spans)}</div>' if spans else "")
            + (f'<div class="ds"><h4>Compliance ({len(comp)} events, {len(triggered)} triggered)</h4>{_mini_compliance_rows(comp)}</div>' if comp else "")
            + (f'<div class="ds"><h4>Logs ({len(logs)} entries)</h4>{_mini_log_rows(logs)}</div>' if logs else "")
            + (f'<div class="ds"><h4>Audit Records</h4>{_mini_audit_rows(audit)}</div>' if audit else "")
            + "</div>"
        )

        rows.append(
            f'<tr class="rr" data-filter="{filter_text}" data-status="{_esc(status.upper())}" data-agent="{agent0}">'
            f'<td class="mono sm">{tid_short}</td>'
            f"<td>{ts_v}</td>"
            f"<td>{user}</td>"
            f'<td class="sm">{agents_str}</td>'
            f"<td>{_badge(status)} {comp_badge}</td>"
            f'<td class="mono">{_esc(dur_s)}</td>'
            f'<td class="mono">{_esc(tok_s)}</td>'
            f'<td><button class="eb" onclick="tr2(\'{did}\',this)">▶</button></td>'
            f"</tr>"
            f'<tr id="{did}" class="hidden"><td colspan="8">{detail_html}</td></tr>'
        )

    return "\n".join(rows), sorted(all_agents), sorted(all_statuses)


def _render_logs(logs: list[dict]) -> str:
    rows = []
    for r in logs:
        level = str(r.get("level") or "INFO").upper()
        ts_v = _esc(_ts(r.get("ts") or r.get("timestamp")))
        logger = _esc(r.get("logger") or "")
        msg = _esc(str(r.get("msg") or r.get("message") or ""))
        tid_raw = _norm_tid(r.get("trace_id"))
        tid = _esc(tid_raw[:16])
        # Structured attribute chips — show key=value pairs from OTel attributes
        attrs = r.get("attributes") or {}
        chips_html = ""
        if isinstance(attrs, dict):
            chips = []
            for k, v in list(attrs.items())[:8]:
                label = _esc(f"{k}={v}")
                chips.append(f'<span class="ac" onclick="filterByAttr(\'{_esc(k)}\',\'{_esc(str(v))}\')" title="Filter by {_esc(k)}">{label}</span>')
            chips_html = f'<div class="acs">{"".join(chips)}</div>' if chips else ""
        tid_link = f'<a class="tl" onclick="jumpToTrace(\'{_esc(tid_raw)}\')" href="#">{tid}</a>' if tid_raw else "—"
        ft = _esc(f"{level} {r.get('logger','')} {r.get('msg','')} {r.get('trace_id','')} {' '.join(str(v) for v in (attrs.values() if isinstance(attrs, dict) else []))}".lower())
        rows.append(
            f'<tr class="lr" data-filter="{ft}" data-level="{_esc(level)}">'
            f"<td>{ts_v}</td>"
            f"<td>{_level_badge(level)}</td>"
            f'<td class="sm mono">{logger}</td>'
            f'<td class="lm">{msg}{chips_html}</td>'
            f'<td class="mono sm">{tid_link}</td>'
            f'<td><button class="tb" onclick="td(this,1)">JSON</button>{_json_pre(r)}</td>'
            f"</tr>"
        )
    return "\n".join(rows)


def _render_spans(spans: list[dict]) -> str:
    rows = []
    for sp in spans:
        ts_v = _esc(_ts(sp.get("ts") or sp.get("timestamp")))
        name = _esc(sp.get("name") or sp.get("event_type") or "?")
        kind = _esc(sp.get("kind") or "")
        status = sp.get("status") or sp.get("result") or "?"
        dur = sp.get("duration_ms") or sp.get("latency_ms")
        dur_s = _esc(f"{dur:.1f}ms" if isinstance(dur, (int, float)) else str(dur or "—"))
        tid = _esc(_norm_tid(sp.get("trace_id"))[:16])
        ft = _esc(f"{sp.get('name','')} {sp.get('event_type','')} {status} {sp.get('trace_id','')}".lower())
        rows.append(
            f'<tr class="srt" data-filter="{ft}">'
            f"<td>{ts_v}</td>"
            f"<td><strong>{name}</strong></td>"
            f"<td>{kind}</td>"
            f"<td>{_badge(status)}</td>"
            f'<td class="mono">{dur_s}</td>'
            f'<td class="mono sm">{tid}</td>'
            f'<td><button class="tb" onclick="td(this,1)">JSON</button>{_json_pre(sp)}</td>'
            f"</tr>"
        )
    return "\n".join(rows)


def _render_compliance(recs: list[dict]) -> str:
    rows = []
    for r in recs:
        triggered = r.get("triggered")
        action = str(r.get("action") or r.get("outcome") or "?")
        rule = _esc(r.get("rule_id") or r.get("event_type") or "?")
        ts_v = _esc(_ts(r.get("timestamp")))
        actor = _esc(r.get("actor") or "—")
        tid_raw = _norm_tid(r.get("trace_id"))
        tid_short = tid_raw[:16]
        tid = f'<a class="tl" onclick="jumpToTrace(\'{_esc(tid_raw)}\')" href="#">{_esc(tid_short)}</a>' if tid_raw else "—"

        if triggered:
            badge = '<span class="b be">TRIGGERED</span>'
        elif action.upper() in ("BLOCK", "DENY", "FAIL", "FAILED"):
            badge = f'<span class="b be">{_esc(action.upper())}</span>'
        else:
            badge = f'<span class="b bs">{_esc(action.upper())}</span>'

        ft = _esc(f"{r.get('rule_id','')} {r.get('event_type','')} {action} {r.get('actor','')} {r.get('trace_id','')}".lower())
        trig_attr = "true" if triggered else "false"
        rows.append(
            f'<tr class="cr" data-filter="{ft}" data-triggered="{trig_attr}">'
            f"<td>{ts_v}</td>"
            f"<td><strong>{rule}</strong></td>"
            f"<td>{badge}</td>"
            f"<td>{actor}</td>"
            f'<td class="mono sm">{tid}</td>'
            f'<td><button class="tb" onclick="td(this,1)">JSON</button>{_json_pre(r)}</td>'
            f"</tr>"
        )

    return "\n".join(rows)


def _render_metrics(recs: list[dict]) -> str:
    rows = []
    for r in recs:
        ts_v = _esc(_ts(r.get("ts")))
        name = _esc(r.get("name") or "?")
        kind = _esc(r.get("kind") or "?")
        unit = _esc(r.get("unit") or "")
        value = r.get("value")
        if isinstance(value, dict):
            vs = _esc(f"sum={value.get('sum')}, count={value.get('count')}")
        else:
            vs = _esc(str(value) if value is not None else "—")
        attrs = _esc(json.dumps(r.get("attributes") or {}, default=str))
        ft = _esc(f"{r.get('name','')} {kind}".lower())
        rows.append(
            f'<tr class="mr2" data-filter="{ft}">'
            f"<td>{ts_v}</td>"
            f"<td class='mono'><strong>{name}</strong></td>"
            f"<td>{kind}</td>"
            f'<td class="mono">{vs}</td>'
            f'<td class="sm dim">{unit}</td>'
            f'<td class="sm dim">{attrs}</td>'
            f"</tr>"
        )
    return "\n".join(rows)


# ─── New render helpers ─────────────────────────────────────────────────────────

def _compute_stats(data: dict, index: dict) -> dict:
    """Compute summary stats for the dashboard header."""
    spans = data["traces"]
    n_total = sum(1 for e in index.values() if e["audit"] or e["spans"])
    span_ids = {str(s.get("span_id") or "").strip() for s in spans}
    root_spans = [
        s for s in spans
        if not str(s.get("parent_span_id") or "").strip()
        or str(s.get("parent_span_id") or "").strip() in ("None", "0x0000000000000000", "0")
        or str(s.get("parent_span_id") or "").strip() not in span_ids
    ]
    durations = sorted(
        float(s.get("duration_ms") or 0) for s in root_spans if s.get("duration_ms") is not None
    )
    n_errors = sum(
        1 for s in spans
        if str(s.get("status") or "").upper() in ("ERROR", "FAILURE", "FAIL", "FAILED")
    )
    n_triggered = sum(1 for r in data["compliance"] if r.get("triggered"))
    avg_lat = sum(durations) / len(durations) if durations else 0
    p50 = durations[len(durations) // 2] if durations else 0
    p95 = durations[min(int(len(durations) * 0.95), len(durations) - 1)] if durations else 0
    return {
        "n_total": n_total,
        "n_errors": n_errors,
        "n_triggered": n_triggered,
        "avg_lat": avg_lat,
        "p50": p50,
        "p95": p95,
        "error_rate": (n_errors / len(root_spans) * 100) if root_spans else 0,
    }


def _render_gantt_bars(spans: list[dict]) -> str:
    """Pure-CSS Gantt timeline — each span is a bar scaled to the trace duration."""
    if not spans:
        return ""
    timed = []
    for sp in spans:
        ts_raw = sp.get("ts") or sp.get("timestamp") or ""
        try:
            ts_dt = datetime.fromisoformat(str(ts_raw).replace("Z", "+00:00"))
            start_ms: float | None = ts_dt.timestamp() * 1000
        except (ValueError, AttributeError, TypeError):
            start_ms = None
        dur = float(sp.get("duration_ms") or 0)
        timed.append((start_ms, dur, sp))
    valid_starts = [t[0] for t in timed if t[0] is not None]
    if not valid_starts:
        return ""
    t0 = min(valid_starts)
    total_dur = max((s - t0) + d for s, d, _ in timed if s is not None) or 1
    bars = []
    for start_ms, dur, sp in timed:
        offset = (start_ms - t0) if start_ms is not None else 0
        left_pct = min((offset / total_dur) * 100, 97)
        width_pct = max((dur / total_dur) * 100, 0.5)
        if left_pct + width_pct > 100:
            width_pct = 100 - left_pct
        name = sp.get("name") or sp.get("event_type") or "span"
        attrs = sp.get("attributes") or {}
        svc = (isinstance(attrs, dict) and attrs.get("service.name")) or sp.get("service_name") or ""
        label = f"{svc} · {name}" if svc else name
        status = str(sp.get("status") or "").upper()
        err_cls = " ge" if status in ("ERROR", "FAILURE", "FAIL", "FAILED") else ""
        dur_s = f"{dur:.0f}ms" if dur else ""
        bars.append(
            f'<div class="gl">'
            f'<div class="gn" title="{_esc(label)}">{_esc(label[:55])}</div>'
            f'<div class="gt"><div class="gb{err_cls}" style="left:{left_pct:.1f}%;width:{width_pct:.1f}%">'
            f'<span class="gd">{_esc(dur_s)}</span></div></div>'
            f'</div>'
        )
    total_s = f"{total_dur:.0f}ms"
    return (
        f'<div class="gc">'
        f'<div class="gh"><span class="dim" style="font-size:11px">0ms</span>'
        f'<span class="dim" style="font-size:11px">Total: {_esc(total_s)}</span></div>'
        + "".join(bars) + '</div>'
    )


def _render_error_groups(spans: list[dict], logs: list[dict]) -> str:
    """Group ERROR spans/logs by name — Sentry-style issue list."""
    groups: dict[str, dict] = {}
    for sp in spans:
        if str(sp.get("status") or "").upper() not in ("ERROR", "FAILURE", "FAIL", "FAILED"):
            continue
        name = sp.get("name") or sp.get("event_type") or "unknown"
        g = groups.setdefault(name, {"count": 0, "example_tid": "", "last_ts": ""})
        g["count"] += 1
        if not g["example_tid"]:
            g["example_tid"] = _norm_tid(sp.get("trace_id"))
        ts = str(sp.get("ts") or sp.get("timestamp") or "")
        if ts > g["last_ts"]:
            g["last_ts"] = ts
    for lg in logs:
        if str(lg.get("level") or "").upper() != "ERROR":
            continue
        name = f"[log] {lg.get('logger') or 'unknown'}"
        g = groups.setdefault(name, {"count": 0, "example_tid": "", "last_ts": ""})
        g["count"] += 1
        if not g["example_tid"]:
            g["example_tid"] = _norm_tid(lg.get("trace_id"))
        ts = str(lg.get("ts") or lg.get("timestamp") or "")
        if ts > g["last_ts"]:
            g["last_ts"] = ts
    if not groups:
        return '<div class="empty"><div class="ic">✅</div><p>No errors in this session.</p></div>'
    rows = []
    for name, g in sorted(groups.items(), key=lambda x: -x[1]["count"]):
        tid = g["example_tid"]
        tid_short = (tid[:20] + "…") if len(tid) > 20 else tid
        link = (
            f'<a class="tl" onclick="jumpToTrace(\'{_esc(tid)}\')" href="#">{_esc(tid_short)}</a>'
            if tid else "—"
        )
        rows.append(
            f'<tr><td><strong>{_esc(name)}</strong></td>'
            f'<td class="mono">{g["count"]}</td>'
            f'<td class="mono sm">{link}</td>'
            f'<td class="dim sm">{_esc(_ts(g["last_ts"]))}</td></tr>'
        )
    return (
        '<table><thead><tr>'
        '<th>Span / Event</th><th>Count</th><th>Example Trace</th><th>Last Seen</th>'
        '</tr></thead>'
        f'<tbody>{"".join(rows)}</tbody></table>'
    )


def _render_service_map(spans: list[dict]) -> str:
    """Build a service call adjacency table from span parent→child relationships."""
    span_to_svc: dict[str, str] = {}
    for sp in spans:
        sid = str(sp.get("span_id") or "").strip()
        attrs = sp.get("attributes") or {}
        svc = (isinstance(attrs, dict) and attrs.get("service.name")) or sp.get("service_name") or ""
        if sid and svc:
            span_to_svc[sid] = svc
    edges: dict[tuple, dict] = {}
    for sp in spans:
        pid = str(sp.get("parent_span_id") or "").strip()
        if not pid or pid in ("None", "0x0000000000000000", "0"):
            continue
        parent_svc = span_to_svc.get(pid, "")
        attrs = sp.get("attributes") or {}
        child_svc = (isinstance(attrs, dict) and attrs.get("service.name")) or sp.get("service_name") or ""
        if not parent_svc or not child_svc or parent_svc == child_svc:
            continue
        key = (parent_svc, child_svc)
        e = edges.setdefault(key, {"count": 0, "total_dur": 0.0, "errors": 0})
        e["count"] += 1
        e["total_dur"] += float(sp.get("duration_ms") or 0)
        if str(sp.get("status") or "").upper() in ("ERROR", "FAILURE", "FAIL", "FAILED"):
            e["errors"] += 1
    if not edges:
        return ""
    rows = []
    for (src, dst), e in sorted(edges.items()):
        avg_dur = e["total_dur"] / e["count"] if e["count"] else 0
        err_badge = (
            f'<span class="b be">{e["errors"]} err</span>' if e["errors"]
            else '<span class="b bs">OK</span>'
        )
        rows.append(
            f'<tr><td class="mono sm">{_esc(src)}</td>'
            f'<td class="dim" style="text-align:center;font-size:16px">→</td>'
            f'<td class="mono sm">{_esc(dst)}</td>'
            f'<td class="mono sm">{e["count"]}</td>'
            f'<td class="mono sm">{avg_dur:.0f}ms</td>'
            f'<td>{err_badge}</td></tr>'
        )
    return (
        '<h4 style="color:var(--dim);font-size:11px;text-transform:uppercase;letter-spacing:.5px;margin-bottom:10px">Service Call Graph</h4>'
        '<table style="max-width:640px"><thead><tr>'
        '<th>From</th><th></th><th>To</th><th>Calls</th><th>Avg Latency</th><th>Status</th>'
        '</tr></thead>'
        f'<tbody>{"".join(rows)}</tbody></table>'
    )


# ─── CSS and JS (raw strings — no brace escaping needed) ───────────────────────

_CSS = """
:root {
  --bg:#0f1117; --bg2:#1a1d27; --bg3:#252836; --bd:#2e3148;
  --tx:#e2e8f0; --dim:#718096; --ac:#6c8efb;
  --ok:#48bb78; --er:#fc8181; --wn:#f6ad55; --in:#76e4f7; --db:#9f7aea;
  --r:6px; --ff:'Segoe UI',system-ui,sans-serif; --fm:'Cascadia Code','Consolas',monospace;
}
*{box-sizing:border-box;margin:0;padding:0}
body{background:var(--bg);color:var(--tx);font-family:var(--ff);font-size:14px}

/* Header */
.hdr{background:var(--bg2);border-bottom:1px solid var(--bd);padding:12px 24px;
     display:flex;align-items:center;gap:14px;flex-wrap:wrap;position:sticky;top:0;z-index:100}
.hdr h1{font-size:17px;font-weight:600;color:var(--ac);white-space:nowrap}
.hdr .meta{color:var(--dim);font-size:12px;white-space:nowrap}

/* Stats bar */
.sbar{display:flex;gap:6px;margin-left:auto;flex-wrap:wrap}
.sc{background:var(--bg3);border:1px solid var(--bd);border-radius:8px;
    padding:6px 14px;min-width:90px;text-align:center}
.sc.alert{border-color:var(--er)}
.sv{display:block;font-size:18px;font-weight:700;font-family:var(--fm);line-height:1.2}
.sv.ok{color:var(--ok)} .sv.er{color:var(--er)} .sv.wn{color:var(--wn)}
.sl{display:block;font-size:10px;color:var(--dim);text-transform:uppercase;letter-spacing:.5px;margin-top:2px}

/* Tabs */
.tabs{background:var(--bg2);border-bottom:1px solid var(--bd);padding:0 24px;
      display:flex;gap:2px;flex-wrap:wrap;position:sticky;top:57px;z-index:99}
.tab{background:none;border:none;border-bottom:3px solid transparent;color:var(--dim);
     padding:11px 15px;cursor:pointer;font-size:13px;font-family:var(--ff);transition:all .15s}
.tab:hover{color:var(--tx)}
.tab.on{color:var(--ac);border-bottom-color:var(--ac)}

/* Panels */
.pnl{display:none;padding:18px 24px}
.pnl.on{display:block}

/* Filters */
.flt{display:flex;gap:9px;margin-bottom:14px;flex-wrap:wrap;align-items:center}
.flt input,.flt select{background:var(--bg2);border:1px solid var(--bd);color:var(--tx);
  padding:7px 11px;border-radius:var(--r);font-size:13px;font-family:var(--ff);outline:none}
.flt input{flex:1;min-width:200px}
.flt input:focus,.flt select:focus{border-color:var(--ac)}
.fc{color:var(--dim);font-size:12px}

/* Tables */
table{width:100%;border-collapse:collapse;font-size:13px}
thead th{background:var(--bg3);color:var(--dim);font-weight:500;text-align:left;
  padding:9px 11px;border-bottom:1px solid var(--bd);
  position:sticky;top:104px;z-index:10;white-space:nowrap}
tbody tr{border-bottom:1px solid var(--bd);transition:background .1s}
tbody tr:hover{background:var(--bg2)}
td{padding:8px 11px;vertical-align:top}
td.mono{font-family:var(--fm);font-size:12px}
td.sm{font-size:12px}
td.dim{color:var(--dim)}
td.lm{word-break:break-word}
.hidden{display:none!important}

/* Badges */
.b{display:inline-block;padding:2px 7px;border-radius:12px;font-size:11px;font-weight:600}
.bs{background:rgba(72,187,120,.15);color:var(--ok);border:1px solid rgba(72,187,120,.3)}
.be{background:rgba(252,129,129,.15);color:var(--er);border:1px solid rgba(252,129,129,.3)}
.bw{background:rgba(246,173,85,.15);color:var(--wn);border:1px solid rgba(246,173,85,.3)}
.bi{background:rgba(118,228,247,.15);color:var(--in);border:1px solid rgba(118,228,247,.3)}
.bd{background:rgba(159,122,234,.15);color:var(--db);border:1px solid rgba(159,122,234,.3)}
.cf{cursor:help}

/* Expand / toggle buttons */
.eb,.tb{background:var(--bg3);border:1px solid var(--bd);color:var(--dim);
  padding:2px 7px;border-radius:var(--r);cursor:pointer;font-size:11px;
  font-family:var(--ff);transition:all .15s}
.eb:hover,.tb:hover{border-color:var(--ac);color:var(--ac)}
.eb.on{color:var(--ac);border-color:var(--ac)}

/* Detail panel */
.dp{padding:14px 18px;border-left:3px solid var(--ac);background:var(--bg);
    display:flex;flex-direction:column;gap:14px}
.ds h4{color:var(--dim);font-size:11px;text-transform:uppercase;letter-spacing:.5px;margin-bottom:7px}
.ds code{font-family:var(--fm);color:var(--ac);font-size:12px;word-break:break-all}

/* Span tree */
.st{display:flex;flex-direction:column;gap:3px}
.sr{display:flex;align-items:center;gap:7px;padding:5px 8px;
    background:var(--bg2);border-radius:var(--r);border-left:2px solid var(--bd);flex-wrap:wrap}
.sn{font-weight:500;font-size:12px}
.sd{color:var(--dim);font-size:11px;font-family:var(--fm)}

/* Gantt chart */
.gc{display:flex;flex-direction:column;gap:4px;font-size:12px}
.gh{display:flex;justify-content:space-between;margin-bottom:2px;padding:0 2px}
.gl{display:grid;grid-template-columns:180px 1fr;gap:8px;align-items:center}
.gn{color:var(--dim);white-space:nowrap;overflow:hidden;text-overflow:ellipsis;font-size:11px}
.gt{position:relative;height:18px;background:var(--bg3);border-radius:3px;overflow:hidden}
.gb{position:absolute;top:0;height:100%;background:var(--ac);border-radius:3px;
    display:flex;align-items:center;padding:0 4px;min-width:2px;opacity:.85;transition:opacity .1s}
.gb:hover{opacity:1}
.gb.ge{background:var(--er)}
.gd{font-size:10px;color:#fff;white-space:nowrap;overflow:hidden;font-family:var(--fm)}

/* Attribute chips on log rows */
.acs{display:flex;flex-wrap:wrap;gap:4px;margin-top:4px}
.ac{background:rgba(108,142,251,.12);border:1px solid rgba(108,142,251,.25);
    color:var(--ac);border-radius:10px;padding:1px 7px;font-size:10px;
    cursor:pointer;font-family:var(--fm);white-space:nowrap}
.ac:hover{background:rgba(108,142,251,.25)}

/* Trace link in logs/compliance */
a.tl{color:var(--ac);cursor:pointer;font-family:var(--fm);font-size:12px;text-decoration:none}
a.tl:hover{text-decoration:underline}

/* Mini rows */
.mr{display:flex;align-items:flex-start;gap:7px;padding:4px 0;
    border-bottom:1px solid var(--bd);flex-wrap:wrap}
.mr:last-child{border-bottom:none}
.lm{flex:1;word-break:break-word}
.dim{color:var(--dim)}

/* JSON pre */
pre.jp{background:var(--bg);border:1px solid var(--bd);border-radius:var(--r);
  padding:9px;font-size:11px;font-family:var(--fm);white-space:pre-wrap;
  word-break:break-all;color:var(--dim);max-height:280px;overflow-y:auto;
  margin-top:5px;width:100%}

/* Empty state */
.empty{text-align:center;padding:50px 20px;color:var(--dim)}
.empty .ic{font-size:44px;margin-bottom:10px}

::-webkit-scrollbar{width:5px;height:5px}
::-webkit-scrollbar-track{background:var(--bg)}
::-webkit-scrollbar-thumb{background:var(--bd);border-radius:3px}
"""


_JS = """
function showTab(name, btn) {
  document.querySelectorAll('.pnl').forEach(p => p.classList.remove('on'));
  document.querySelectorAll('.tab').forEach(b => b.classList.remove('on'));
  document.getElementById('p-' + name).classList.add('on');
  btn.classList.add('on');
  _cnt(name);
}

function tr2(id, btn) {
  const row = document.getElementById(id);
  if (!row) return;
  const hidden = row.classList.toggle('hidden');
  btn.textContent = hidden ? '▶' : '▼';
  btn.classList.toggle('on', !hidden);
}

function td(btn, inline) {
  const pre = btn.nextElementSibling;
  if (!pre) return;
  pre.classList.toggle('hidden');
  btn.textContent = pre.classList.contains('hidden') ? 'JSON' : 'Hide';
}

/* Jump from a log/compliance trace_id link to the expanded Requests row */
function jumpToTrace(tid) {
  const reqBtn = document.querySelector('.tab[onclick*="requests"]');
  if (reqBtn) reqBtn.click();
  const rows = document.querySelectorAll('#req-t .rr');
  for (const row of rows) {
    const ft = row.dataset.filter || '';
    if (!ft.includes(tid.slice(0, 16).toLowerCase())) continue;
    row.scrollIntoView({behavior:'smooth', block:'center'});
    const eb = row.querySelector('.eb');
    if (eb) {
      const m = eb.getAttribute('onclick')?.match(/'([^']+)'/);
      if (m) {
        const dr = document.getElementById(m[1]);
        if (dr && dr.classList.contains('hidden')) eb.click();
      }
    }
    break;
  }
  return false;
}

/* Filter logs by a clicked attribute chip */
function filterByAttr(key, val) {
  const inp = document.getElementById('log-s');
  if (!inp) return;
  inp.value = key + '=' + val;
  filterTable('logs');
}

const TBL = {
  requests:   {t:'req-t',  rc:'rr',  s:'req-s',  ff:[{sel:'req-sf',  at:'data-status'}, {sel:'req-af', at:'data-agent'}], c:'req-c'},
  logs:       {t:'log-t',  rc:'lr',  s:'log-s',  ff:[{sel:'log-lf',  at:'data-level'}],  c:'log-c'},
  spans:      {t:'sp-t',   rc:'srt', s:'sp-s',   ff:[],                                  c:'sp-c'},
  compliance: {t:'cmp-t',  rc:'cr',  s:'cmp-s',  ff:[{sel:'cmp-tf',  at:'data-triggered'}], c:'cmp-c'},
  metrics:    {t:'mtr-t',  rc:'mr2', s:'mtr-s',  ff:[],                                  c:'mtr-c'},
  errors:     {t:'err-t',  rc:'err-r',s:'err-s', ff:[],                                  c:'err-c'},
};

function filterTable(key) {
  const cfg = TBL[key]; if (!cfg) return;
  const tbody = document.getElementById(cfg.t); if (!tbody) return;
  const search = (document.getElementById(cfg.s)?.value || '').toLowerCase();
  const fvs = cfg.ff.map(f => (document.getElementById(f.sel)?.value || '').toLowerCase());
  let vis = 0;
  const rows = tbody.querySelectorAll('.' + cfg.rc);
  rows.forEach(row => {
    const ft = (row.dataset.filter || '').toLowerCase();
    const ms = !search || ft.includes(search);
    const mf = cfg.ff.every((f, i) => {
      const v = fvs[i]; if (!v) return true;
      return (row.getAttribute(f.at) || '').toLowerCase().includes(v);
    });
    const show = ms && mf;
    row.classList.toggle('hidden', !show);
    if (cfg.rc === 'rr' && !show) {
      const btn = row.querySelector('.eb');
      if (btn) {
        const m = btn.getAttribute('onclick')?.match(/'([^']+)'/);
        if (m) { const dr = document.getElementById(m[1]); if (dr) dr.classList.add('hidden'); }
      }
    }
    if (show) vis++;
  });
  const el = document.getElementById(cfg.c);
  if (el) el.textContent = vis < rows.length ? vis + ' of ' + rows.length + ' shown' : rows.length + ' total';
}

function _cnt(name) {
  const cfg = TBL[name]; if (!cfg) return;
  const tbody = document.getElementById(cfg.t); if (!tbody) return;
  const rows = tbody.querySelectorAll('.' + cfg.rc);
  const vis = [...rows].filter(r => !r.classList.contains('hidden')).length;
  const el = document.getElementById(cfg.c);
  if (el) el.textContent = vis < rows.length ? vis + ' of ' + rows.length + ' shown' : rows.length + ' total';
}

document.addEventListener('DOMContentLoaded', () => {
  Object.keys(TBL).forEach(k => _cnt(k));
});
"""


# ─── Main renderer ──────────────────────────────────────────────────────────────

def render_html(data: dict[str, list[dict]], index: dict[str, dict]) -> str:
    """Assemble the final self-contained HTML string."""
    req_rows, all_agents, all_statuses = _render_requests(index)
    log_rows = _render_logs(data["logs"])
    span_rows = _render_spans(data["traces"])
    comp_rows = _render_compliance(data["compliance"])
    mtr_rows = _render_metrics(data["metrics"])
    err_html = _render_error_groups(data["traces"], data["logs"])
    svc_map_html = _render_service_map(data["traces"])
    stats = _compute_stats(data, index)

    n_req = stats["n_total"]
    n_logs = len(data["logs"])
    n_spans = len(data["traces"])
    n_comp = len(data["compliance"])
    n_mtr = len(data["metrics"])
    n_trig = stats["n_triggered"]
    n_err = stats["n_errors"]
    gen_ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    agent_opts = "\n".join(f'<option value="{_esc(a)}">{_esc(a)}</option>' for a in all_agents)
    status_opts = "\n".join(f'<option value="{_esc(s)}">{_esc(s)}</option>' for s in all_statuses)

    def _fmt_lat(ms: float) -> str:
        return f"{ms/1000:.2f}s" if ms >= 1000 else f"{ms:.0f}ms"

    err_rate_cls = "er" if stats["error_rate"] > 5 else ("wn" if stats["error_rate"] > 0 else "ok")
    alert_cls = " alert" if n_trig else ""

    stats_bar = (
        '<div class="sbar">'
        f'<div class="sc"><span class="sv">{n_req}</span><span class="sl">Requests</span></div>'
        f'<div class="sc"><span class="sv {err_rate_cls}">{stats["error_rate"]:.1f}%</span><span class="sl">Error Rate</span></div>'
        f'<div class="sc"><span class="sv">{_fmt_lat(stats["avg_lat"])}</span><span class="sl">Avg Latency</span></div>'
        f'<div class="sc"><span class="sv">{_fmt_lat(stats["p95"])}</span><span class="sl">P95 Latency</span></div>'
        f'<div class="sc{alert_cls}"><span class="sv {"er" if n_trig else "ok"}">{n_trig}</span><span class="sl">{"⚠ Alerts" if n_trig else "Alerts"}</span></div>'
        '</div>'
    )

    def _table(tab_id: str, cols: list[str], rows: str, row_class: str, empty_icon: str, empty_msg: str) -> str:
        if not rows:
            return f'<div class="empty"><div class="ic">{empty_icon}</div><p>{empty_msg}</p></div>'
        th = "".join(f"<th>{c}</th>" for c in cols)
        return (
            f'<table><thead><tr>{th}</tr></thead>'
            f'<tbody id="{tab_id}-t">{rows}</tbody></table>'
        )

    req_table = _table(
        "req",
        ["Trace ID", "Timestamp", "User", "Agents", "Status", "Latency", "Tokens", ""],
        req_rows, "rr", "📭",
        "No request data yet.<br><small>Run a query, then shut down to generate a session report.</small>",
    )
    log_table = _table(
        "log",
        ["Timestamp", "Level", "Logger", "Message / Attributes", "Trace ID", ""],
        log_rows, "lr", "📋",
        "No log data found.<br><small>Set OTEL_FILE_OUTPUT_DIR and OBS_PROFILE=grafana_and_file.</small>",
    )
    span_table = _table(
        "sp",
        ["Timestamp", "Name", "Kind", "Status", "Duration", "Trace ID", ""],
        span_rows, "srt", "🔍",
        "No span data found.<br><small>Set ENABLE_INSTRUMENTATION=true.</small>",
    )
    comp_table = _table(
        "cmp",
        ["Timestamp", "Rule / Event", "Result", "Actor", "Trace ID", ""],
        comp_rows, "cr", "🛡",
        "No compliance events found.",
    )
    mtr_table = _table(
        "mtr",
        ["Timestamp", "Metric", "Kind", "Value", "Unit", "Attributes"],
        mtr_rows, "mr2", "📊",
        "No metrics data found.<br><small>Metrics flush every 15–30 s. Wait for the interval.</small>",
    )

    return (
        "<!DOCTYPE html>\n"
        '<html lang="en">\n'
        "<head>\n"
        '<meta charset="UTF-8">\n'
        '<meta name="viewport" content="width=device-width,initial-scale=1.0">\n'
        "<title>Observability Report</title>\n"
        f"<style>{_CSS}</style>\n"
        "</head>\n"
        "<body>\n"

        '<div class="hdr">'
        "<h1>Observability Report</h1>"
        f'<span class="meta">Generated {gen_ts}</span>'
        f'{stats_bar}'
        "</div>\n"

        '<nav class="tabs">'
        f'<button class="tab on" onclick="showTab(\'requests\',this)">Requests ({n_req})</button>'
        f'<button class="tab" onclick="showTab(\'logs\',this)">Logs ({n_logs})</button>'
        f'<button class="tab" onclick="showTab(\'spans\',this)">Spans ({n_spans})</button>'
        f'<button class="tab" onclick="showTab(\'compliance\',this)">Compliance ({n_comp})</button>'
        f'<button class="tab" onclick="showTab(\'metrics\',this)">Metrics ({n_mtr})</button>'
        f'<button class="tab" onclick="showTab(\'errors\',this)">Errors ({n_err})</button>'
        "</nav>\n"

        '<div id="p-requests" class="pnl on">'
        + (f'<div style="margin-bottom:18px">{svc_map_html}</div>' if svc_map_html else "")
        + '<div class="flt">'
        '<input id="req-s" placeholder="Search trace ID, user, agent…" oninput="filterTable(\'requests\')">'
        f'<select id="req-sf" onchange="filterTable(\'requests\')"><option value="">All statuses</option>{status_opts}</select>'
        f'<select id="req-af" onchange="filterTable(\'requests\')"><option value="">All agents</option>{agent_opts}</select>'
        '<span class="fc" id="req-c"></span>'
        "</div>"
        f"{req_table}"
        "</div>\n"

        '<div id="p-logs" class="pnl">'
        '<div class="flt">'
        '<input id="log-s" placeholder="Search message, logger, trace ID, attribute…" oninput="filterTable(\'logs\')">'
        '<select id="log-lf" onchange="filterTable(\'logs\')">'
        '<option value="">All levels</option>'
        '<option value="ERROR">ERROR</option><option value="WARNING">WARNING</option>'
        '<option value="WARN">WARN</option><option value="INFO">INFO</option><option value="DEBUG">DEBUG</option>'
        "</select>"
        '<span class="fc" id="log-c"></span>'
        "</div>"
        f"{log_table}"
        "</div>\n"

        '<div id="p-spans" class="pnl">'
        '<div class="flt">'
        '<input id="sp-s" placeholder="Search span name, trace ID…" oninput="filterTable(\'spans\')">'
        '<span class="fc" id="sp-c"></span>'
        "</div>"
        f"{span_table}"
        "</div>\n"

        '<div id="p-compliance" class="pnl">'
        '<div class="flt">'
        '<input id="cmp-s" placeholder="Search rule, actor, trace ID…" oninput="filterTable(\'compliance\')">'
        '<select id="cmp-tf" onchange="filterTable(\'compliance\')">'
        '<option value="">All events</option><option value="true">Triggered only</option><option value="false">Passed only</option>'
        "</select>"
        '<span class="fc" id="cmp-c"></span>'
        "</div>"
        f"{comp_table}"
        "</div>\n"

        '<div id="p-metrics" class="pnl">'
        '<div class="flt">'
        '<input id="mtr-s" placeholder="Search metric name…" oninput="filterTable(\'metrics\')">'
        '<span class="fc" id="mtr-c"></span>'
        "</div>"
        f"{mtr_table}"
        "</div>\n"

        '<div id="p-errors" class="pnl">'
        '<p style="color:var(--dim);font-size:12px;margin-bottom:14px">'
        'All spans and log entries with ERROR status, grouped by name. '
        'Click a trace ID to jump to the Requests tab.</p>'
        f"{err_html}"
        "</div>\n"

        f"<script>{_JS}</script>\n"
        "</body>\n</html>"
    )


# ─── Public API ─────────────────────────────────────────────────────────────────

def generate_html_report(
    config: Any = None,
    otel_dir: str = "",
    out_path: str = "",
) -> str:
    """Generate a self-contained HTML observability report from JSONL sources.

    Reads exclusively from the observability folder (otel_dir).
    Output: {OTEL_FILE_OUTPUT_DIR}/reports/{YYYYMMDD}_{HHMMSS}_{service_name}.html

    Args:
        config: Optional TelemetryConfig — resolves file_output_dir and the output
                path via config.report_path(). Callers typically only need this arg.
        otel_dir: Override the OTel file output dir (contains traces/, metrics/, logs/).
                  Falls back to config.file_output_dir, then OTEL_FILE_OUTPUT_DIR env var.
        out_path: Override output HTML path. Defaults to config.report_path() so the
                  report lands inside the observability dir alongside the JSONL files.

    Returns:
        Absolute path of the generated HTML file.
    """
    if not otel_dir and config is not None:
        otel_dir = getattr(config, "file_output_dir", "")
    if not otel_dir:
        otel_dir = os.getenv("OTEL_FILE_OUTPUT_DIR", "")
    if otel_dir:
        otel_dir = os.path.abspath(otel_dir)

    session_ts = getattr(config, "_file_ts", "") if config is not None else ""

    if not out_path:
        if config is not None and hasattr(config, "report_path"):
            out_path = config.report_path()
        elif otel_dir:
            ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
            out_path = os.path.join(otel_dir, "reports", f"{ts}_report.html")
        else:
            out_path = "observability_report.html"

    data = load_all_sources(otel_dir=otel_dir, session_ts=session_ts)
    index = build_request_index(data)
    html = render_html(data, index)

    out_path = os.path.abspath(out_path)
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(html)

    return out_path


# ─── CLI entry point ────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(
        prog="beacon-report",
        description="Generate a self-contained HTML observability report from beacon_kit JSONL files.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Examples:\n"
            "  beacon-report                              # auto-detect from OTEL_FILE_OUTPUT_DIR\n"
            "  beacon-report --dir ../observability       # specify OTel output directory\n"
            "  beacon-report --dir ../observability --out report.html\n"
        ),
    )
    parser.add_argument("--dir", dest="otel_dir", default="",
                        help="OTel file output dir (contains traces/, metrics/, logs/ subdirs)")
    parser.add_argument("--out", dest="out_path", default="",
                        help="Output HTML path (default: {otel_dir}/reports/{ts}_report.html)")
    args = parser.parse_args()

    out = generate_html_report(otel_dir=args.otel_dir, out_path=args.out_path)

    otel_abs = os.path.abspath(args.otel_dir) if args.otel_dir else os.path.abspath(os.getenv("OTEL_FILE_OUTPUT_DIR", ""))
    data = load_all_sources(otel_dir=otel_abs if os.path.isdir(otel_abs) else "")
    n_trig = sum(1 for r in data["compliance"] if r.get("triggered"))
    print(f"Report generated: {out}")
    print(f"  Spans:             {len(data['traces'])}")
    print(f"  Logs:              {len(data['logs'])}")
    print(f"  Compliance events: {len(data['compliance'])}")
    print(f"  Metrics:           {len(data['metrics'])}")
    if n_trig:
        print(f"  ⚠  Guardrail triggers: {n_trig}")


if __name__ == "__main__":
    main()
