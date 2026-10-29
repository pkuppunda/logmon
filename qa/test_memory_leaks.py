from applogmon.analyzer import ErrorAnalyzer
from applogmon.parser import parse_line


def test_history_remains_bounded():
    analyzer = ErrorAnalyzer(history_limit=100)

    for index in range(10_000):
        analyzer.add(parse_line(f"ERROR simulated failure {index}"))

    assert len(analyzer.history) == 100


def test_repeated_errors_are_counted():
    analyzer = ErrorAnalyzer(history_limit=10)

    for _ in range(3):
        analyzer.add(parse_line("2026-10-28 14:30:05 ERROR Database failed"))

    assert analyzer.repeated_errors() == [
        ("ERROR", "Database failed", 3)
    ]


def test_history_limit_must_be_positive():
    import pytest

    with pytest.raises(ValueError):
        ErrorAnalyzer(history_limit=0)
