"""LLM Reasoning Capture — parses explicit reasoning markers from agent responses.

Agents are prompted to embed structured <llm_reasoning> JSON blocks at key
decision points (intent routing, synthesis, safety review). This module
extracts those blocks so they can be displayed in the UI "AI Reasoning" tab.

Design notes
------------
- Agents embed markers in their response *text*. Because all agent communication
  travels through the MAF A2A text transport, no framework internals need to be
  touched — the orchestrator simply parses the raw response string.
- Parsing is fault-tolerant: malformed JSON falls back to {"raw": text} so a
  badly-formatted block is captured rather than silently dropped.
- Markers are stripped from the displayed answer so users never see them.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple


# Matches <llm_reasoning>...</llm_reasoning> including newlines inside the block.
# Both tags tolerate optional whitespace around the tag name (e.g. "< llm_reasoning>",
# "< /llm_reasoning>") which some LLMs emit.
_REASONING_RE = re.compile(
    r"<\s*llm_reasoning\s*>(.*?)<\s*/\s*llm_reasoning\s*>",
    re.DOTALL | re.IGNORECASE,
)


@dataclass
class LLMReasoningEntry:
    """One captured LLM reasoning block from an agent response."""

    agent: str            # "compliance" | "price_assist" | "data" | "rag"
    phase: str            # "safety_review" | "intent_routing" | "synthesis" | ...
    data: Dict[str, Any] = field(default_factory=dict)
    timestamp: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )

    def to_dict(self) -> dict:
        return asdict(self)


def strip_reasoning_markers(text: str) -> str:
    """Strip all <llm_reasoning> blocks from *text* without capturing entries.

    Used as a safety-net pass at the final output stage to guarantee that no
    marker block ever reaches the user even if an upstream extraction call was
    skipped (e.g. on a retry path) or returned without stripping.
    """
    return _REASONING_RE.sub("", text).strip()


def extract_reasoning(
    text: str,
    agent: str,
) -> Tuple[List[LLMReasoningEntry], str]:
    """Parse all <llm_reasoning> blocks from *text*.

    Args:
        text:  Raw agent response (may contain zero or more reasoning blocks).
        agent: Logical agent name used to label entries (e.g. "price_assist").

    Returns:
        ``(entries, clean_text)`` where *clean_text* has all reasoning markers
        stripped so it can be stored or displayed without the raw JSON.
    """
    entries: List[LLMReasoningEntry] = []
    for match in _REASONING_RE.finditer(text):
        raw = match.group(1).strip()
        try:
            data: Dict[str, Any] = json.loads(raw)
        except (json.JSONDecodeError, ValueError):
            data = {"raw": raw}
        phase = data.get("phase", "unknown") if isinstance(data, dict) else "unknown"
        # Agents may self-identify with an "agent" key so that reasoning blocks
        # from Data/RAG agents are correctly attributed even when they travel
        # through PriceAssist's tool results and are extracted at the orchestrator.
        effective_agent = data.get("agent", agent) if isinstance(data, dict) else agent
        entries.append(LLMReasoningEntry(agent=effective_agent, phase=phase, data=data))

    # Remove all marker blocks from the visible answer.
    clean = _REASONING_RE.sub("", text).strip()
    return entries, clean
