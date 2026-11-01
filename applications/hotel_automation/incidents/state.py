"""Mutable internal state for a single incident."""

from __future__ import annotations

from collections import deque
from datetime import datetime
from typing import Any, Optional

from hotel_automation.incidents.components import same_logical_component
from hotel_automation.incidents.constants import (
    ANCHOR_EVENT_COUNT,
    MAX_EVENTS_PER_INCIDENT,
    RECOVERY_CODE_TO_ROLE,
)
from hotel_automation.incidents.root_cause import UNDETERMINED, RootCause, rank_root_cause
from hotel_automation.incidents.summary import ComponentSummary, EventOrder
from hotel_automation.incidents.validation import ParsedEvent

STATUS_OPEN = "open"
STATUS_CLOSED = "closed"

_StoredEvent = tuple[datetime, int, dict[str, Any]]


def recovery_applies_to(ev: ParsedEvent, summary: ComponentSummary) -> bool:
    """Whether a recovery event resolves failures recorded in ``summary``."""
    if same_logical_component(ev.profile, summary.profile):
        return True
    role = RECOVERY_CODE_TO_ROLE.get(ev.event_code or "")
    if role is None or summary.role != role:
        return False
    return (
        not ev.profile.qualifiers
        or not summary.profile.qualifiers
        or ev.profile.qualifiers == summary.profile.qualifiers
    )


class IncidentState:
    def __init__(self, incident_id: str, ev: ParsedEvent, seq: int) -> None:
        self.incident_id = incident_id
        self.status = STATUS_OPEN
        self.started_at = ev.timestamp
        self.last_seen = ev.timestamp
        self.event_count = 0
        self._anchor_events: list[_StoredEvent] = []
        self._recent_events: deque[_StoredEvent] = deque(
            maxlen=MAX_EVENTS_PER_INCIDENT - ANCHOR_EVENT_COUNT
        )
        self.ids: dict[str, set[str]] = {
            "trace_id": set(),
            "correlation_id": set(),
            "request_id": set(),
        }
        self.summaries: dict[str, ComponentSummary] = {}
        self.root_cause: RootCause = UNDETERMINED
        self.latest_failure: Optional[EventOrder] = None
        self.pending_recovery_at: Optional[datetime] = None
        self.add_event(ev, seq)

    def find_summary(self, ev: ParsedEvent) -> Optional[ComponentSummary]:
        summary = self.summaries.get(ev.profile.name)
        if summary is not None:
            return summary
        for candidate in self.summaries.values():
            if same_logical_component(ev.profile, candidate.profile):
                return candidate
        return None

    def elapsed_from_span(self, ts: datetime) -> float:
        if ts > self.last_seen:
            return (ts - self.last_seen).total_seconds()
        if ts < self.started_at:
            return (self.started_at - ts).total_seconds()
        return 0.0

    def add_event(self, ev: ParsedEvent, seq: int) -> None:
        self.started_at = min(self.started_at, ev.timestamp)
        self.last_seen = max(self.last_seen, ev.timestamp)
        self.event_count += 1

        stored = (ev.timestamp, seq, ev.payload)
        if len(self._anchor_events) < ANCHOR_EVENT_COUNT:
            self._anchor_events.append(stored)
        else:
            self._recent_events.append(stored)

        for key, value in ev.ids.items():
            if value:
                self.ids[key].add(value)

        if ev.is_failure:
            self._record_failure(ev, seq)
        elif ev.is_recovery:
            self._record_recovery(ev, seq)

    def _record_failure(self, ev: ParsedEvent, seq: int) -> None:
        order = (ev.timestamp, seq)
        if self.latest_failure is None or order > self.latest_failure:
            self.latest_failure = order

        summary = self.find_summary(ev)
        if summary is None:
            self.summaries[ev.profile.name] = ComponentSummary(ev, seq)
            changed = True
        else:
            changed = summary.record_failure(ev, seq)

        if changed:
            self.root_cause = rank_root_cause(self.summaries.values())
        self._refresh_pending_recovery()

    def _record_recovery(self, ev: ParsedEvent, seq: int) -> None:
        order = (ev.timestamp, seq)
        for summary in self.summaries.values():
            if recovery_applies_to(ev, summary):
                summary.record_recovery(order)
        self._refresh_pending_recovery()

    def _refresh_pending_recovery(self) -> None:
        """Resolution is pending once the root (or every) component recovered."""
        root_key = self.root_cause.component_key
        if root_key is not None and root_key in self.summaries:
            relevant = [self.summaries[root_key]]
        else:
            relevant = list(self.summaries.values())

        if not relevant or not all(s.is_recovered for s in relevant):
            self.pending_recovery_at = None
            return

        latest_recovery = max(
            s.last_recovery for s in self.summaries.values() if s.is_recovered
        )
        if self.latest_failure is not None and self.latest_failure > latest_recovery:
            self.pending_recovery_at = None
            return

        self.pending_recovery_at = latest_recovery[0]

    def is_stale(self, now: datetime, close_after_seconds: int) -> bool:
        reference = (
            self.pending_recovery_at
            if self.pending_recovery_at is not None
            else self.last_seen
        )
        return (now - reference).total_seconds() >= close_after_seconds

    def snapshot(self) -> dict[str, Any]:
        stored = sorted(
            [*self._anchor_events, *self._recent_events],
            key=lambda item: (item[0], item[1]),
        )
        return {
            "incident_id": self.incident_id,
            "status": self.status,
            "started_at": self.started_at,
            "last_seen": self.last_seen,
            "event_count": self.event_count,
            "events": [item[2].copy() for item in stored],
            "root_cause": self.root_cause.description,
            "root_cause_confidence": self.root_cause.confidence,
        }
