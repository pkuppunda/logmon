"""Generate realistic hotel operations events."""

from __future__ import annotations

from dataclasses import dataclass
import logging
import random
from typing import Iterator, Optional

ROOMS = (101, 102, 103, 201, 202)
GUESTS = ("Ravi", "Priya", "Anil", "Kavya", "Suresh")

DATABASE = "database"
PAYMENT_GATEWAY = "payment_gateway"

RECOVERY_MESSAGES = {
    DATABASE: "Database connection restored",
    PAYMENT_GATEWAY: "Payment gateway recovered",
}


@dataclass(frozen=True)
class HotelEvent:
    level: int
    message: str

    @property
    def level_name(self) -> str:
        return logging.getLevelName(self.level)


class HotelEventSimulator:
    """Stateful, optionally seeded generator of hotel log events.

    Failing subsystems are remembered so that a recovery message is only
    emitted for a subsystem that is currently degraded.
    """

    def __init__(self, seed: Optional[int] = None):
        self._random = random.Random(seed)
        self.degraded: set[str] = set()

    def _booking_confirmed(self, guest: str, room: int) -> HotelEvent:
        return HotelEvent(logging.INFO, f"Booking confirmed for guest {guest}, room {room}")

    def _checked_in(self, guest: str, room: int) -> HotelEvent:
        return HotelEvent(logging.INFO, f"Guest {guest} checked into room {room}")

    def _payment_received(self, guest: str, room: int) -> HotelEvent:
        return HotelEvent(logging.INFO, f"Payment received for guest {guest}")

    def _cleaning_delayed(self, guest: str, room: int) -> HotelEvent:
        return HotelEvent(
            logging.WARNING, f"Room {room} cleaning delayed; guest {guest} waiting"
        )

    def _payment_timeout(self, guest: str, room: int) -> HotelEvent:
        self.degraded.add(PAYMENT_GATEWAY)
        return HotelEvent(logging.ERROR, f"Payment gateway timeout for guest {guest}")

    def _database_failed(self, guest: str, room: int) -> HotelEvent:
        self.degraded.add(DATABASE)
        return HotelEvent(logging.ERROR, "Database connection failed while saving booking")

    def _recover(self) -> HotelEvent:
        subsystem = self._random.choice(sorted(self.degraded))
        self.degraded.discard(subsystem)
        return HotelEvent(logging.INFO, RECOVERY_MESSAGES[subsystem])

    def next_event(self) -> HotelEvent:
        guest = self._random.choice(GUESTS)
        room = self._random.choice(ROOMS)
        generators = (
            self._booking_confirmed,
            self._checked_in,
            self._payment_received,
            self._cleaning_delayed,
            self._payment_timeout,
            self._database_failed,
        )

        if self.degraded and self._random.random() < 0.2:
            return self._recover()
        return self._random.choice(generators)(guest, room)

    def events(self, limit: Optional[int] = None) -> Iterator[HotelEvent]:
        produced = 0
        while limit is None or produced < limit:
            yield self.next_event()
            produced += 1
