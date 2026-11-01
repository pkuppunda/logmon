"""Bounded event history and repeated-error summaries."""

from collections import Counter, deque
from typing import Iterable

from hotel_automation.monitoring.parser import LEVEL_VALUES, LogEntry


class ErrorAnalyzer:
    def __init__(self, history_limit: int = 1000):
        if isinstance(history_limit, bool) or not isinstance(history_limit, int):
            raise ValueError("history_limit must be an integer")
        if history_limit < 1:
            raise ValueError("history_limit must be at least 1")

        self.history: deque[LogEntry] = deque(maxlen=history_limit)
        self.error_counts: Counter = Counter()

    def add(self, entry: LogEntry) -> None:
        self.history.append(entry)

        if LEVEL_VALUES.get(entry.level, 0) >= LEVEL_VALUES["ERROR"]:
            self.error_counts[(entry.level, entry.message)] += 1

    def add_many(self, entries: Iterable[LogEntry]) -> None:
        for entry in entries:
            self.add(entry)

    def repeated_errors(self, minimum_count: int = 2) -> list[tuple[str, str, int]]:
        """Return error messages observed at least ``minimum_count`` times."""
        return sorted(
            [
                (level, message, count)
                for (level, message), count in self.error_counts.items()
                if count >= minimum_count
            ],
            key=lambda item: item[2],
            reverse=True,
        )

    def level_counts(self) -> dict[str, int]:
        return dict(Counter(entry.level for entry in self.history))
