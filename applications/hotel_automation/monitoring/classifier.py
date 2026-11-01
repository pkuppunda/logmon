"""Translate hotel automation log entries into structured incident events."""

from dataclasses import dataclass
import re
from typing import Optional

from hotel_automation.monitoring.parser import LogEntry


@dataclass(frozen=True)
class MessageRule:
    pattern: re.Pattern
    component: str
    event_code: str


MESSAGE_RULES: tuple[MessageRule, ...] = (
    MessageRule(
        re.compile(r"^Database connection failed", re.IGNORECASE),
        "database",
        "DB_CONNECTION_FAILED",
    ),
    MessageRule(
        re.compile(r"^Database connection restored", re.IGNORECASE),
        "database",
        "DB_CONNECTION_RESTORED",
    ),
    MessageRule(
        re.compile(r"^Payment gateway timeout", re.IGNORECASE),
        "payment_gateway",
        "PAYMENT_GATEWAY_TIMEOUT",
    ),
    MessageRule(
        re.compile(r"^Payment gateway recovered", re.IGNORECASE),
        "payment_gateway",
        "PAYMENT_GATEWAY_RECOVERED",
    ),
    MessageRule(
        re.compile(r"^Room \S+ cleaning delayed", re.IGNORECASE),
        "housekeeping",
        "ROOM_CLEANING_DELAYED",
    ),
    MessageRule(
        re.compile(r"^Booking confirmed", re.IGNORECASE),
        "booking_service",
        "BOOKING_CONFIRMED",
    ),
    MessageRule(
        re.compile(r"^Guest \S+ checked into room", re.IGNORECASE),
        "front_desk",
        "GUEST_CHECKED_IN",
    ),
    MessageRule(
        re.compile(r"^Payment received", re.IGNORECASE),
        "payment_gateway",
        "PAYMENT_RECEIVED",
    ),
)


def classify_entry(entry: LogEntry) -> Optional[dict]:
    """Return a correlator event for a recognized, timestamped entry."""
    if entry.timestamp is None:
        return None

    for rule in MESSAGE_RULES:
        if rule.pattern.search(entry.message):
            return {
                "timestamp": entry.timestamp.replace("T", " "),
                "level": entry.level,
                "component": rule.component,
                "event_code": rule.event_code,
                "message": entry.message,
            }

    return None
