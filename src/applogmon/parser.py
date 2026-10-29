"""Parse conventional timestamped application log lines."""

from dataclasses import dataclass
import re
from typing import Optional


LOG_PATTERN = re.compile(
    r"^(?P<timestamp>\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}:\d{2})"
    r"\s+(?P<level>DEBUG|INFO|WARNING|ERROR|CRITICAL)\s+"
    r"(?P<message>.*)$",
    re.IGNORECASE,
)

LEVEL_VALUES = {
    "DEBUG": 10,
    "INFO": 20,
    "WARNING": 30,
    "ERROR": 40,
    "CRITICAL": 50,
}


@dataclass(frozen=True)
class LogEntry:
    timestamp: Optional[str]
    level: str
    message: str
    raw: str
    source: str = ""


def parse_line(line: str, source: str = "") -> LogEntry:
    """Parse a log line; preserve unrecognized lines as INFO entries."""
    raw = line.rstrip("\r\n")
    match = LOG_PATTERN.match(raw)

    if not match:
        return LogEntry(
            timestamp=None,
            level="INFO",
            message=raw,
            raw=raw,
            source=source,
        )

    return LogEntry(
        timestamp=match.group("timestamp"),
        level=match.group("level").upper(),
        message=match.group("message"),
        raw=raw,
        source=source,
    )
