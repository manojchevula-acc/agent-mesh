"""HuggingFace local LLM implementation."""

import asyncio
from functools import partial
from typing import Any

from ..config.llm import LLMConfig
from ..utils.logging import get_logger
from .base import BaseLLM, Message

logger = get_logger(__name__)


class HuggingFaceLLM(BaseLLM):
    """Local HuggingFace transformers pipeline.

    Generation is CPU/GPU bound, so it runs in a thread pool executor. The model
    is lazy-loaded on first use.
    """

    def __init__(self, config: LLMConfig) -> None:
        self._config = config
        self._pipeline: Any | None = None
        logger.info("Initialising HuggingFace LLM", model=config.hf_model_id, device=config.hf_device)

    def _load(self) -> None:
        if self._pipeline is None:
            from transformers import pipeline

            self._pipeline = pipeline(
                "text-generation",
                model=self._config.hf_model_id,
                device=self._config.hf_device,
            )

    def _sync_generate(self, messages: list[Message]) -> str:
        self._load()
        assert self._pipeline is not None
        chat = [{"role": m.role, "content": m.content} for m in messages]
        outputs = self._pipeline(
            chat,
            max_new_tokens=self._config.max_tokens,
            temperature=max(self._config.temperature, 1e-4),
            do_sample=self._config.temperature > 0,
            return_full_text=False,
        )
        generated = outputs[0]["generated_text"]
        if isinstance(generated, list):  # chat-format output
            return generated[-1]["content"]
        return str(generated)

    async def generate(self, messages: list[Message]) -> str:
        loop = asyncio.get_running_loop()
        content = await loop.run_in_executor(None, partial(self._sync_generate, messages))
        logger.info("HuggingFace generation complete", model=self._config.hf_model_id, chars=len(content))
        return content

    async def health_check(self) -> bool:
        return True
