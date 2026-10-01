"""Guest notes are cleaned before they reach the kitchen screen."""
from jubran.application.ordering_service import sanitize_note


def test_control_and_direction_characters_are_removed():
    assert sanitize_note("بدون‮ بصل\x07") == "بدون بصل"
    assert sanitize_note("no <b>onion</b>") == "no bonion/b"
    assert sanitize_note("line one\nline\ttwo   three") == "line one line two three"


def test_empty_and_long_notes():
    assert sanitize_note("   ") is None and sanitize_note(None) is None
    assert len(sanitize_note("x" * 500)) == 200
