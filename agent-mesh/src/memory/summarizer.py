"""Rolling LLM summarization for multi-turn conversation memory.

After each turn the orchestrator fires ``summarize_and_persist`` as a
non-blocking asyncio task.  The task calls Groq (the same OpenAI-compat
endpoint the rest of the mesh uses) with a compact prompt, then writes the
updated summary back to the JSONL store as a ``type=summary`` record.

On the next turn ``ConversationStore.load_with_summary`` reads this record
and returns ``(summary_str, [])`` so the prompt stays small regardless of
session length.
"""
from __future__ import annotations

import asyncio
import logging
import httpx

from src.config import Config

_log = logging.getLogger(__name__)

_SYSTEM_PROMPT = (
    "You are a conversation summarizer. "
    "Given the running summary and the latest exchange, produce a concise updated summary "
    "(≤200 words) capturing all key facts, decisions, and user intent. "
    "Be factual and concise. Do not add commentary or greetings."
)


async def _call_groq(user_prompt: str) -> str:
    """Send a single summarization request to the Groq endpoint and return the text."""
    url = f"{Config.LLM_BASE_URL.rstrip('/')}/chat/completions"
    model = Config.SUMMARY_MODEL or Config.GROQ_MODEL
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": _SYSTEM_PROMPT},
            {"role": "user", "content": user_prompt},
        ],
        "max_tokens": 300,
        "temperature": 0.3,
    }
    headers = {
        "Authorization": f"Bearer {Config.GROQ_API_KEY}",
        "Content-Type": "application/json",
    }
    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.post(url, json=payload, headers=headers)
        resp.raise_for_status()
        data = resp.json()
    return data["choices"][0]["message"]["content"].strip()


async def summarize_and_persist(
    session_id: str,
    prior_summary: str,
    user_msg: str,
    assistant_msg: str,
) -> None:
    """Build an updated rolling summary and persist it to the store.

    Designed to run as ``asyncio.create_task(...)`` — any error is logged
    and swallowed so it never surfaces to the user.
    """
    try:
        exchange_block = f"User: {user_msg}\nAssistant: {assistant_msg}"
        if prior_summary:
            prompt = (
                f"Running summary:\n{prior_summary}\n\n"
                f"Latest exchange:\n{exchange_block}\n\n"
                "Produce an updated concise summary."
            )
        else:
            prompt = (
                f"First exchange in a new session:\n{exchange_block}\n\n"
                "Produce a concise summary of this exchange."
            )

        new_summary = await _call_groq(prompt)

        # Lazy import to avoid circular deps
        from src.memory.conversation_store import ConversationStore
        store = ConversationStore()
        store.save_summary(session_id, new_summary)
        _log.debug("summary persisted session=%s len=%d", session_id, len(new_summary))
    except Exception as exc:
        _log.warning("summarization failed session=%s: %s", session_id, exc)
