"""Strict validation of correlator configuration and incoming events."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
import re
from typing import Any, Optional

from hotel_automation.incidents.components import (
    ComponentProfile,
    code_profile,
    profile_component,
)
from hotel_automation.incidents.constants import (
    FAILURE_LEVELS,
    OPTIONAL_ID_FIELDS,
    RECOVERY_CODE_TO_ROLE,
    TIMESTAMP_FORMAT,
    VALID_LEVELS,
    WARNING_LEVELS,
)

_STRICT_TIMESTAMP_RE = re.compile(r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}$")


def validate_positive_int(name: str, value: Any) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ValueError(
            f"{name} must be a positive integer (booleans are not allowed); "
            f"got {value!r}"
        )
    return value


def parse_timestamp(value: Any, field_name: str = "timestamp") -> datetime:
    """Accept naive datetimes or strict ``YYYY-MM-DD HH:MM:SS`` strings."""
    if value is None:
        raise ValueError(f"{field_name} cannot be None")

    if isinstance(value, datetime):
        if value.tzinfo is not None and value.utcoffset() is not None:
            raise ValueError(f"{field_name} must be a naive datetime")
        return value

    if isinstance(value, date):
        raise ValueError(f"{field_name} must include a time component")

    if isinstance(value, str):
        if not _STRICT_TIMESTAMP_RE.match(value):
            raise ValueError(
                f"Malformed {field_name} {value!r}: expected '{TIMESTAMP_FORMAT}'"
            )
        try:
            return datetime.strptime(value, TIMESTAMP_FORMAT)
        except ValueError as exc:
            raise ValueError(f"Invalid {field_name} {value!r}: {exc}") from exc

    raise ValueError(
        f"Unsupported {field_name} type {type(value).__name__}: "
        f"expected datetime or '{TIMESTAMP_FORMAT}' string"
    )


def _required_text(event: dict, key: str) -> str:
    value = event.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"Event field {key!r} must be a non-empty string")
    return value


@dataclass(frozen=True)
class ParsedEvent:
    payload: dict[str, Any]
    timestamp: datetime
    level: str
    message: str
    profile: ComponentProfile
    event_code: Optional[str]
    code_roles: frozenset[str]
    code_tokens: frozenset[str]
    request_id: Optional[str]
    correlation_id: Optional[str]
    trace_id: Optional[str]
    is_recovery: bool = field(default=False)

    @property
    def is_failure(self) -> bool:
        return not self.is_recovery and self.level in FAILURE_LEVELS

    @property
    def is_warning(self) -> bool:
        return not self.is_recovery and self.level in WARNING_LEVELS

    @property
    def ids(self) -> dict[str, Optional[str]]:
        return {
            "trace_id": self.trace_id,
            "correlation_id": self.correlation_id,
            "request_id": self.request_id,
        }


def parse_event(event: Any) -> ParsedEvent:
    """Validate an event without side effects and return a normalized view."""
    if not isinstance(event, dict):
        raise ValueError(f"Event must be a dict, got {type(event).__name__}")
    if "timestamp" not in event:
        raise ValueError("Event is missing required field 'timestamp'")

    timestamp = parse_timestamp(event["timestamp"])

    level = _required_text(event, "level").strip().upper()
    if level not in VALID_LEVELS:
        raise ValueError(f"Unsupported log level {event['level']!r}")

    component = _required_text(event, "component").strip()
    message = _required_text(event, "message")

    optional: dict[str, Optional[str]] = {}
    for key in OPTIONAL_ID_FIELDS:
        value = event.get(key)
        if value is not None and not isinstance(value, str):
            raise ValueError(f"Optional field {key!r} must be a string or None")
        optional[key] = (value.strip() or None) if isinstance(value, str) else None

    event_code = optional["event_code"].upper() if optional["event_code"] else None

    payload: dict[str, Any] = {
        "timestamp": timestamp,
        "level": level,
        "component": component,
        "message": message,
    }
    for key in OPTIONAL_ID_FIELDS:
        if key in event:
            payload[key] = optional[key]
    if event_code is not None:
        payload["event_code"] = event_code

    code_roles, code_tokens = code_profile(event_code)

    return ParsedEvent(
        payload=payload,
        timestamp=timestamp,
        level=level,
        message=message,
        profile=profile_component(component),
        event_code=event_code,
        code_roles=code_roles,
        code_tokens=code_tokens,
        request_id=optional["request_id"],
        correlation_id=optional["correlation_id"],
        trace_id=optional["trace_id"],
        is_recovery=event_code in RECOVERY_CODE_TO_ROLE,
    )
