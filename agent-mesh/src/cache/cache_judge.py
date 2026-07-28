"""LLM judge for gray-zone cache validation.

When cosine similarity falls between CACHE_MISS_THRESHOLD and
CACHE_SIMILARITY_THRESHOLD the embeddings alone are not decisive.
This module calls a small LLM (same provider/key as the rest of the mesh)
to make a binary decision with a one-line reason: is the cached answer still
valid for the new query?

The reason string is surfaced in the UI (amber banner + pipeline step panel)
so users and auditors can see why a gray-zone candidate was accepted or rejected.

Uses the same httpx raw-HTTP pattern as src/memory/summarizer.py so there
is no new dependency — just the existing GROQ_API_KEY / LLM_BASE_URL config.
"""
from __future__ import annotations

import logging
from typing import Tuple

import httpx

from src.config import Config

_log = logging.getLogger("agent_mesh.cache.judge")

_JUDGE_PROMPT = """\
You are a cache validation assistant for a financial services AI system.

User role: {role}
New query: "{new_query}"
Original cached query: "{cached_query}"
Cached answer (excerpt):
\"\"\"
{cached_answer_excerpt}
\"\"\"

Task: Decide whether the cached answer fully and accurately addresses the new query for this user role.
Consider: same intent, same scope, same subject — minor rephrasing is fine.
Reject if: different entity, different time scope, different intent, or the answer would not satisfy the new query.

Reply in this exact format — decision first, then a colon, then one short reason (max 12 words):
YES: <one short reason>
or
NO: <one short reason>

Examples:
YES: same customer and intent, only wording differs
NO: asks about a different time period than cached answer"""


async def llm_cache_judge(
    new_query: str,
    cached_query: str,
    cached_answer: str,
    role: str,
) -> Tuple[bool, str]:
    """Return (decision, reason) where decision=True means the cached answer is valid.

    reason is a short human-readable sentence suitable for display in the UI.
    On any error or timeout degrades gracefully to (False, "") — treated as MISS.
    """
    if not Config.CACHE_JUDGE_ENABLED:
        return False, ""

    prompt = _JUDGE_PROMPT.format(
        role=role,
        new_query=new_query,
        cached_query=cached_query,
        cached_answer_excerpt=cached_answer[:400],
    )

    url = f"{Config.LLM_BASE_URL.rstrip('/')}/chat/completions"
    payload = {
        "model": Config.CACHE_JUDGE_MODEL,
        "messages": [{"role": "user", "content": prompt}],
        "max_tokens": 60,
        "temperature": 0,
    }
    headers = {
        "Authorization": f"Bearer {Config.GROQ_API_KEY}",
        "Content-Type": "application/json",
    }

    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.post(url, json=payload, headers=headers)
            resp.raise_for_status()
            raw = resp.json()["choices"][0]["message"]["content"].strip()
            decision, reason = _parse_judge_response(raw)
            _log.debug(
                "cache judge: role=%s decision=%s reason=%r sim_query=%r",
                role, "HIT" if decision else "MISS", reason, new_query[:60],
            )
            return decision, reason
    except Exception as exc:
        _log.warning("cache judge error (degrading to MISS): %s", exc)
        return False, ""


def _parse_judge_response(raw: str) -> Tuple[bool, str]:
    """Parse 'YES: reason' or 'NO: reason' from the LLM response.

    Tolerates missing colon, extra whitespace, and lowercase variants.
    Falls back to plain YES/NO detection if the format is unexpected.
    """
    upper = raw.upper()
    if ":" in raw:
        prefix, _, rest = raw.partition(":")
        decision = prefix.strip().upper().startswith("YES")
        reason = rest.strip()
    else:
        decision = upper.startswith("YES")
        reason = ""
    # Cap reason length for safety
    reason = reason[:120] if reason else ""
    return decision, reason
