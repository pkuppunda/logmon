from applogmon.parser import parse_line


def test_parse_error_line():
    entry = parse_line(
        "2026-10-28 14:30:05 ERROR Database connection failed"
    )
    assert entry.timestamp == "2026-10-28 14:30:05"
    assert entry.level == "ERROR"
    assert entry.message == "Database connection failed"


def test_parse_level_is_case_insensitive():
    entry = parse_line("2026-10-28 14:30:05 error Failed")
    assert entry.level == "ERROR"


def test_unrecognized_line_is_preserved():
    entry = parse_line("Traceback (most recent call last):")
    assert entry.level == "INFO"
    assert entry.message == "Traceback (most recent call last):"


def test_source_is_preserved():
    entry = parse_line("plain text", source="app.log")
    assert entry.source == "app.log"
