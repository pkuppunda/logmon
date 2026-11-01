"""Poll hotel automation log files and report new entries."""

from pathlib import Path
import time
from typing import Optional

from hotel_automation.monitoring.analyzer import ErrorAnalyzer
from hotel_automation.monitoring.classifier import classify_entry
from hotel_automation.monitoring.parser import LEVEL_VALUES, parse_line
from hotel_automation.monitoring.reader import LogReader
from hotel_automation.monitoring.report import display_entry, display_summary


class LogMonitor:
    def __init__(
        self,
        app_dir: Path,
        pattern: str = "logs/*.log",
        minimum_level: str = "WARNING",
        history_limit: int = 1000,
        poll_interval: float = 0.5,
        scan_existing: bool = False,
        correlator=None,
        display: bool = True,
    ):
        if poll_interval <= 0:
            raise ValueError("poll_interval must be greater than zero")
        if minimum_level.upper() not in LEVEL_VALUES:
            raise ValueError(f"Unsupported minimum level: {minimum_level!r}")

        self.app_dir = Path(app_dir)
        self.pattern = pattern
        self.minimum_level = minimum_level.upper()
        self.poll_interval = poll_interval
        self.scan_existing = scan_existing
        self.analyzer = ErrorAnalyzer(history_limit=history_limit)
        self.readers: dict[Path, LogReader] = {}
        self.correlator = correlator
        self.display = display

    def __enter__(self) -> "LogMonitor":
        return self

    def __exit__(self, *exc_info) -> None:
        self.close()

    def close(self) -> None:
        for reader in self.readers.values():
            reader.close()
        self.readers.clear()

    def _process_line(self, line: str, path: Path) -> None:
        entry = parse_line(line, source=str(path))
        self.analyzer.add(entry)
        if self.display:
            display_entry(entry, self.minimum_level)

        if self.correlator is not None:
            event: Optional[dict] = classify_entry(entry)
            if event is not None:
                self.correlator.observe(event)

    def poll_once(self) -> int:
        """Process available new lines and return the number of lines read."""
        paths = sorted(self.app_dir.glob(self.pattern))
        active_paths = set(paths)

        for path in list(self.readers):
            if path not in active_paths:
                self.readers.pop(path).close()

        lines_read = 0
        for path in paths:
            reader = self.readers.get(path)
            if reader is None:
                reader = LogReader(path, scan_existing=self.scan_existing)
                self.readers[path] = reader

            for line in reader.read_new_lines():
                self._process_line(line, path)
                lines_read += 1

        # Files discovered later are new, so their full content is relevant.
        self.scan_existing = True
        for reader in self.readers.values():
            reader.scan_existing = True

        return lines_read

    def run(self, follow: bool = False) -> None:
        print(f"Monitoring application: {self.app_dir}")
        print(f"Pattern: {self.pattern}")
        print(f"Minimum level: {self.minimum_level}")
        print("Press Ctrl+C to stop.\n")

        try:
            while True:
                self.poll_once()
                if not follow:
                    break
                time.sleep(self.poll_interval)
        finally:
            self.close()
            display_summary(self.analyzer)
