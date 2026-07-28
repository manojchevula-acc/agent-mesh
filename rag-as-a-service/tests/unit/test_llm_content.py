"""Unit tests for multimodal Message.content serialization."""

from gernas_rag.llm.base import (
    ImagePart,
    TextPart,
    flatten_text,
    to_anthropic_content,
    to_openai_content,
)


def test_flatten_text_passthrough_str():
    assert flatten_text("plain") == "plain"


def test_flatten_text_drops_images():
    content = [TextPart(text="a"), ImagePart(bytes=b"x", mime_type="image/png"), TextPart(text="b")]
    # Text-only providers keep text, never send bytes.
    assert flatten_text(content) == "a\nb"


def test_anthropic_str_passthrough():
    assert to_anthropic_content("hi") == "hi"


def test_anthropic_builds_image_block():
    content = [TextPart(text="describe"), ImagePart(bytes=b"PNGDATA", mime_type="image/png")]
    blocks = to_anthropic_content(content)
    assert blocks[0] == {"type": "text", "text": "describe"}
    assert blocks[1]["type"] == "image"
    assert blocks[1]["source"]["type"] == "base64"
    assert blocks[1]["source"]["media_type"] == "image/png"
    assert blocks[1]["source"]["data"]  # base64 payload present


def test_openai_builds_image_url_block():
    content = [TextPart(text="q"), ImagePart(bytes=b"PNGDATA", mime_type="image/png")]
    blocks = to_openai_content(content)
    assert blocks[0] == {"type": "text", "text": "q"}
    assert blocks[1]["type"] == "image_url"
    assert blocks[1]["image_url"]["url"].startswith("data:image/png;base64,")
