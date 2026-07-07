"""Explicit intent / domain / entity classification (Component A).

Advisory-first: the result is logged and stored in state; it only alters routing
(the out_of_scope short-circuit) once ``intent_detection_enforced`` is set. Uses the
small/fast model configured for ``Step.INTENT``. See the production-upgrade docs, §5.1.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field

from sql_agent.agent.prompts import INTENT_PRECLASSIFIER_PROMPT
from sql_agent.llm import Step, get_llm
from sql_agent.logging_config import get_logger

log = get_logger("intent")


@dataclass(frozen=True)
class Intent:
    tier: str
    domain: str = ""
    entities: dict = field(default_factory=dict)
    missing: list = field(default_factory=list)
    tables_hint: list = field(default_factory=list)
    confidence: float = 0.0
    reason: str = ""

    def as_dict(self) -> dict:
        return asdict(self)


def _parse(text: str) -> dict:
    """Extract the first JSON object from a model response, tolerating stray prose."""
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end == -1 or end < start:
        raise ValueError("no JSON object found")
    return json.loads(text[start : end + 1])


def classify(question: str) -> Intent:
    """Classify a single user question. Never raises — falls back to a safe default
    (full_dynamic) so the pipeline continues even if the classifier misbehaves."""
    try:
        raw = get_llm(Step.INTENT).invoke(
            INTENT_PRECLASSIFIER_PROMPT.format(user_request=question)
        )
        text = raw.content if hasattr(raw, "content") else str(raw)
        data = _parse(text)
    except Exception as exc:  # noqa: BLE001 — advisory stage must never break the turn
        log.warning("INTENT classify failed | %s | defaulting to full_dynamic", exc)
        return Intent(tier="full_dynamic", reason="unparseable; safe default")

    return Intent(
        tier=data.get("tier", "full_dynamic"),
        domain=data.get("domain", ""),
        entities=data.get("entities", {}) or {},
        missing=data.get("missing", []) or [],
        tables_hint=data.get("tables_hint", []) or [],
        confidence=float(data.get("confidence", 0.0) or 0.0),
        reason=data.get("reason", ""),
    )
