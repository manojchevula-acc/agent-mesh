"""LLM abstract base class + multimodal content parts.

``Message.content`` is normally a plain string. For the hydration path (§13) it
may also be a list of content parts (text + image). The string form is preserved
so every existing caller is unaffected; text-only providers flatten any image
parts away and answer from text, matching the graceful-degradation ethos used
elsewhere in the codebase.
"""

import base64
from abc import ABC, abstractmethod
from dataclasses import dataclass


@dataclass
class TextPart:
    text: str
    type: str = "text"


@dataclass
class ImagePart:
    bytes: bytes
    mime_type: str
    ref: str | None = None  # artifact_ref, preserved for citation.
    type: str = "image"


ContentPart = TextPart | ImagePart


@dataclass
class Message:
    role: str  # 'system' | 'user' | 'assistant'
    content: str | list[ContentPart]


def flatten_text(content: str | list[ContentPart]) -> str:
    """Collapse content to plain text, dropping images. Used by text-only providers."""
    if isinstance(content, str):
        return content
    return "\n".join(p.text for p in content if isinstance(p, TextPart))


def to_anthropic_content(content: str | list[ContentPart]) -> str | list[dict]:
    """Serialise content into Anthropic Messages API blocks (image = base64 source)."""
    if isinstance(content, str):
        return content
    out: list[dict] = []
    for part in content:
        if isinstance(part, TextPart):
            out.append({"type": "text", "text": part.text})
        elif isinstance(part, ImagePart):
            b64 = base64.b64encode(part.bytes).decode("ascii")
            out.append(
                {
                    "type": "image",
                    "source": {"type": "base64", "media_type": part.mime_type, "data": b64},
                }
            )
    return out


def to_openai_content(content: str | list[ContentPart]) -> str | list[dict]:
    """Serialise content into OpenAI chat-completions blocks (image = data URL)."""
    if isinstance(content, str):
        return content
    out: list[dict] = []
    for part in content:
        if isinstance(part, TextPart):
            out.append({"type": "text", "text": part.text})
        elif isinstance(part, ImagePart):
            b64 = base64.b64encode(part.bytes).decode("ascii")
            out.append(
                {
                    "type": "image_url",
                    "image_url": {"url": f"data:{part.mime_type};base64,{b64}"},
                }
            )
    return out


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
