"""Shared fixtures for running the suite against the hotel_automation app.

The ``applications`` directory is placed on ``sys.path`` so that the
``hotel_automation`` package is importable without installation. Fixtures
that need a live application start it as a real subprocess writing into a
per-test temporary directory and always stop it during teardown.
"""

from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
import signal
import subprocess
import sys
import time
from typing import Callable, Optional

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
APPLICATIONS_DIR = PROJECT_ROOT / "applications"

if str(APPLICATIONS_DIR) not in sys.path:
    sys.path.insert(0, str(APPLICATIONS_DIR))

from hotel_automation.service import STARTED_MESSAGE, STOPPED_MESSAGE  # noqa: E402

STARTUP_TIMEOUT = 15.0
SHUTDOWN_TIMEOUT = 15.0


def wait_until(
    predicate: Callable[[], bool], timeout: float, interval: float = 0.02
) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(interval)
    return predicate()


def read_log(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return ""


@dataclass
class RunningHotelApp:
    process: subprocess.Popen
    app_dir: Path
    log_file: Path

    def log_text(self) -> str:
        return read_log(self.log_file)

    def log_lines(self) -> list[str]:
        return self.log_text().splitlines()

    def is_running(self) -> bool:
        return self.process.poll() is None

    def wait_for_exit(self, timeout: float = SHUTDOWN_TIMEOUT) -> int:
        return self.process.wait(timeout=timeout)

    def stop(self, timeout: float = SHUTDOWN_TIMEOUT) -> int:
        """Gracefully stop the app, escalating to kill if it does not exit."""
        if self.process.poll() is None:
            if os.name == "posix":
                self.process.send_signal(signal.SIGINT)
            else:
                self.process.terminate()
            try:
                self.process.wait(timeout=timeout)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait(timeout=timeout)
        for stream in (self.process.stdout, self.process.stderr):
            if stream is not None and not stream.closed:
                stream.close()
        return self.process.returncode


def _app_environment(app_dir: Path) -> dict[str, str]:
    env = {
        key: value
        for key, value in os.environ.items()
        if not key.startswith("HOTEL_AUTOMATION_")
    }
    existing = env.get("PYTHONPATH")
    env["PYTHONPATH"] = (
        f"{APPLICATIONS_DIR}{os.pathsep}{existing}" if existing else str(APPLICATIONS_DIR)
    )
    env["HOTEL_AUTOMATION_LOG_DIR"] = str(app_dir / "logs")
    env["PYTHONUNBUFFERED"] = "1"
    return env


@pytest.fixture
def start_hotel_app(tmp_path) -> Callable[..., RunningHotelApp]:
    """Factory that launches the app and guarantees shutdown on teardown."""
    started: list[RunningHotelApp] = []

    def _start(
        *,
        interval: float = 0.05,
        seed: Optional[int] = None,
        max_events: Optional[int] = None,
        name: str = "hotel_app",
        wait_ready: bool = True,
    ) -> RunningHotelApp:
        app_dir = tmp_path / name
        app_dir.mkdir()
        args = [sys.executable, "-m", "hotel_automation", "--interval", str(interval)]
        if seed is not None:
            args += ["--seed", str(seed)]
        if max_events is not None:
            args += ["--max-events", str(max_events)]

        process = subprocess.Popen(
            args,
            cwd=str(PROJECT_ROOT),
            env=_app_environment(app_dir),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        app = RunningHotelApp(process, app_dir, app_dir / "logs" / "app.log")
        started.append(app)

        if wait_ready and not wait_until(
            lambda: STARTED_MESSAGE in app.log_text() or not app.is_running(),
            STARTUP_TIMEOUT,
        ):
            pytest.fail("hotel_automation did not start in time")
        if wait_ready and STARTED_MESSAGE not in app.log_text():
            stderr = process.stderr.read() if process.stderr else ""
            pytest.fail(f"hotel_automation failed to start: {stderr}")
        return app

    yield _start

    for app in started:
        app.stop()


__all__ = [
    "RunningHotelApp",
    "STARTED_MESSAGE",
    "STOPPED_MESSAGE",
    "read_log",
    "wait_until",
]
