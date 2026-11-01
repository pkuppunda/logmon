"""Correlate structured hotel log events into lifecycle-managed incidents.

Behavioural contract
--------------------
* Configuration values are positive integers (booleans rejected) and
  ``close_after_seconds >= correlation_window_seconds``.
* Only failures (ERROR/CRITICAL/FATAL) open incidents. Warnings join an open
  incident only through a shared trace/correlation/request id; recoveries join
  the incident whose failing component they resolve. Other events are ignored.
* A failure joins an open incident when explicit evidence links them (shared
  identifier, same logical component, event-code reference, or architectural
  dependency) and it is within ``correlation_window_seconds`` (inclusive) of
  the incident's ``[started_at, last_seen]`` span. Temporal proximity or
  message similarity alone never merges incidents.
* Ordinary incidents close when ``now - last_seen >= close_after_seconds``.
  Incidents pending recovery close when ``now - recovery >= close_after_seconds``;
  a failure of the same component during that probation cancels the pending
  resolution. Failures after closure open new incidents.
* Each incident retains at most ``MAX_EVENTS_PER_INCIDENT`` event records while
  ``event_count`` stays exact. Closed history is bounded by ``history_limit``.
* Invalid events raise ``ValueError`` without mutating any state, and every
  public accessor returns defensive copies.
"""

from __future__ import annotations

from datetime import datetime
from typing import Optional

from hotel_automation.incidents.components import roles_related
from hotel_automation.incidents.state import STATUS_CLOSED, IncidentState, recovery_applies_to
from hotel_automation.incidents.validation import (
    ParsedEvent,
    parse_event,
    parse_timestamp,
    validate_positive_int,
)

SHARED_ID_SCORES = (
    ("trace_id", 100.0),
    ("correlation_id", 95.0),
    ("request_id", 90.0),
)


class IncidentCorrelator:
    """Correlate structured log events into incidents with root-cause ranking."""

    def __init__(
        self,
        correlation_window_seconds: int = 60,
        close_after_seconds: int = 120,
        history_limit: int = 500,
    ) -> None:
        self.correlation_window_seconds = validate_positive_int(
            "correlation_window_seconds", correlation_window_seconds
        )
        self.close_after_seconds = validate_positive_int(
            "close_after_seconds", close_after_seconds
        )
        self.history_limit = validate_positive_int("history_limit", history_limit)
        if self.close_after_seconds < self.correlation_window_seconds:
            raise ValueError(
                "close_after_seconds must be greater than or equal to "
                "correlation_window_seconds"
            )

        self._next_incident_number = 1
        self._next_sequence = 1
        self._active: dict[str, IncidentState] = {}
        self._history: dict[str, IncidentState] = {}

    # ------------------------------------------------------------------ API

    def observe(self, event: dict) -> Optional[dict]:
        """Process one event; return the affected incident snapshot or None."""
        parsed = parse_event(event)

        if parsed.is_recovery:
            incident = self._match_recovery(parsed)
        elif parsed.is_warning:
            incident = self._match_by_shared_id(parsed)
        elif parsed.is_failure:
            incident = self._match_failure(parsed)
            if incident is None:
                incident = self._open_incident(parsed)
                return incident.snapshot()
        else:
            return None

        if incident is None:
            return None

        incident.add_event(parsed, self._take_sequence())
        return incident.snapshot()

    @property
    def active_incidents(self) -> list[dict]:
        return [incident.snapshot() for incident in self._active.values()]

    @property
    def incident_history(self) -> list[dict]:
        return [incident.snapshot() for incident in self._history.values()]

    def get_incident(self, incident_id: str) -> Optional[dict]:
        if not isinstance(incident_id, str):
            return None
        incident = self._active.get(incident_id) or self._history.get(incident_id)
        return incident.snapshot() if incident is not None else None

    def close_stale_incidents(self, now: datetime) -> list[dict]:
        """Close incidents that are stale at ``now``; return closed snapshots."""
        current = parse_timestamp(now, field_name="now")
        stale = [
            incident
            for incident in self._active.values()
            if incident.is_stale(current, self.close_after_seconds)
        ]

        closed = []
        for incident in stale:
            del self._active[incident.incident_id]
            incident.status = STATUS_CLOSED
            self._history[incident.incident_id] = incident
            closed.append(incident.snapshot())

        while len(self._history) > self.history_limit:
            del self._history[next(iter(self._history))]

        return closed

    # ------------------------------------------------------------ internals

    def _take_sequence(self) -> int:
        seq = self._next_sequence
        self._next_sequence += 1
        return seq

    def _open_incident(self, ev: ParsedEvent) -> IncidentState:
        incident_id = f"INC-{self._next_incident_number:06d}"
        self._next_incident_number += 1
        incident = IncidentState(incident_id, ev, self._take_sequence())
        self._active[incident_id] = incident
        return incident

    def _within_window(self, ev: ParsedEvent, incident: IncidentState) -> bool:
        return incident.elapsed_from_span(ev.timestamp) <= self.correlation_window_seconds

    def _within_probation(self, ev: ParsedEvent, incident: IncidentState) -> bool:
        pending = incident.pending_recovery_at
        if pending is None or ev.timestamp < pending:
            return False
        if (ev.timestamp - pending).total_seconds() >= self.close_after_seconds:
            return False
        return incident.find_summary(ev) is not None

    def _match_failure(self, ev: ParsedEvent) -> Optional[IncidentState]:
        best: Optional[IncidentState] = None
        best_key = None

        for incident in self._active.values():
            if not (
                self._within_window(ev, incident)
                or self._within_probation(ev, incident)
            ):
                continue

            score = self._correlation_score(ev, incident)
            if score <= 0.0:
                continue

            key = (score, -incident.elapsed_from_span(ev.timestamp), incident.last_seen)
            if best_key is None or key > best_key:
                best, best_key = incident, key

        return best

    def _match_by_shared_id(self, ev: ParsedEvent) -> Optional[IncidentState]:
        best: Optional[IncidentState] = None
        best_elapsed = None
        for incident in self._active.values():
            if self._shared_id_score(ev, incident) <= 0.0:
                continue
            elapsed = incident.elapsed_from_span(ev.timestamp)
            if elapsed > self.correlation_window_seconds:
                continue
            if best_elapsed is None or elapsed < best_elapsed:
                best, best_elapsed = incident, elapsed
        return best

    def _match_recovery(self, ev: ParsedEvent) -> Optional[IncidentState]:
        best: Optional[IncidentState] = None
        best_rank = None

        for incident in self._active.values():
            relevant = [
                summary
                for summary in incident.summaries.values()
                if recovery_applies_to(ev, summary) and ev.timestamp >= summary.earliest_ts
            ]
            if not relevant:
                continue

            root_key = incident.root_cause.component_key
            is_root = any(summary.key == root_key for summary in relevant)
            rank = (is_root, incident.last_seen)
            if best_rank is None or rank > best_rank:
                best, best_rank = incident, rank

        return best

    @staticmethod
    def _shared_id_score(ev: ParsedEvent, incident: IncidentState) -> float:
        for key, score in SHARED_ID_SCORES:
            value = ev.ids[key]
            if value and value in incident.ids[key]:
                return score
        return 0.0

    @staticmethod
    def _conflicting_ids(ev: ParsedEvent, incident: IncidentState, keys) -> bool:
        return any(
            ev.ids[key] and incident.ids[key] and ev.ids[key] not in incident.ids[key]
            for key in keys
        )

    def _correlation_score(self, ev: ParsedEvent, incident: IncidentState) -> float:
        shared = self._shared_id_score(ev, incident)
        if shared > 0.0:
            return shared

        same_component = incident.find_summary(ev)
        if same_component is not None:
            if self._conflicting_ids(ev, incident, ("trace_id", "correlation_id")):
                if ev.event_code and ev.event_code in same_component.event_codes:
                    return 65.0
                return 0.0
            if ev.event_code and ev.event_code in same_component.event_codes:
                return 75.0
            return 60.0

        if self._conflicting_ids(ev, incident, ("trace_id", "correlation_id", "request_id")):
            return 0.0

        summaries = list(incident.summaries.values())
        incident_roles = {s.role for s in summaries if s.role is not None}
        incident_tokens = {tok for s in summaries for tok in s.tokens}
        incident_code_roles = {r for s in summaries for r in s.code_roles if r != s.role}

        foreign_code_roles = ev.code_roles - {ev.profile.role}
        foreign_code_tokens = ev.code_tokens - ev.profile.tokens
        references_incident = bool(
            foreign_code_roles & incident_roles or foreign_code_tokens & incident_tokens
        )
        referenced_by_incident = ev.profile.role in incident_code_roles
        if references_incident or referenced_by_incident:
            return 70.0

        if ev.profile.role is None or ev.profile.role in incident_roles:
            return 0.0

        incident_qualifiers = {q for s in summaries for q in s.profile.qualifiers}
        qualifiers_compatible = (
            not ev.profile.qualifiers and not incident_qualifiers
        ) or bool(ev.profile.qualifiers & incident_qualifiers)
        if qualifiers_compatible and any(
            roles_related(ev.profile.role, role) for role in incident_roles
        ):
            return 45.0

        return 0.0
