import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timezone

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
from hotel_automation.booking_manager import (
    BookingManager,
    BookingConflictError,
    InvalidBookingTransition,
)


def make_manager(history_limit=1000):
    return BookingManager(
        room_numbers=[101, 102, 103],
        history_limit=history_limit,
    )


def make_booking(
    manager,
    guest="Ravi",
    room=101,
    check_in=date(2026, 11, 10),
    check_out=date(2026, 11, 12),
    expires_at=datetime(2026, 11, 9, tzinfo=timezone.utc),
):
    return manager.create_booking(
        guest_name=guest,
        room_number=room,
        check_in=check_in,
        check_out=check_out,
        expires_at=expires_at,
    )


def test_same_room_cannot_be_booked_for_overlapping_dates():
    manager = make_manager()

    make_booking(manager)

    with pytest.raises(BookingConflictError):
        make_booking(
            manager,
            guest="Priya",
            check_in=date(2026, 11, 11),
            check_out=date(2026, 11, 13),
        )

    # Adjacent reservations must be allowed.
    adjacent = make_booking(
        manager,
        guest="Anil",
        check_in=date(2026, 11, 12),
        check_out=date(2026, 11, 14),
    )

    assert adjacent.room_number == 101


def test_concurrent_booking_requests_for_same_room():
    manager = make_manager()

    def attempt_booking(guest):
        return make_booking(manager, guest=guest)

    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [
            executor.submit(attempt_booking, "Ravi"),
            executor.submit(attempt_booking, "Priya"),
        ]

        results = []
        for future in futures:
            try:
                results.append(future.result())
            except BookingConflictError:
                results.append(None)

    successful_bookings = [item for item in results if item is not None]

    assert len(successful_bookings) == 1
    assert successful_bookings[0].status == BookingStatus.PENDING


def test_invalid_booking_state_transitions_are_rejected():
    manager = make_manager()
    booking = make_booking(manager)

    # A pending booking can be checked in.
    manager.check_in(booking.booking_id)

    assert manager.get_booking(
        booking.booking_id
    ).status == BookingStatus.CHECKED_IN

    # A checked-in booking cannot be cancelled.
    with pytest.raises(InvalidBookingTransition):
        manager.cancel_booking(booking.booking_id)

    # A checked-in booking can be checked out.
    manager.check_out(booking.booking_id)

    assert manager.get_booking(
        booking.booking_id
    ).status == BookingStatus.CHECKED_OUT

    # A checked-out booking cannot be checked in again.
    with pytest.raises(InvalidBookingTransition):
        manager.check_in(booking.booking_id)


def test_invalid_booking_dates_do_not_modify_state():
    manager = make_manager()

    with pytest.raises(ValueError):
        make_booking(
            manager,
            check_in=date(2026, 11, 12),
            check_out=date(2026, 11, 10),
        )

    with pytest.raises(ValueError):
        make_booking(
            manager,
            check_in=date(2026, 11, 10),
            check_out=date(2026, 11, 10),
        )

    assert manager.history == []


def test_booking_history_respects_configured_limit():
    manager = make_manager(history_limit=2)

    booking = make_booking(manager)
    manager.check_in(booking.booking_id)
    manager.check_out(booking.booking_id)

    assert len(manager.history) == 2

    event_types = [
        event.event_type for event in manager.history
    ]

    assert event_types == [
        "BOOKING_CHECKED_IN",
        "BOOKING_CHECKED_OUT",
    ]
