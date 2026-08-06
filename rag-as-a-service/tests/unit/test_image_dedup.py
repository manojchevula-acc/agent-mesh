"""Exact and perceptual deduplication."""

from io import BytesIO

import pytest

from gernas_rag.config.multimodal import ImageExtractionConfig
from gernas_rag.images.dedup import Deduper, dhash, hamming

PIL = pytest.importorskip("PIL.Image")


def _gradient(width: int = 128, height: int = 128, shift: int = 0):
    image = PIL.new("RGB", (width, height))
    pixels = image.load()
    for x in range(width):
        for y in range(height):
            pixels[x, y] = ((x * 2 + shift) % 256, (y * 2) % 256, ((x + y) + shift) % 256)
    return image


def _checker(width: int = 128, height: int = 128, box: int = 16):
    image = PIL.new("RGB", (width, height), (255, 255, 255))
    pixels = image.load()
    for x in range(width):
        for y in range(height):
            if ((x // box) + (y // box)) % 2 == 0:
                pixels[x, y] = (0, 0, 0)
    return image


# ── dhash ────────────────────────────────────────────────────────────────
def test_dhash_is_deterministic():
    image = _gradient()
    assert dhash(image) == dhash(image)


def test_dhash_length_is_16_hex_chars():
    assert len(dhash(_gradient())) == 16


def test_dhash_survives_rescaling():
    original = _gradient(256, 256)
    rescaled = original.resize((243, 243), PIL.LANCZOS)
    assert hamming(dhash(original), dhash(rescaled)) <= 4


def test_dhash_survives_png_to_webp_roundtrip():
    original = _gradient()
    buf = BytesIO()
    original.save(buf, format="WEBP", quality=90)
    buf.seek(0)
    assert hamming(dhash(original), dhash(PIL.open(buf).convert("RGB"))) <= 4


def test_different_images_differ():
    assert hamming(dhash(_gradient()), dhash(_checker())) > 4


def test_hamming_on_mismatched_lengths_is_max():
    assert hamming("abcd", "abcdef12") == 64


# ── Deduper ──────────────────────────────────────────────────────────────
@pytest.fixture
def deduper() -> Deduper:
    return Deduper(ImageExtractionConfig())


def test_exact_duplicate_detected(deduper):
    deduper.remember("sha-a", "0000000000000000", "asset-a")
    is_dup, existing = deduper.is_duplicate("sha-a", "ffffffffffffffff")
    assert is_dup and existing == "asset-a"


def test_near_duplicate_detected(deduper):
    original = _gradient(256, 256)
    rescaled = original.resize((243, 243), PIL.LANCZOS)
    deduper.remember("sha-1", dhash(original), "asset-1")
    is_dup, existing = deduper.is_duplicate("sha-2", dhash(rescaled))
    assert is_dup and existing == "asset-1"


def test_distinct_images_are_not_duplicates(deduper):
    deduper.remember("sha-1", dhash(_gradient()), "asset-1")
    is_dup, _ = deduper.is_duplicate("sha-2", dhash(_checker()))
    assert not is_dup


def test_perceptual_dedup_can_be_disabled():
    deduper = Deduper(ImageExtractionConfig(dedup_perceptual=False))
    original = _gradient(256, 256)
    deduper.remember("sha-1", dhash(original), "asset-1")
    is_dup, _ = deduper.is_duplicate(
        "sha-2", dhash(original.resize((243, 243), PIL.LANCZOS))
    )
    assert not is_dup


def test_threshold_is_configurable():
    strict = Deduper(ImageExtractionConfig(phash_hamming_threshold=0))
    original = _gradient(256, 256)
    strict.remember("sha-1", dhash(original), "asset-1")
    is_dup, _ = strict.is_duplicate(
        "sha-2", dhash(original.resize((200, 200), PIL.LANCZOS))
    )
    assert not is_dup
