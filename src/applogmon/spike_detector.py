
"""Detect bursts of log errors within a rolling time window."""

from collections import Counter, deque
from dataclasses import dataclass
from datetime import datetime, timedelta



@dataclass(frozen=True)
class SpikeAlert:
    timestamp: datetime
    count: int
    window_seconds: int
    threshold: int
    top_messages: tuple[tuple[str, int], ...]


class ErrorSpikeDetector:
    def __init__(
        self,
        window_seconds: int = 60,
        threshold: int = 10,
        levels=("ERROR", "CRITICAL"),
        cooldown_seconds: int = 60,
    ):
        if window_seconds <= 0:
            raise ValueError("window_seconds must be greater than zero")
        if threshold <= 0:
            raise ValueError("threshold must be greater than zero")
        if cooldown_seconds < 0:
            raise ValueError("cooldown_seconds cannot be negative")

        self.window_seconds = window_seconds
        self.threshold = threshold
        self.levels = {
            level.strip().upper()
            for level in levels
            if level.strip()
        }
        if not self.levels:
            raise ValueError("At least one log level is required")

        self.cooldown_seconds = cooldown_seconds
        self.events = deque()
        self.last_alert_at = None

    def observe(self, level, message, timestamp=None):
        level = level.strip().upper()
        if level not in self.levels:
            return None
        if isinstance(timestamp, str):
            timestamp = datetime.strptime(
                timestamp, "%Y-%m-%d %H:%M:%S"
            )
        now = timestamp or datetime.now()
        
        cutoff = now - timedelta(seconds=self.window_seconds)

        while self.events and self.events[0][0] < cutoff:
            self.events.popleft()

        self.events.append((now, message.strip() or "(empty message)"))

        if len(self.events) < self.threshold:
            return None

        if self.last_alert_at is not None:
            elapsed = (now - self.last_alert_at).total_seconds()
            if elapsed < self.cooldown_seconds:
                return None

        counts = Counter(message for _, message in self.events)
        self.last_alert_at = now

        return SpikeAlert(
            timestamp=now,
            count=len(self.events),
            window_seconds=self.window_seconds,
            threshold=self.threshold,
            top_messages=tuple(counts.most_common(3)),
        )
