"""Incremental log-file reader with truncation and replacement support."""

from pathlib import Path
import time
from typing import Iterator


class LogReader:
    """Read newly appended lines without loading the entire log into memory."""

    def __init__(self, path: Path, scan_existing: bool = False):
        self.path = Path(path)
        self.scan_existing = scan_existing
        self._file = None
        self._identity = None

    def __enter__(self) -> "LogReader":
        return self

    def __exit__(self, *exc_info) -> None:
        self.close()

    def close(self) -> None:
        if self._file is not None:
            self._file.close()
            self._file = None
        self._identity = None

    def _open(self) -> bool:
        try:
            new_file = self.path.open("r", encoding="utf-8", errors="replace")
        except (FileNotFoundError, PermissionError, IsADirectoryError):
            return False

        try:
            stat = self.path.stat()
        except OSError:
            new_file.close()
            return False

        if self._file is not None:
            self._file.close()

        self._file = new_file
        self._identity = (stat.st_dev, stat.st_ino)

        if not self.scan_existing:
            self._file.seek(0, 2)

        return True

    def read_new_lines(self) -> list[str]:
        """Return lines appended since the previous call."""
        try:
            stat = self.path.stat()
        except FileNotFoundError:
            self.close()
            return []
        except OSError:
            return []

        identity = (stat.st_dev, stat.st_ino)

        if self._file is None or identity != self._identity:
            if not self._open():
                return []
        else:
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
        """Yield new lines until the caller stops iterating."""
        if interval <= 0:
            raise ValueError("interval must be greater than zero")

        while True:
            yield from self.read_new_lines()
            time.sleep(interval)
