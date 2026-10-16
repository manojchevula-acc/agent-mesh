"""PDPL: PII patterns (WP2-A-04 pattern classes, in-process pass) and registry actions."""
from __future__ import annotations

import pytest

from gernas import Ctx
from gernas import redaction
from gernas.attributes import REDACTION_ACTIONS, REGISTRY
from gernas.utils.sanitizer import scrub_pii


@pytest.mark.parametrize("text,placeholder", [
    ("id 784-1985-1234567-1 on file", "[EMIRATES_ID]"),
    ("id 784198512345671 on file", "[EMIRATES_ID]"),
    ("iban AE070331234567890123456 ok", "[IBAN]"),
    ("iban AE07 0331 2345 6789 0123 456 ok", "[IBAN]"),
    ("card 4111 1111 1111 1111 used", "[CARD]"),
    ("card 4111111111111111 used", "[CARD]"),
    ("Authorization: Bearer abc.def.ghi", "Bearer [REDACTED]"),
    ("key gsk_ABCDEFGHIJKLMNOPQRSTUV123", "[API_KEY]"),
    ("password=hunter2 now", "password=[REDACTED]"),
    ("mail alice@fab.ae", "[EMAIL]"),
])
def test_patterns_are_scrubbed(text, placeholder):
    assert placeholder in scrub_pii(text)


def test_non_luhn_digit_runs_are_not_cards():
    assert "[CARD]" not in scrub_pii("order 4111111111111112")


def test_plain_text_is_untouched():
    text = "Loan pricing for SME segment rose 25 bps in Q3"
    assert scrub_pii(text) == text


def test_registry_actions():
    assert redaction.redact(Ctx.PROMPT, "x")[0] is redaction.DROP
    assert redaction.redact(Ctx.USER_ID, "alice")[0] is redaction.DROP
    fp, changed = redaction.redact(Ctx.TOKENISED_USER_ID, "alice")
    assert changed and fp == redaction.fingerprint("alice")
    assert redaction.redact(Ctx.AGENT_ID, "a") == ("a", False)


def test_masked_payload_fields_need_a_capture_grant():
    assert redaction.redact(Ctx.PROMPT_REDACTED, "call 0501234567")[0] is redaction.DROP
    redaction.set_capture_payloads(True)
    try:
        value, changed = redaction.redact(Ctx.PROMPT_REDACTED, "call +971 50 123 4567")
        assert changed and "[PHONE]" in value
    finally:
        redaction.set_capture_payloads(False)


def test_every_restricted_field_has_a_non_keep_action():
    for key, spec in REGISTRY.items():
        if spec.sensitivity in ("restricted", "secret"):
            assert REDACTION_ACTIONS.get(key) in ("drop", "fingerprint", "tokenise"), key
