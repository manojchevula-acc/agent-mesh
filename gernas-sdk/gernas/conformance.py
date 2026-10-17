"""Span conformance against the GERNAS semantic conventions (registry).

Rules, each producing machine-readable violations:

* ``required``      — every GERNAS span carries the envelope and the attributes its
  operation requires (``REQUIRED_ATTRIBUTES``); envelope keys may sit on the resource;
* ``run_context``   — spans below an ``agent.run`` carry ``agent_id`` / ``agent_run_id``;
* ``duplicate``     — a span is not a same-operation, same-identity child of its parent
  (double instrumentation, e.g. decorator + framework adapter on one call);
* ``unregistered``  — no ``gernas.*`` key outside the registry;
* ``restricted``    — no denylisted key and no field whose registry action is ``drop``;
* ``enum``          — enumerated fields only hold registered values;
* ``cardinality``   — bounded fields stay within ``max_bounded_values`` distinct values.

Inputs: ``ReadableSpan`` objects or the OTel Collector ``file`` exporter's OTLP-JSON output.
"""

from __future__ import annotations

import json
from collections import defaultdict
from collections.abc import Iterable
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from gernas.attributes import DENYLIST, ENVELOPE, REGISTRY, REQUIRED_ATTRIBUTES, RUN_CONTEXT, Ctx, SpanName

_GERNAS_PREFIX = Ctx.AGENT_ID.split(".", 1)[0] + "."
# attributes that identify "the same call" per operation (duplicate-emission rule)
_IDENTITY = {
    SpanName.AGENT: (Ctx.AGENT_ID,),
    SpanName.STEP: (Ctx.STEP_ID,),
    SpanName.LLM: (Ctx.MODEL_ID,),
    SpanName.TOOL: (Ctx.TOOL_ID,),
    SpanName.RETRIEVAL: (Ctx.INDEX_ID,),
    SpanName.GUARDRAIL: (Ctx.POLICY_ID,),
}
# operations that sit above the agent run and need no run context
_OUTSIDE_RUN = (SpanName.USE_CASE, SpanName.HTTP, SpanName.AGENT)


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
        return self.name if self.name in REQUIRED_ATTRIBUTES else None


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
        out.append(
            NormalSpan(
                name=s.name,
                trace_id=format(s.context.trace_id, "032x"),
                span_id=format(s.context.span_id, "016x"),
                parent_span_id=format(s.parent.span_id, "016x") if s.parent else None,
                status=s.status.status_code.name,
                attributes=dict(s.attributes or {}),
                resource=dict(s.resource.attributes) if s.resource else {},
            )
        )
    return out


def load_file(path: str | Path) -> list[NormalSpan]:
    """Read Collector ``file`` exporter OTLP-JSON lines."""
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
                        status = {
                            0: "UNSET",
                            1: "OK",
                            2: "ERROR",
                            "STATUS_CODE_OK": "OK",
                            "STATUS_CODE_ERROR": "ERROR",
                        }.get(code, "UNSET")
                        spans.append(
                            NormalSpan(
                                sp.get("name", ""),
                                sp.get("traceId", ""),
                                sp.get("spanId", ""),
                                sp.get("parentSpanId") or None,
                                status,
                                _otlp_attrs(sp.get("attributes")),
                                resource,
                            )
                        )
    return spans


# ── checks ───────────────────────────────────────────────────────────────────


def check(spans: Iterable[NormalSpan], *, max_bounded_values: int = 500, require_gernas_only: bool = False) -> Report:
    report = Report()
    spans = list(spans)
    by_id = {(s.trace_id, s.span_id): s for s in spans}
    distinct: dict[str, set] = defaultdict(set)
    ops: dict[str, int] = defaultdict(int)

    def add(rule: str, span: NormalSpan, key: str, detail: str) -> None:
        report.violations.append(Violation(rule, span.name, key, detail, span.trace_id))

    def parent_of(span: NormalSpan) -> NormalSpan | None:
        return by_id.get((span.trace_id, span.parent_span_id)) if span.parent_span_id else None

    def inside_run(span: NormalSpan) -> bool:
        node, hops = parent_of(span), 0
        while node is not None and hops < 256:
            if node.operation == SpanName.AGENT and Ctx.OPERATION in node.attributes:
                return True
            node, hops = parent_of(node), hops + 1
        return False

    for span in spans:
        report.spans_checked += 1
        attrs = span.attributes
        op = span.operation
        is_gernas = op is not None and Ctx.OPERATION in attrs
        if not is_gernas and require_gernas_only:
            continue
        if op is not None and is_gernas:
            report.gernas_spans += 1
            ops[op] += 1
            for key in (*ENVELOPE, *REQUIRED_ATTRIBUTES.get(op, ())):
                if key not in attrs and key not in span.resource:
                    add("required", span, key, f"{op} span is missing required attribute")
            if op not in _OUTSIDE_RUN and inside_run(span):
                for key in RUN_CONTEXT:
                    if key not in attrs:
                        add("run_context", span, key, f"{op} span inside an agent run lacks run context")
            parent = parent_of(span)
            identity = _IDENTITY.get(op)
            if (
                parent is not None
                and identity
                and parent.operation == op
                and Ctx.OPERATION in parent.attributes
                and all(attrs.get(k) is not None and attrs.get(k) == parent.attributes.get(k) for k in identity)
            ):
                add("duplicate", span, identity[0], f"{op} emitted twice for the same call (nested duplicate)")
        for key, value in attrs.items():
            if key in DENYLIST:
                add("restricted", span, key, "denylisted key must never be emitted")
                continue
            spec = REGISTRY.get(key)
            if spec is None:
                if key.startswith(_GERNAS_PREFIX):
                    add("unregistered", span, key, "attribute is not in the registry")
                continue
            if spec.action == "drop":
                add("restricted", span, key, f"{spec.sensitivity} field must never be emitted")
            if spec.enum:
                values = value if isinstance(value, (list, tuple)) else [value]
                for v in values:
                    normalised = str(v).lower() if isinstance(v, bool) else str(v)
                    if normalised not in spec.enum:
                        add("enum", span, key, f"value {v!r} not in {list(spec.enum)}")
            if spec.cardinality == "bounded":
                distinct[key].add(str(value))
    for key, seen in distinct.items():
        if len(seen) > max_bounded_values:
            report.violations.append(
                Violation(
                    "cardinality", "*", key, f"{len(seen)} distinct values > {max_bounded_values} (bounded field)"
                )
            )
    report.operations = dict(ops)
    return report
