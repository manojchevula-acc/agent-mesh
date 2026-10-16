"""Span conformance against the attribute registry (WP1-CTX-07 step 2, WP1-CORE-07 step 2).

Rules, each producing machine-readable violations:

* ``required``      — every span carries the envelope and the attributes its
  operation requires (``REQUIRED_ATTRIBUTES``); envelope keys may sit on the resource;
* ``unregistered``  — no ``gernas.*`` key outside the registry;
* ``restricted``    — no field whose registry action is ``drop`` (raw prompt,
  completion, user id ...);
* ``enum``          — enumerated fields only hold registered values;
* ``cardinality``   — bounded fields stay within ``max_bounded_values`` distinct values.

Inputs: ``ReadableSpan`` objects, Gernas SDK JSONL (``FileSpanExporter``) or the
OTel Collector ``file`` exporter's OTLP-JSON output.
"""
from __future__ import annotations

import json
from collections import defaultdict
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Iterable

from gernas.attributes import ENVELOPE, REGISTRY, REQUIRED_ATTRIBUTES, Ctx, SpanName

_GERNAS_PREFIX = Ctx.AGENT_ID.split(".", 1)[0] + "."
_TEMPLATED = {SpanName.TOOL.split("{", 1)[0]: SpanName.TOOL, SpanName.MEMORY.split("{", 1)[0]: SpanName.MEMORY}


@dataclass
class NormalSpan:
    name: str
    trace_id: str
    span_id: str
    parent_span_id: str | None
    status: str
    attributes: dict[str, Any]
    resource: dict[str, Any] = field(default_factory=dict)

    @property
    def operation(self) -> str | None:
        op = self.attributes.get(Ctx.OPERATION)
        if op:
            return str(op)
        if self.name in REQUIRED_ATTRIBUTES:
            return self.name
        for prefix, template in _TEMPLATED.items():
            if self.name.startswith(prefix):
                return template
        return None


@dataclass
class Violation:
    rule: str
    span: str
    key: str
    detail: str
    trace_id: str = ""


@dataclass
class Report:
    spans_checked: int = 0
    gernas_spans: int = 0
    operations: dict[str, int] = field(default_factory=dict)
    violations: list[Violation] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        return not self.violations

    def to_dict(self) -> dict[str, Any]:
        out = asdict(self)
        out["passed"] = self.passed
        return out


# ── normalisation ────────────────────────────────────────────────────────────

def _otlp_value(v: dict) -> Any:
    for kind in ("stringValue", "boolValue", "intValue", "doubleValue"):
        if kind in v:
            return int(v[kind]) if kind == "intValue" else v[kind]
    if "arrayValue" in v:
        return [_otlp_value(x) for x in v["arrayValue"].get("values", [])]
    return None


def _otlp_attrs(items: list | None) -> dict[str, Any]:
    return {a["key"]: _otlp_value(a.get("value", {})) for a in (items or [])}


def from_readable(spans: Iterable[Any]) -> list[NormalSpan]:
    out = []
    for s in spans:
        out.append(NormalSpan(
            name=s.name, trace_id=format(s.context.trace_id, "032x"), span_id=format(s.context.span_id, "016x"),
            parent_span_id=format(s.parent.span_id, "016x") if s.parent else None,
            status=s.status.status_code.name, attributes=dict(s.attributes or {}),
            resource=dict(s.resource.attributes) if s.resource else {},
        ))
    return out


def load_file(path: str | Path) -> list[NormalSpan]:
    """Read Collector OTLP-JSON lines or Gernas SDK span JSONL."""
    spans: list[NormalSpan] = []
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        doc = json.loads(line)
        if "resourceSpans" in doc:
            for rs in doc["resourceSpans"]:
                resource = _otlp_attrs(rs.get("resource", {}).get("attributes"))
                for ss in rs.get("scopeSpans", []):
                    for sp in ss.get("spans", []):
                        code = (sp.get("status") or {}).get("code", 0)
                        status = {0: "UNSET", 1: "OK", 2: "ERROR", "STATUS_CODE_OK": "OK",
                                  "STATUS_CODE_ERROR": "ERROR"}.get(code, "UNSET")
                        spans.append(NormalSpan(sp.get("name", ""), sp.get("traceId", ""), sp.get("spanId", ""),
                                                sp.get("parentSpanId") or None, status,
                                                _otlp_attrs(sp.get("attributes")), resource))
        elif "attributes" in doc and "name" in doc:
            spans.append(NormalSpan(doc["name"], str(doc.get("trace_id", "")).removeprefix("0x"),
                                    str(doc.get("span_id", "")).removeprefix("0x"),
                                    (doc.get("parent_span_id") or "").removeprefix("0x") or None,
                                    str(doc.get("status") or "UNSET"), doc.get("attributes") or {},
                                    doc.get("resource") or {}))
    return spans


# ── checks ───────────────────────────────────────────────────────────────────

def check(spans: Iterable[NormalSpan], *, max_bounded_values: int = 500,
          require_gernas_only: bool = False) -> Report:
    report = Report()
    distinct: dict[str, set] = defaultdict(set)
    ops: dict[str, int] = defaultdict(int)
    for span in spans:
        report.spans_checked += 1
        attrs = span.attributes
        op = span.operation
        is_gernas = op is not None and Ctx.OPERATION in attrs
        if not is_gernas and require_gernas_only:
            continue
        if is_gernas:
            report.gernas_spans += 1
            ops[op] += 1
            for key in (*ENVELOPE, *REQUIRED_ATTRIBUTES.get(op, ())):
                if key not in attrs and key not in span.resource:
                    report.violations.append(Violation("required", span.name, key,
                                                       f"{op} span is missing required attribute", span.trace_id))
        for key, value in attrs.items():
            spec = REGISTRY.get(key)
            if spec is None:
                if key.startswith(_GERNAS_PREFIX):
                    report.violations.append(Violation("unregistered", span.name, key,
                                                       "attribute is not in the registry", span.trace_id))
                continue
            if spec.action == "drop":
                report.violations.append(Violation("restricted", span.name, key,
                                                   f"{spec.sensitivity} field must never be emitted", span.trace_id))
            if spec.enum:
                values = value if isinstance(value, (list, tuple)) else [value]
                for v in values:
                    normalised = str(v).lower() if isinstance(v, bool) else str(v)
                    if normalised not in spec.enum:
                        report.violations.append(Violation("enum", span.name, key,
                                                           f"value {v!r} not in {list(spec.enum)}", span.trace_id))
            if spec.cardinality == "bounded":
                distinct[key].add(str(value))
    for key, values in distinct.items():
        if len(values) > max_bounded_values:
            report.violations.append(Violation("cardinality", "*", key,
                                               f"{len(values)} distinct values > {max_bounded_values} (bounded field)"))
    report.operations = dict(ops)
    return report
