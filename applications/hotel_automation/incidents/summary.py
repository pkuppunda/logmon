"""Per-component failure summaries tracked inside an incident."""

from __future__ import annotations

from datetime import datetime
from typing import Optional

from hotel_automation.incidents.constants import CRITICAL_LEVELS
from hotel_automation.incidents.components import ComponentProfile
from hotel_automation.incidents.validation import ParsedEvent

EventOrder = tuple[datetime, int]


class ComponentSummary:
    """Aggregated failure evidence for one logical component."""

    def __init__(self, ev: ParsedEvent, seq: int) -> None:
        self.profile: ComponentProfile = ev.profile
        self.display_component = ev.profile.display
        self.tokens: set[str] = set(ev.profile.tokens)
        self.earliest: EventOrder = (ev.timestamp, seq)
        self.latest_failure: EventOrder = (ev.timestamp, seq)
        self.last_recovery: Optional[EventOrder] = None
        self.failure_count = 1
        self.has_critical = ev.level in CRITICAL_LEVELS
        self.representative_code = ev.event_code
        self.representative_message = ev.message
        self.event_codes: set[str] = {ev.event_code} if ev.event_code else set()
        self.code_roles: set[str] = set(ev.code_roles)
        self.code_tokens: set[str] = set(ev.code_tokens)

    @property
    def key(self) -> str:
        return self.profile.name

    @property
    def role(self) -> Optional[str]:
        return self.profile.role

    @property
    def earliest_ts(self) -> datetime:
        return self.earliest[0]

    @property
    def is_recovered(self) -> bool:
        return self.last_recovery is not None and self.last_recovery > self.latest_failure

    def record_failure(self, ev: ParsedEvent, seq: int) -> bool:
        """Record another failure; return True when ranking inputs changed."""
        changed = self.failure_count == 1
        self.failure_count += 1
        order = (ev.timestamp, seq)

        if ev.level in CRITICAL_LEVELS and not self.has_critical:
            self.has_critical = True
            changed = True
        if ev.event_code and ev.event_code not in self.event_codes:
            self.event_codes.add(ev.event_code)
            self.code_roles.update(ev.code_roles)
            self.code_tokens.update(ev.code_tokens)
            changed = True
        if not ev.profile.tokens <= self.tokens:
            self.tokens.update(ev.profile.tokens)
            changed = True

        if order > self.latest_failure:
            self.latest_failure = order
        if order < self.earliest:
            self.earliest = order
            self.display_component = ev.profile.display
            self.representative_code = ev.event_code
            self.representative_message = ev.message
            changed = True
        elif self.representative_code is None and ev.event_code is not None:
            self.representative_code = ev.event_code
            changed = True

        return changed

    def record_recovery(self, order: EventOrder) -> None:
        if order > self.latest_failure and (
            self.last_recovery is None or order > self.last_recovery
        ):
            self.last_recovery = order
