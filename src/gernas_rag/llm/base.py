"""LLM abstract base class."""

from abc import ABC, abstractmethod
from dataclasses import dataclass


@dataclass
class Message:
    role: str  # 'system' | 'user' | 'assistant'
    content: str


class BaseLLM(ABC):
    """All LLM providers must implement this interface."""

    @abstractmethod
    async def generate(self, messages: list[Message]) -> str:
        """Generate a completion from a list of chat messages."""
        ...

    @abstractmethod
    async def health_check(self) -> bool:
        """Return True if the provider is reachable / configured."""
        ...
