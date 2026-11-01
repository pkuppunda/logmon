"""Hotel automation service lifecycle: startup, event loop, and shutdown."""

from __future__ import annotations

import logging
import threading
from typing import Optional

from hotel_automation.config import AppConfig
from hotel_automation.simulator import HotelEventSimulator

LOG_FORMAT = "%(asctime)s %(levelname)s %(message)s"
LOG_DATE_FORMAT = "%Y-%m-%d %H:%M:%S"

STARTED_MESSAGE = "Hotel automation application started"
STOPPED_MESSAGE = "Hotel automation application stopped"


class HotelAutomationService:
    """Emit hotel operations events to a log file until stopped.

    ``run()`` blocks in the calling thread; ``start()`` runs the same loop in a
    background thread. ``stop()`` requests a graceful shutdown which always
    writes the stopped message and releases the log file handle.
    """

    def __init__(
        self,
        config: AppConfig,
        simulator: Optional[HotelEventSimulator] = None,
    ):
        self.config = config
        self.simulator = simulator or HotelEventSimulator(seed=config.seed)
        self.events_emitted = 0
        self._stop_requested = threading.Event()
        self._finished = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._lock = threading.Lock()
        self._running = False
        self._logger: Optional[logging.Logger] = None
        self._handler: Optional[logging.Handler] = None

    @property
    def log_file(self):
        return self.config.log_file

    @property
    def is_running(self) -> bool:
        return self._running

    def __enter__(self) -> "HotelAutomationService":
        self.start()
        return self

    def __exit__(self, *exc_info) -> None:
        self.stop()

    def _open_logger(self) -> logging.Logger:
        self.config.log_dir.mkdir(parents=True, exist_ok=True)
        handler = logging.FileHandler(self.config.log_file, encoding="utf-8")
        handler.setFormatter(logging.Formatter(LOG_FORMAT, datefmt=LOG_DATE_FORMAT))

        logger = logging.Logger("hotel_automation", level=logging.INFO)
        logger.propagate = False
        logger.addHandler(handler)

        self._handler = handler
        self._logger = logger
        return logger

    def _close_logger(self) -> None:
        if self._logger is not None and self._handler is not None:
            self._logger.removeHandler(self._handler)
            self._handler.close()
        self._handler = None
        self._logger = None

    def run(self) -> int:
        """Run the event loop in the current thread; return events emitted."""
        with self._lock:
            if self._running:
                raise RuntimeError("Hotel automation service is already running")
            self._running = True
            self._finished.clear()

        logger = self._open_logger()
        try:
            logger.info(STARTED_MESSAGE)
            limit = self.config.max_events
            while not self._stop_requested.is_set():
                if limit is not None and self.events_emitted >= limit:
                    break
                event = self.simulator.next_event()
                logger.log(event.level, event.message)
                self.events_emitted += 1
                self._stop_requested.wait(self.config.interval)
        finally:
            logger.info(STOPPED_MESSAGE)
            self._close_logger()
            self._running = False
            self._finished.set()

        return self.events_emitted

    def start(self) -> None:
        """Run the service in a background thread."""
        if self._thread is not None and self._thread.is_alive():
            raise RuntimeError("Hotel automation service is already running")
        self._stop_requested.clear()
        self._finished.clear()
        self._thread = threading.Thread(
            target=self.run, name="hotel-automation", daemon=True
        )
        self._thread.start()

    def request_stop(self) -> None:
        self._stop_requested.set()

    def wait(self, timeout: Optional[float] = None) -> bool:
        return self._finished.wait(timeout)

    def stop(self, timeout: float = 10.0) -> None:
        """Request shutdown and wait for the background loop to finish."""
        self.request_stop()
        if self._thread is not None:
            self._thread.join(timeout)
            if self._thread.is_alive():
                raise RuntimeError("Hotel automation service did not stop in time")
            self._thread = None
