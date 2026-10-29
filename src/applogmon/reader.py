"""Incremental log-file reader with basic truncation and replacement support."""

from pathlib import Path
from typing import Iterator


class LogReader:
    """Read new lines without loading the entire log into memory."""

    def __init__(self, path: Path, scan_existing: bool = False):
        self.path = Path(path)
        self.scan_existing = scan_existing
        self._file = None
        self._identity = None

    def close(self) -> None:
        if self._file is not None:
            self._file.close()
            self._file = None
        self._identity = None

    def _open(self) -> bool:
        try:
            new_file = self.path.open(
                "r", encoding="utf-8", errors="replace"
            )
            stat = self.path.stat()
        except (FileNotFoundError, PermissionError, IsADirectoryError):
            return False

        identity = (stat.st_dev, stat.st_ino)

        if self._file is not None:
            self._file.close()

        self._file = new_file
        self._identity = identity

        if not self.scan_existing:
            self._file.seek(0, 2)

        return True

    def read_new_lines(self) -> list[str]:
        """Return lines appended since the previous call."""
        if not self.path.exists():
            self.close()
            return []

        try:
            stat = self.path.stat()
        except OSError:
            return []

        identity = (stat.st_dev, stat.st_ino)

        if self._file is None:
            if not self._open():
                return []
        elif identity != self._identity:
            # The path now refers to a different file (rotation/replacement).
            if not self._open():
                return []
        else:
            # Detect copy-truncate log rotation.
            try:
                if stat.st_size < self._file.tell():
                    self._file.seek(0)
            except (OSError, ValueError):
                if not self._open():
                    return []

        try:
            return self._file.readlines()
        except (OSError, ValueError):
            self.close()
            return []

    def follow(self, interval: float = 0.5) -> Iterator[str]:
        """Yield new lines until interrupted by the caller."""
        import time

        if interval <= 0:
            raise ValueError("interval must be greater than zero")

        while True:
            yield from self.read_new_lines()
            time.sleep(interval)
