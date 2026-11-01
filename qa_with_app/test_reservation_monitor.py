import sys
import threading
import time
from datetime import date, datetime, timedelta, timezone

from pathlib import Path


TEST_FILE = Path(__file__).resolve()

# Supports both:
# 1. logmon/qa_with_app/test_hotel_memory_resources.py
# 2. logmon/test_hotel_memory_resources.py

if TEST_FILE.parent.name == "qa_with_app":
    PROJECT_ROOT = TEST_FILE.parent.parent
else:
    PROJECT_ROOT = TEST_FILE.parent

APPLICATIONS_DIR = PROJECT_ROOT / "applications"

sys.path.insert(0, str(APPLICATIONS_DIR))

import pytest

# Make hotel_automation importable from the project root.
APPLICATIONS_DIR = (
    Path(__file__).resolve().parents[1] / "applications"
)
sys.path.insert(0, str(APPLICATIONS_DIR))

from hotel_automation.booking import BookingStatus
from hotel_automation.booking_manager import BookingManager
from hotel_automation.reservation_monitor import ReservationMonitor


def make_manager():
    return BookingManager(
        room_numbers=[101, 102, 103],
        history_limit=100,
    )


def make_booking(manager, expires_at):
    return manager.create_booking(
        guest_name="Ravi",
        room_number=101,
        check_in=date(2026, 11, 10),
        check_out=date(2026, 11, 12),
        expires_at=expires_at,
    )


def wait_until(predicate, timeout=2.0):
    deadline = time.monotonic() + timeout

    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.01)

    return predicate()


def test_expired_pending_booking_is_marked_expired():
    manager = make_manager()

    now = datetime(2026, 11, 9, 12, tzinfo=timezone.utc)
    booking = make_booking(
        manager,
        expires_at=now - timedelta(seconds=1),
    )

    monitor = ReservationMonitor(manager, interval=0.1)
    monitor.scan_once(now=now)

    updated = manager.get_booking(booking.booking_id)

    assert updated.status == BookingStatus.EXPIRED


def test_monitor_does_not_expire_cancelled_or_checked_in_bookings():
    manager = make_manager()

    now = datetime(2026, 11, 9, 12, tzinfo=timezone.utc)
    expired_at = now - timedelta(seconds=1)

    cancelled = make_booking(manager, expired_at)
    manager.cancel_booking(cancelled.booking_id)

    # Use a different room to avoid reservation conflicts.
    checked_in = manager.create_booking(
        guest_name="Priya",
        room_number=102,
        check_in=date(2026, 11, 10),
        check_out=date(2026, 11, 12),
        expires_at=expired_at,
    )
    manager.check_in(checked_in.booking_id)

    monitor = ReservationMonitor(manager, interval=0.1)
    monitor.scan_once(now=now)

    assert manager.get_booking(
        cancelled.booking_id
    ).status == BookingStatus.CANCELLED

    assert manager.get_booking(
        checked_in.booking_id
    ).status == BookingStatus.CHECKED_IN


def test_repeated_expiration_scans_do_not_duplicate_events():
    manager = make_manager()

    now = datetime(2026, 11, 9, 12, tzinfo=timezone.utc)
    booking = make_booking(
        manager,
        expires_at=now - timedelta(seconds=1),
    )

    monitor = ReservationMonitor(manager, interval=0.1)

    monitor.scan_once(now=now)
    monitor.scan_once(now=now)
    monitor.scan_once(now=now)

    expiration_events = [
        event for event in manager.history
        if event.event_type == "BOOKING_EXPIRED"
        and event.booking_id == booking.booking_id
    ]

    assert len(expiration_events) == 1


def test_monitor_can_stop_and_restart_without_thread_leaks():
    manager = make_manager()
    monitor = ReservationMonitor(manager, interval=0.05)

    monitor.start()

    with pytest.raises(RuntimeError, match="already running"):
        monitor.start()

    assert monitor.is_running

    monitor.stop(timeout=1.0)

    assert not monitor.is_running

    # Restart the same monitor instance.
    monitor.start()
    assert monitor.is_running

    monitor.stop(timeout=1.0)

    assert not monitor.is_running

    leaked_threads = [
        thread
        for thread in threading.enumerate()
        if thread.name == "reservation-monitor"
        and thread.is_alive()
    ]

    assert leaked_threads == []


def test_monitor_cleans_up_after_unexpected_exception(monkeypatch):
    manager = make_manager()

    def failing_expiration_scan(now=None):
        raise RuntimeError("Simulated reservation monitor failure")

    monkeypatch.setattr(
        manager,
        "expire_pending",
        failing_expiration_scan,
    )

    monitor = ReservationMonitor(manager, interval=0.01)

    monitor.start()

    assert wait_until(
        lambda: monitor.last_error is not None
    )

    assert isinstance(monitor.last_error, RuntimeError)
    assert str(monitor.last_error) == (
        "Simulated reservation monitor failure"
    )

    assert monitor.wait(timeout=1.0)
    assert not monitor.is_running

    # stop() must remain safe after the worker has failed.
    monitor.stop(timeout=1.0)
