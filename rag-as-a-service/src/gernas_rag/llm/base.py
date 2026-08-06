"""LLM abstract base class.

``Message.content`` is a union of ``str`` and a list of content parts. The union
is what keeps every existing ``Message(role=..., content="...")`` construction
valid, and it means a text-only request still serialises to a plain string —
byte-identical to what this service sends today.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Literal, Union


@dataclass
class TextPart:
    text: str
    type: Literal["text"] = "text"


@dataclass
class ImagePart:
    """An image as an inline base64 data URI.

    We never send a bare URL: assets sit behind auth and must not be made
    anonymously fetchable just to satisfy an API contract.
    """

    data_uri: str  # "data:image/jpeg;base64,...."
    type: Literal["image"] = "image"

    def __repr__(self) -> str:  # Keep base64 blobs out of logs and tracebacks.
        return f"ImagePart(bytes~{len(self.data_uri) * 3 // 4})"


ContentPart = Union[TextPart, ImagePart]


@dataclass
class Message:
    role: str  # 'system' | 'user' | 'assistant'
    content: Union[str, list[ContentPart]]

    @property
    def has_images(self) -> bool:
        return isinstance(self.content, list) and any(
            isinstance(p, ImagePart) for p in self.content
        )

    def text_only(self) -> "Message":
        """Drop image parts, keeping the interleaved text descriptors.

        Because each image is preceded by its own ``[IN]`` label, the result is a
        coherent caption-only prompt — exactly the text-only design.
        """
        if not isinstance(self.content, list):
            return self
        return Message(
            role=self.role, content=[p for p in self.content if isinstance(p, TextPart)]
        )

    def flatten(self) -> str:
        """Plain-text rendering, for providers that cannot take parts."""
        if isinstance(self.content, str):
            return self.content
        return "\n".join(p.text for p in self.content if isinstance(p, TextPart))


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

    @property
    def supports_vision(self) -> bool:
        """Providers that accept :class:`ImagePart` override this."""
        return False


def reject_images(messages: list[Message], model: str) -> None:
    """Guard for text-only providers.

    Raises rather than silently stringifying an image, which would produce a
    confidently wrong answer with no signal that anything went missing.
    """
    if any(m.has_images for m in messages):
        raise ValueError(
            f"Model '{model}' cannot accept image input. Route through "
            "VisionRouter, or set llm.vision_enabled=false."
        )
