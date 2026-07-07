"""Feedback tests — no DB required."""

from sql_agent.feedback import capture_implicit, record


def test_empty_result_records_negative_filter_signal(monkeypatch):
    captured = {}
    from sql_agent.feedback import signals
    monkeypatch.setattr(signals, "record", lambda **kw: captured.update(kw))
    capture_implicit(session_id="s", user_id="u", turn_ref="t",
                     tier="semi_dynamic", status="success", rows_returned=0)
    assert captured["polarity"] == "negative"
    assert captured["stage"] == "filter_selection"


def test_failed_analytical_turn_blames_generation(monkeypatch):
    captured = {}
    from sql_agent.feedback import signals
    monkeypatch.setattr(signals, "record", lambda **kw: captured.update(kw))
    capture_implicit(session_id="s", user_id="u", turn_ref="t",
                     tier="full_dynamic", status="error", rows_returned=0)
    assert captured["polarity"] == "negative"
    assert captured["stage"] == "generation"


def test_successful_nonempty_turn_emits_no_signal(monkeypatch):
    calls = []
    from sql_agent.feedback import signals
    monkeypatch.setattr(signals, "record", lambda **kw: calls.append(kw))
    capture_implicit(session_id="s", user_id="u", turn_ref="t",
                     tier="parameterised", status="success", rows_returned=3)
    assert calls == []


def test_record_is_safe_noop_without_db():
    # No metadata DB configured in tests => must not raise.
    record(session_id="s", user_id="u", signal_type="explicit_rating", polarity="positive")
