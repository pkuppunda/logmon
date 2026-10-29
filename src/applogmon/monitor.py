"""Poll log files and report new entries."""

from pathlib import Path
import time

from applogmon.analyzer import ErrorAnalyzer
from applogmon.parser import parse_line
from applogmon.reader import LogReader
from applogmon.report import display_entry, display_summary
from applogmon.spike_detector import ErrorSpikeDetector


class LogMonitor:
    def __init__(
        self,
        app_dir: Path,
        pattern: str = "logs/*.log",
        minimum_level: str = "WARNING",
        history_limit: int = 1000,
        poll_interval: float = 0.5,
        scan_existing: bool = False,
        spike_window: int = 60,
        spike_threshold: int = 10,
        spike_levels=("ERROR", "CRITICAL"),
        spike_cooldown: int = 60,
    ):
        if poll_interval <= 0:
            raise ValueError("poll_interval must be greater than zero")

        self.app_dir = Path(app_dir)
        self.pattern = pattern
        self.minimum_level = minimum_level.upper()
        self.poll_interval = poll_interval
        self.scan_existing = scan_existing
        self.analyzer = ErrorAnalyzer(history_limit=history_limit)
        self.readers: dict[Path, LogReader] = {}
        self.spike_detector = ErrorSpikeDetector(
            window_seconds=spike_window,
            threshold=spike_threshold,
            levels=spike_levels,
            cooldown_seconds=spike_cooldown,
        )

    def poll_once(self) -> int:
        """Process available new lines and return the number of lines read."""
        paths = sorted(self.app_dir.glob(self.pattern))
        active_paths = set(paths)

        # Close files that no longer match the configured pattern.
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
                entry = parse_line(line, source=str(path))
                self.analyzer.add(entry)
                display_entry(entry, self.minimum_level)

                alert = self.spike_detector.observe(
                    level=entry.level,
                    message=entry.message,
                    timestamp=entry.timestamp,
                )

                if alert is not None:
                    print(
                        f"\n[ERROR SPIKE] {alert.count} matching entries "
                        f"in {alert.window_seconds}s "
                        f"(threshold: {alert.threshold})"
                    )
                    for message, count in alert.top_messages:
                        print(f"  {count}x {message}")

                lines_read += 1


        # Existing files are scanned only on initial discovery.
        # Subsequent polling reads only newly appended content.
        self.scan_existing = True
        for reader in self.readers.values():
            reader.scan_existing = True

        return lines_read

    def run(self, follow: bool = False) -> None:
        print(f"Monitoring application: {self.app_dir}")
        print(f"Pattern: {self.pattern}")
        print(f"Minimum level: {self.minimum_level}")
        print("Press Ctrl+C to stop.\n")
        print(
            f"Error spike detection: {self.spike_detector.threshold} "
            f"errors in {self.spike_detector.window_seconds}s"
        )
        print(
            f"Counted levels: {', '.join(sorted(self.spike_detector.levels))}"
        )
        print(
            f"Alert cooldown: {self.spike_detector.cooldown_seconds}s\n"
        )

        try:
            while True:
                self.poll_once()
                if not follow:
                    break
                time.sleep(self.poll_interval)
        finally:
            for reader in self.readers.values():
                reader.close()
            display_summary(self.analyzer)
