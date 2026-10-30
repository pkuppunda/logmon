"""Deterministic incident correlation, root-cause ranking, and lifecycle management.

Design and Behavioral Contracts
-------------------------------
1. Timeout Configuration Relationship:
   ``correlation_window_seconds``, ``close_after_seconds``, and ``history_limit``
   must be positive integers (booleans are explicitly rejected). Additionally,
   ``close_after_seconds >= correlation_window_seconds`` is required and validated
   so an active incident cannot become stale and close before its correlation
   window expires.

2. Correlation Window and Out-of-Order Events:
   Related events correlate into an active incident when explicit evidence exists
   (shared ``trace_id`` / ``correlation_id`` / ``request_id``, same component
   with compatible failure domain, cross-component ``event_code`` reference, or
   architectural dependency alignment) and ``elapsed_seconds <= correlation_window_seconds``
   relative to the incident's ``[started_at, last_seen]`` span. Temporal proximity
   or message similarity alone never merges incidents. Out-of-order timestamps
   update ``started_at = min(...)`` and ``last_seen = max(...)`` without rewinding
   ``last_seen`` or corrupting pending recovery state.

3. Exact Closure-Boundary Behavior:
   - Ordinary incidents (not pending recovery resolution) close in
     ``close_stale_incidents(now)`` when ``(now - last_seen).total_seconds() >= close_after_seconds``.
   - Pending-resolution incidents close only in ``close_stale_incidents(now)``
     when ``(now - pending_recovery_at).total_seconds() >= close_after_seconds``
     and no relevant failure occurred after that recovery.
   - Any relevant failure ordered after a recovery during the probation period
     cancels pending resolution and retains the open incident. Failures observed
     after closure open a new incident rather than mutating closed history records.

4. Duplicate-Event Behavior:
   Repeated failure events with identical timestamps, components, codes, and
   messages within the correlation window correlate into the same open incident
   (preventing duplicate incidents) while incrementing ``event_count`` so genuine
   repeated failures occurring within 1-second timestamp resolution are never
   discarded.

5. Bounded Per-Incident Event Retention:
   To keep memory and defensive-copy overhead bounded for long-running incidents
   (e.g., 100,000+ events), each incident retains up to ``MAX_EVENTS_PER_INCIDENT``
   (64) event records in ``events`` (preserving the earliest ``ANCHOR_EVENT_COUNT``
   initiating events plus the most recent rolling events), while ``event_count``
   records the exact total number of correlated events.
"""

from __future__ import annotations

from collections import deque
from functools import lru_cache
from datetime import date, datetime
import re
from typing import Any

TIMESTAMP_FORMAT = "%Y-%m-%d %H:%M:%S"
_STRICT_TIMESTAMP_RE = re.compile(r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}$")
_TOKEN_SPLIT_RE = re.compile(r"[^a-z0-9]+")

VALID_LEVELS = frozenset(
    {"DEBUG", "INFO", "WARNING", "WARN", "ERROR", "CRITICAL", "FATAL"}
)
FAILURE_LEVELS = frozenset({"ERROR", "CRITICAL", "FATAL"})
WARNING_LEVELS = frozenset({"WARNING", "WARN"})

RECOVERY_EVENT_CODES = frozenset(
    {
        "SERVICE_RECOVERED",
        "DB_CONNECTION_RESTORED",
        "PAYMENT_GATEWAY_RECOVERED",
        "NOTIFICATION_SERVICE_RECOVERED",
        "BOOKING_SERVICE_RECOVERED",
        "REPORTING_SERVICE_RECOVERED",
    }
)

_RECOVERY_CODE_TO_ROLE: dict[str, str] = {
    "DB_CONNECTION_RESTORED": "database",
    "PAYMENT_GATEWAY_RECOVERED": "payment_gateway",
    "NOTIFICATION_SERVICE_RECOVERED": "notification_service",
    "BOOKING_SERVICE_RECOVERED": "booking_service",
    "REPORTING_SERVICE_RECOVERED": "reporting_service",
}

MAX_EVENTS_PER_INCIDENT = 64
ANCHOR_EVENT_COUNT = 16

_COMPONENT_TOKEN_TO_ROLE: dict[str, str] = {
    "db": "database",
    "database": "database",
    "postgres": "database",
    "postgresql": "database",
    "mysql": "database",
    "sqlite": "database",
    "mongodb": "database",
    "datastore": "database",
    "rds": "database",
    "sql": "database",
    "cache": "cache",
    "redis": "cache",
    "memcached": "cache",
    "queue": "queue",
    "broker": "queue",
    "kafka": "queue",
    "rabbitmq": "queue",
    "sqs": "queue",
    "network": "network",
    "dns": "network",
    "auth": "auth",
    "identity": "auth",
    "oauth": "auth",
    "sso": "auth",
    "iam": "auth",
    "payment": "payment_gateway",
    "payments": "payment_gateway",
    "billing": "payment_gateway",
    "stripe": "payment_gateway",
    "booking": "booking_service",
    "bookings": "booking_service",
    "reservation": "booking_service",
    "reservations": "booking_service",
    "order": "booking_service",
    "orders": "booking_service",
    "reporting": "reporting_service",
    "report": "reporting_service",
    "reports": "reporting_service",
    "analytics": "reporting_service",
    "notification": "notification_service",
    "notifications": "notification_service",
    "mailer": "notification_service",
    "email": "notification_service",
    "sms": "notification_service",
    "api": "api_gateway",
    "gateway": "api_gateway",
    "frontend": "api_gateway",
    "web": "api_gateway",
    "router": "api_gateway",
    "ingress": "api_gateway",
}

_HEAD_INFRA_ROLES = frozenset({"database", "cache", "queue", "network"})

_DEPENDS_ON: dict[str, frozenset[str]] = {
    "database": frozenset({"network", "dns"}),
    "cache": frozenset({"network", "dns"}),
    "queue": frozenset({"network", "dns"}),
    "auth": frozenset({"database", "cache", "network", "dns"}),
    "payment_gateway": frozenset({"network", "dns"}),
    "booking_service": frozenset(
        {"database", "cache", "payment_gateway", "auth", "queue"}
    ),
    "reporting_service": frozenset({"database", "cache"}),
    "notification_service": frozenset({"queue", "booking_service"}),
    "api_gateway": frozenset(
        {
            "auth",
            "booking_service",
            "reporting_service",
            "payment_gateway",
            "database",
            "cache",
        }
    ),
}

_DEPENDED_BY: dict[str, frozenset[str]] = {
    upstream: frozenset(
        downstream
        for downstream, upstreams in _DEPENDS_ON.items()
        if upstream in upstreams
    )
    for upstream in set(_DEPENDS_ON).union(*_DEPENDS_ON.values())
}

_INFRA_ROLES = frozenset(
    {"database", "cache", "queue", "network", "dns", "auth", "payment_gateway"}
)
_EDGE_ROLES = frozenset({"api_gateway", "notification_service"})

_STOP_COMPONENT_TOKENS = frozenset(
    {
        "service",
        "services",
        "srv",
        "svc",
        "app",
        "application",
        "module",
        "component",
        "worker",
        "manager",
        "handler",
        "client",
        "server",
        "engine",
        "system",
        "core",
        "gateway",
    }
)

_INITIATING_CODE_TOKENS = frozenset(
    {
        "CONNECTION_FAILED",
        "CONNECTION_REFUSED",
        "CONNECTION_LOST",
        "CONNECTION_RESET",
        "POOL_EXHAUSTED",
        "DISK_FULL",
        "OUT_OF_MEMORY",
        "OOM",
        "DEADLOCK",
        "CORRUPTION",
        "CRASH",
        "UNREACHABLE",
        "DNS_FAILURE",
        "CERT_EXPIRED",
        "QUOTA_EXCEEDED",
        "REFUSED",
        "EXHAUSTED",
    }
)

_SYMPTOM_ONLY_CODES = frozenset(
    {
        "UPSTREAM_ERROR",
        "UPSTREAM_UNAVAILABLE",
        "UPSTREAM_TIMEOUT",
        "UPSTREAM_FAILURE",
        "DEPENDENCY_FAILED",
        "DEPENDENCY_ERROR",
        "DEPENDENCY_UNAVAILABLE",
        "CASCADING_FAILURE",
        "DOWNSTREAM_ERROR",
        "SECONDARY_FAILURE",
    }
)

_SYMPTOM_CODE_SUFFIXES = (
    "_SAVE_FAILED",
    "_SKIPPED",
    "_ABORTED",
    "_CANCELED",
    "_CANCELLED",
    "_FALLBACK",
)

_CLIENT_VALIDATION_TOKENS = frozenset(
    {
        "VALIDATION",
        "INVALID",
        "MALFORMED",
        "BAD_REQUEST",
        "NOT_FOUND",
        "UNAUTHORIZED",
        "FORBIDDEN",
        "PERMISSION_DENIED",
    }
)

_OPAQUE_MESSAGES = frozenset(
    {
        "error",
        "unknown error",
        "unexpected error",
        "unspecified error",
        "unspecified failure",
        "undetermined error",
        "operation failed",
        "failed",
        "failure",
        "something went wrong",
    }
)

_SYMPTOM_MESSAGE_PHRASES = (
    "upstream service failed",
    "upstream error",
    "upstream unavailable",
    "cascading failure",
    "secondary failure",
    "dependency failed",
)

_CAUSAL_DOWNSTREAM_PHRASES = (
    "due to ",
    "caused by ",
    "because of ",
    "while saving ",
    "waiting for ",
    "upstream ",
)

_OPTIONAL_STRING_FIELDS = (
    "event_code",
    "request_id",
    "correlation_id",
    "trace_id",
)


def _validate_positive_int(name: str, value: Any) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ValueError(
            f"{name} must be a positive integer (booleans are not allowed); "
            f"got {value!r}"
        )
    return value


def _parse_timestamp(raw_ts: Any, field_name: str = "timestamp") -> datetime:
    if raw_ts is None:
        raise ValueError(f"Missing required {field_name}: value cannot be None")
    if isinstance(raw_ts, bool):
        raise ValueError(
            f"Unsupported {field_name} type bool: expected datetime or "
            f"'{TIMESTAMP_FORMAT}' string"
        )
    if isinstance(raw_ts, datetime):
        if raw_ts.tzinfo is not None and raw_ts.utcoffset() is not None:
            raise ValueError(
                f"Unsupported timezone-aware {field_name}: expected naive "
                f"datetime or '{TIMESTAMP_FORMAT}' string"
            )
        return raw_ts
    if isinstance(raw_ts, date):
        raise ValueError(
            f"Unsupported {field_name} type date: expected datetime or "
            f"'{TIMESTAMP_FORMAT}' string"
        )
    if isinstance(raw_ts, str):
        if not _STRICT_TIMESTAMP_RE.match(raw_ts):
            raise ValueError(
                f"Malformed or unsupported {field_name} {raw_ts!r}: "
                f"expected '{TIMESTAMP_FORMAT}'"
            )
        try:
            return datetime.strptime(raw_ts, TIMESTAMP_FORMAT)
        except ValueError as exc:
            raise ValueError(
                f"Malformed {field_name} {raw_ts!r}: {exc}"
            ) from exc
    raise ValueError(
        f"Unsupported {field_name} type {type(raw_ts).__name__}: "
        f"expected datetime or '{TIMESTAMP_FORMAT}' string"
    )


@lru_cache(maxsize=2048)
def _normalize_component(
    component: str,
) -> tuple[str, tuple[str, ...], str | None, frozenset[str]]:
    lowered = component.strip().lower()
    tokens = tuple(tok for tok in _TOKEN_SPLIT_RE.split(lowered) if tok)
    norm_name = "_".join(tokens) if tokens else lowered

    non_stop = [tok for tok in tokens if tok not in _STOP_COMPONENT_TOKENS]
    role: str | None = _COMPONENT_TOKEN_TO_ROLE.get(norm_name)
    if role is None and non_stop:
        tail_role = _COMPONENT_TOKEN_TO_ROLE.get(non_stop[-1])
        if tail_role in _HEAD_INFRA_ROLES:
            role = tail_role
    if role is None:
        for tok in non_stop:
            mapped = _COMPONENT_TOKEN_TO_ROLE.get(tok)
            if mapped is not None:
                role = mapped
                break
        if role is None:
            for tok in tokens:
                mapped = _COMPONENT_TOKEN_TO_ROLE.get(tok)
                if mapped is not None:
                    role = mapped
                    break

    distinctive_tokens = tuple(tok for tok in non_stop if len(tok) >= 2)
    qualifier_tokens = frozenset(
        tok
        for tok in distinctive_tokens
        if _COMPONENT_TOKEN_TO_ROLE.get(tok) != role
    )
    return norm_name, distinctive_tokens, role, qualifier_tokens


def _same_logical_component(
    norm_a: str,
    role_a: str | None,
    qual_a: frozenset[str],
    norm_b: str,
    role_b: str | None,
    qual_b: frozenset[str],
) -> bool:
    if norm_a == norm_b:
        return True
    if role_a is not None and role_a == role_b:
        if not qual_a and not qual_b:
            return True
    return False


@lru_cache(maxsize=2048)
def _extract_code_roles_and_tokens(
    event_code: str | None,
) -> tuple[frozenset[str], frozenset[str]]:
    if not event_code:
        return frozenset(), frozenset()
    tokens = [tok for tok in _TOKEN_SPLIT_RE.split(event_code.lower()) if tok]
    roles: set[str] = set()
    sig_tokens: set[str] = set()
    for tok in tokens:
        if tok not in _STOP_COMPONENT_TOKENS and len(tok) >= 2:
            sig_tokens.add(tok)
        mapped = _COMPONENT_TOKEN_TO_ROLE.get(tok)
        if mapped is not None and tok != "gateway":
            roles.add(mapped)
    if "payment" in tokens and "gateway" in tokens:
        roles.add("payment_gateway")
    return frozenset(roles), frozenset(sig_tokens)


@lru_cache(maxsize=2048)
def _is_client_validation_code(event_code: str | None) -> bool:
    if not event_code:
        return False
    upper = event_code.upper()
    parts = set(upper.split("_"))
    return bool(parts & _CLIENT_VALIDATION_TOKENS) or any(
        tok in upper for tok in ("BAD_REQUEST", "NOT_FOUND", "PERMISSION_DENIED")
    )


class _ParsedEvent:
    __slots__ = (
        "payload",
        "timestamp",
        "level",
        "component",
        "norm_component",
        "component_tokens",
        "role",
        "qualifiers",
        "message",
        "event_code",
        "code_roles",
        "code_tokens",
        "request_id",
        "correlation_id",
        "trace_id",
        "is_recovery",
        "is_failure",
        "is_warning",
    )

    def __init__(
        self,
        payload: dict[str, Any],
        timestamp: datetime,
        level: str,
        component: str,
        norm_component: str,
        component_tokens: tuple[str, ...],
        role: str | None,
        qualifiers: frozenset[str],
        message: str,
        event_code: str | None,
        code_roles: frozenset[str],
        code_tokens: frozenset[str],
        request_id: str | None,
        correlation_id: str | None,
        trace_id: str | None,
    ) -> None:
        self.payload = payload
        self.timestamp = timestamp
        self.level = level
        self.component = component
        self.norm_component = norm_component
        self.component_tokens = component_tokens
        self.role = role
        self.qualifiers = qualifiers
        self.message = message
        self.event_code = event_code
        self.code_roles = code_roles
        self.code_tokens = code_tokens
        self.request_id = request_id
        self.correlation_id = correlation_id
        self.trace_id = trace_id
        self.is_recovery = bool(
            event_code and event_code.upper() in RECOVERY_EVENT_CODES
        )
        self.is_failure = (not self.is_recovery) and (level in FAILURE_LEVELS)
        self.is_warning = (not self.is_recovery) and (level in WARNING_LEVELS)


def _validate_and_parse_event(event: Any) -> _ParsedEvent:
    if not isinstance(event, dict):
        raise ValueError(
            f"Invalid event: expected dict, got {type(event).__name__}"
        )

    if "timestamp" not in event:
        raise ValueError("Missing required field 'timestamp' in event")
    timestamp = _parse_timestamp(event["timestamp"], field_name="timestamp")

    if "level" not in event or event["level"] is None:
        raise ValueError("Missing required field 'level' in event")
    raw_level = event["level"]
    if not isinstance(raw_level, str) or not raw_level.strip():
        raise ValueError(f"Invalid 'level' in event: {raw_level!r}")
    level = raw_level.strip().upper()
    if level not in VALID_LEVELS:
        raise ValueError(f"Unsupported log level {raw_level!r}")

    if "component" not in event or event["component"] is None:
        raise ValueError("Missing required field 'component' in event")
    raw_component = event["component"]
    if not isinstance(raw_component, str) or not raw_component.strip():
        raise ValueError(f"Invalid 'component' in event: {raw_component!r}")
    component = raw_component.strip()

    if "message" not in event or event["message"] is None:
        raise ValueError("Missing required field 'message' in event")
    raw_message = event["message"]
    if not isinstance(raw_message, str) or not raw_message.strip():
        raise ValueError(f"Invalid 'message' in event: {raw_message!r}")
    message = raw_message

    optional_vals: dict[str, str | None] = {}
    for key in _OPTIONAL_STRING_FIELDS:
        if key in event:
            val = event[key]
            if val is None:
                optional_vals[key] = None
            elif isinstance(val, str):
                stripped = val.strip()
                optional_vals[key] = stripped if stripped else None
            else:
                raise ValueError(
                    f"Optional field {key!r} must be a string or None, "
                    f"got {type(val).__name__}"
                )

    payload: dict[str, Any] = {
        "timestamp": timestamp,
        "level": level,
        "component": component,
        "message": message,
    }
    for key in _OPTIONAL_STRING_FIELDS:
        if key in event:
            payload[key] = (
                event[key].strip() if isinstance(event[key], str) else None
            )

    event_code = optional_vals.get("event_code")
    if event_code is not None:
        event_code = event_code.upper()
        payload["event_code"] = event_code

    norm_comp, comp_tokens, role, qualifiers = _normalize_component(component)
    code_roles, code_tokens = _extract_code_roles_and_tokens(event_code)

    return _ParsedEvent(
        payload=payload,
        timestamp=timestamp,
        level=level,
        component=component,
        norm_component=norm_comp,
        component_tokens=comp_tokens,
        role=role,
        qualifiers=qualifiers,
        message=message,
        event_code=event_code,
        code_roles=code_roles,
        code_tokens=code_tokens,
        request_id=optional_vals.get("request_id"),
        correlation_id=optional_vals.get("correlation_id"),
        trace_id=optional_vals.get("trace_id"),
    )


class _ComponentSummary:
    __slots__ = (
        "norm_component",
        "display_component",
        "role",
        "qualifiers",
        "tokens",
        "earliest_ts",
        "earliest_seq",
        "latest_failure_order",
        "failure_count",
        "has_critical",
        "representative_code",
        "representative_message",
        "event_codes",
        "code_roles",
        "code_tokens",
        "last_recovery_order",
    )

    def __init__(self, ev: _ParsedEvent, seq: int) -> None:
        self.norm_component = ev.norm_component
        self.display_component = ev.component
        self.role = ev.role
        self.qualifiers = ev.qualifiers
        self.tokens = set(ev.component_tokens)
        self.earliest_ts = ev.timestamp
        self.earliest_seq = seq
        self.latest_failure_order: tuple[datetime, int] = (ev.timestamp, seq)
        self.failure_count = 1
        self.has_critical = ev.level in {"CRITICAL", "FATAL"}
        self.representative_code = ev.event_code
        self.representative_message = ev.message
        self.event_codes: set[str] = {ev.event_code} if ev.event_code else set()
        self.code_roles: set[str] = set(ev.code_roles)
        self.code_tokens: set[str] = set(ev.code_tokens)
        self.last_recovery_order: tuple[datetime, int] | None = None

    def update(self, ev: _ParsedEvent, seq: int) -> bool:
        changed = self.failure_count == 1
        self.failure_count += 1
        if ev.level in {"CRITICAL", "FATAL"} and not self.has_critical:
            self.has_critical = True
            changed = True
        if ev.event_code and ev.event_code not in self.event_codes:
            self.event_codes.add(ev.event_code)
            self.code_roles.update(ev.code_roles)
            self.code_tokens.update(ev.code_tokens)
            changed = True
        if ev.component_tokens and not set(ev.component_tokens).issubset(self.tokens):
            self.tokens.update(ev.component_tokens)
            changed = True
        order = (ev.timestamp, seq)
        if order > self.latest_failure_order:
            self.latest_failure_order = order
        if order < (self.earliest_ts, self.earliest_seq):
            self.earliest_ts = ev.timestamp
            self.earliest_seq = seq
            self.display_component = ev.component
            self.representative_code = ev.event_code
            self.representative_message = ev.message
            changed = True
        elif self.representative_code is None and ev.event_code is not None:
            self.representative_code = ev.event_code
            changed = True
        return changed


class _IncidentState:
    __slots__ = (
        "incident_id",
        "status",
        "started_at",
        "last_seen",
        "event_count",
        "_anchor_events",
        "_recent_events",
        "_events_sorted",
        "root_cause",
        "root_cause_confidence",
        "root_norm_component",
        "component_summaries",
        "trace_ids",
        "correlation_ids",
        "request_ids",
        "indexed_components",
        "indexed_roles",
        "indexed_tokens",
        "pending_recovery_at",
        "latest_failure_order",
    )

    def __init__(self, incident_id: str, ev: _ParsedEvent, seq: int) -> None:
        self.incident_id = incident_id
        self.status = "open"
        self.started_at = ev.timestamp
        self.last_seen = ev.timestamp
        self.event_count = 0
        self._anchor_events: list[tuple[datetime, int, dict[str, Any]]] = []
        self._recent_events: deque[tuple[datetime, int, dict[str, Any]]] | None = None
        self._events_sorted = True
        self.root_cause: str | None = None
        self.root_cause_confidence: float | None = None
        self.root_norm_component: str | None = None
        self.component_summaries: dict[str, _ComponentSummary] = {}
        self.trace_ids: set[str] = set()
        self.correlation_ids: set[str] = set()
        self.request_ids: set[str] = set()
        self.indexed_components: set[str] = set()
        self.indexed_roles: set[str] = set()
        self.indexed_tokens: set[str] = set()
        self.pending_recovery_at: datetime | None = None
        self.latest_failure_order: tuple[datetime, int] | None = None
        self.add_event(ev, seq)

    def _find_matching_summary_key(self, ev: _ParsedEvent) -> str:
        if ev.norm_component in self.component_summaries:
            return ev.norm_component
        for key, cs in self.component_summaries.items():
            if _same_logical_component(
                ev.norm_component,
                ev.role,
                ev.qualifiers,
                cs.norm_component,
                cs.role,
                cs.qualifiers,
            ):
                return key
        return ev.norm_component

    def add_event(self, ev: _ParsedEvent, seq: int) -> bool:
        """Record event into incident; returns True if last_seen increased."""
        prev_last_seen = self.last_seen
        if self.event_count > 0 and ev.timestamp < prev_last_seen:
            self._events_sorted = False

        if ev.timestamp < self.started_at:
            self.started_at = ev.timestamp
        advanced_last_seen = False
        if ev.timestamp > self.last_seen:
            self.last_seen = ev.timestamp
            advanced_last_seen = True

        self.event_count += 1
        item = (ev.timestamp, seq, ev.payload)
        if len(self._anchor_events) < ANCHOR_EVENT_COUNT:
            self._anchor_events.append(item)
        else:
            if self._recent_events is None:
                self._recent_events = deque(
                    maxlen=MAX_EVENTS_PER_INCIDENT - ANCHOR_EVENT_COUNT
                )
            self._recent_events.append(item)

        if ev.trace_id:
            self.trace_ids.add(ev.trace_id)
        if ev.correlation_id:
            self.correlation_ids.add(ev.correlation_id)
        if ev.request_id:
            self.request_ids.add(ev.request_id)

        if ev.is_failure:
            order = (ev.timestamp, seq)
            if (
                self.latest_failure_order is None
                or order > self.latest_failure_order
            ):
                self.latest_failure_order = order

            comp_key = self._find_matching_summary_key(ev)
            summary = self.component_summaries.get(comp_key)
            if summary is None:
                self.component_summaries[comp_key] = _ComponentSummary(ev, seq)
                rc_changed = True
            else:
                rc_changed = summary.update(ev, seq)

            if rc_changed:
                self._recompute_root_cause()
            self._refresh_pending_recovery()

        elif ev.is_recovery:
            rec_order = (ev.timestamp, seq)
            specific_role = _RECOVERY_CODE_TO_ROLE.get(ev.event_code or "")
            for comp_sum in self.component_summaries.values():
                matches_comp = _same_logical_component(
                    ev.norm_component,
                    ev.role,
                    ev.qualifiers,
                    comp_sum.norm_component,
                    comp_sum.role,
                    comp_sum.qualifiers,
                )
                matches_role = (
                    specific_role is not None
                    and comp_sum.role == specific_role
                    and (
                        not ev.qualifiers
                        or not comp_sum.qualifiers
                        or ev.qualifiers == comp_sum.qualifiers
                    )
                )
                if matches_comp or matches_role:
                    if rec_order > comp_sum.latest_failure_order:
                        if (
                            comp_sum.last_recovery_order is None
                            or rec_order > comp_sum.last_recovery_order
                        ):
                            comp_sum.last_recovery_order = rec_order
            self._refresh_pending_recovery()

        return advanced_last_seen

    def _refresh_pending_recovery(self) -> None:
        if not self.component_summaries:
            self.pending_recovery_at = None
            return

        if (
            self.root_norm_component is not None
            and self.root_norm_component in self.component_summaries
        ):
            root_sum = self.component_summaries[self.root_norm_component]
            if (
                root_sum.last_recovery_order is None
                or root_sum.last_recovery_order <= root_sum.latest_failure_order
            ):
                self.pending_recovery_at = None
                return
            latest_rec_order = root_sum.last_recovery_order
            for comp_sum in self.component_summaries.values():
                if (
                    comp_sum.last_recovery_order is not None
                    and comp_sum.last_recovery_order
                    > comp_sum.latest_failure_order
                    and comp_sum.last_recovery_order > latest_rec_order
                ):
                    latest_rec_order = comp_sum.last_recovery_order
            if (
                self.latest_failure_order is not None
                and self.latest_failure_order > latest_rec_order
            ):
                self.pending_recovery_at = None
                return
            self.pending_recovery_at = latest_rec_order[0]
        else:
            rec_orders: list[tuple[datetime, int]] = []
            for comp_sum in self.component_summaries.values():
                if (
                    comp_sum.last_recovery_order is None
                    or comp_sum.last_recovery_order
                    <= comp_sum.latest_failure_order
                ):
                    self.pending_recovery_at = None
                    return
                rec_orders.append(comp_sum.last_recovery_order)
            latest_rec_order = max(rec_orders)
            if (
                self.latest_failure_order is not None
                and self.latest_failure_order > latest_rec_order
            ):
                self.pending_recovery_at = None
                return
            self.pending_recovery_at = latest_rec_order[0]

    def _recompute_root_cause(self) -> None:
        summaries = list(self.component_summaries.values())
        if not summaries:
            self.root_cause = None
            self.root_cause_confidence = None
            self.root_norm_component = None
            return

        if len(summaries) == 1:
            comp = summaries[0]
            if self._is_symptom_only_or_opaque(comp):
                self.root_cause = None
                self.root_cause_confidence = None
                self.root_norm_component = None
                return

            conf = 0.58
            if comp.representative_code:
                conf += 0.10
                if self._has_initiating_code(comp):
                    conf += 0.07
            if comp.role in _INFRA_ROLES:
                conf += 0.06
            if comp.has_critical:
                conf += 0.05
            if comp.failure_count >= 2:
                conf += 0.04
            conf = round(min(0.85, max(0.50, conf)), 2)

            self.root_norm_component = comp.norm_component
            self.root_cause = self._format_root_cause(comp)
            self.root_cause_confidence = conf
            return

        min_ts = min(c.earliest_ts for c in summaries)
        max_ts = max(c.earliest_ts for c in summaries)
        scored: list[tuple[float, _ComponentSummary, bool]] = []

        for cand in summaries:
            score = 0.0
            downstream_count = 0
            upstream_count = 0

            for other in summaries:
                if other.norm_component == cand.norm_component:
                    continue
                if self._component_depends_on(other, cand):
                    downstream_count += 1
                if self._component_depends_on(cand, other):
                    upstream_count += 1

            score += min(50.0, downstream_count * 35.0)
            score -= min(50.0, upstream_count * 35.0)

            if self._has_initiating_code(cand):
                score += 15.0
            elif cand.representative_code and not self._has_symptom_markers(cand):
                score += 4.0

            if self._has_symptom_markers(cand):
                score -= 25.0

            if cand.role in _INFRA_ROLES:
                score += 10.0
            elif cand.role in _EDGE_ROLES:
                score -= 5.0

            if cand.earliest_ts == min_ts and max_ts > min_ts:
                score += 15.0
            elif cand.earliest_ts > min_ts:
                delay = (cand.earliest_ts - min_ts).total_seconds()
                score -= min(15.0, delay * 1.5)

            if cand.has_critical:
                score += 8.0

            has_dep_support = downstream_count > 0 and upstream_count == 0
            scored.append((score, cand, has_dep_support))

        scored.sort(
            key=lambda item: (
                item[0],
                -item[1].earliest_ts.timestamp(),
                -item[1].earliest_seq,
            ),
            reverse=True,
        )

        best_score, best_cand, best_has_dep = scored[0]
        second_score = scored[1][0]
        margin = best_score - second_score

        if best_score < -10.0 or margin < 5.0:
            self.root_cause = None
            self.root_cause_confidence = None
            self.root_norm_component = None
            return

        conf = 0.50 + min(0.40, margin / 120.0)
        if best_has_dep and best_cand.earliest_ts == min_ts:
            conf += 0.05
        elif best_cand.earliest_ts > min_ts:
            conf = min(conf, 0.74)

        conf = round(min(0.95, max(0.35, conf)), 2)
        self.root_norm_component = best_cand.norm_component
        self.root_cause = self._format_root_cause(best_cand)
        self.root_cause_confidence = conf

    @staticmethod
    def _component_depends_on(
        downstream: _ComponentSummary, upstream: _ComponentSummary
    ) -> bool:
        if (
            downstream.role is not None
            and upstream.role is not None
            and downstream.role != upstream.role
        ):
            if upstream.role in _DEPENDS_ON.get(downstream.role, frozenset()):
                return True
        if upstream.role is not None and upstream.role in downstream.code_roles:
            if downstream.role != upstream.role:
                return True
        if upstream.tokens and (downstream.code_tokens & upstream.tokens):
            if not (upstream.code_tokens & downstream.tokens):
                return True
        return False

    @staticmethod
    def _has_initiating_code(comp: _ComponentSummary) -> bool:
        for code in comp.event_codes:
            if code in _INITIATING_CODE_TOKENS:
                return True
            for tok in _INITIATING_CODE_TOKENS:
                if tok in code:
                    return True
            if comp.role in _INFRA_ROLES and (
                "TIMEOUT" in code or "UNAVAILABLE" in code or "FAILED" in code
            ):
                return True
        return False

    @staticmethod
    def _has_symptom_markers(comp: _ComponentSummary) -> bool:
        for code in comp.event_codes:
            if code in _SYMPTOM_ONLY_CODES:
                return True
            if any(code.endswith(sfx) for sfx in _SYMPTOM_CODE_SUFFIXES):
                return True
            if "UPSTREAM" in code or "CASCAD" in code or "DOWNSTREAM" in code:
                return True
        msg_lower = comp.representative_message.strip().lower()
        return any(phrase in msg_lower for phrase in _CAUSAL_DOWNSTREAM_PHRASES)

    @staticmethod
    def _is_symptom_only_or_opaque(comp: _ComponentSummary) -> bool:
        if comp.event_codes and comp.event_codes <= _SYMPTOM_ONLY_CODES:
            return True
        msg_lower = comp.representative_message.strip().lower().rstrip(".!")
        if any(phrase in msg_lower for phrase in _SYMPTOM_MESSAGE_PHRASES):
            if not comp.event_codes or comp.event_codes <= _SYMPTOM_ONLY_CODES:
                return True
        if (
            not comp.event_codes
            and comp.role is None
            and msg_lower in _OPAQUE_MESSAGES
        ):
            return True
        return False

    @staticmethod
    def _format_root_cause(comp: _ComponentSummary) -> str:
        if comp.representative_code:
            return (
                f"{comp.display_component} ({comp.representative_code}): "
                f"{comp.representative_message}"
            )
        return f"{comp.display_component}: {comp.representative_message}"

    def to_dict(self) -> dict[str, Any]:
        combined = list(self._anchor_events)
        if self._recent_events:
            combined.extend(self._recent_events)
        if not self._events_sorted:
            combined.sort(key=lambda item: (item[0], item[1]))
        events_copy = [item[2].copy() for item in combined]
        return {
            "incident_id": self.incident_id,
            "status": self.status,
            "started_at": self.started_at,
            "last_seen": self.last_seen,
            "event_count": self.event_count,
            "events": events_copy,
            "root_cause": self.root_cause,
            "root_cause_confidence": self.root_cause_confidence,
        }


class IncidentCorrelator:
    """Correlate structured log events into lifecycle-managed incidents."""

    def __init__(
        self,
        correlation_window_seconds: int = 60,
        close_after_seconds: int = 120,
        history_limit: int = 500,
    ) -> None:
        self.correlation_window_seconds = _validate_positive_int(
            "correlation_window_seconds", correlation_window_seconds
        )
        self.close_after_seconds = _validate_positive_int(
            "close_after_seconds", close_after_seconds
        )
        self.history_limit = _validate_positive_int(
            "history_limit", history_limit
        )
        if self.close_after_seconds < self.correlation_window_seconds:
            raise ValueError(
                "close_after_seconds must be greater than or equal to "
                f"correlation_window_seconds ({self.close_after_seconds} < "
                f"{self.correlation_window_seconds})"
            )

        self._next_incident_num = 1
        self._next_event_seq = 1
        self._active: dict[str, _IncidentState] = {}
        self._history: dict[str, _IncidentState] = {}

        # Secondary indices over active incidents.
        # Bucket maps use OrderedDict[incident_id, None] ordered by recency so
        # reverse iteration visits most recently active incidents first.
        self._by_trace_id: dict[str, str | dict[str, None]] = {}
        self._by_correlation_id: dict[str, str | dict[str, None]] = {}
        self._by_request_id: dict[str, str | dict[str, None]] = {}
        self._by_component: dict[str, dict[str, None]] = {}
        self._by_role: dict[str, dict[str, None]] = {}
        self._by_token: dict[str, dict[str, None]] = {}
        self._monotonic_buckets = True
        self._max_seen_ts: datetime | None = None

    def observe(self, event: dict) -> dict | None:
        parsed = _validate_and_parse_event(event)

        if self._max_seen_ts is not None and parsed.timestamp < self._max_seen_ts:
            self._monotonic_buckets = False
        elif (
            self._max_seen_ts is None or parsed.timestamp > self._max_seen_ts
        ):
            self._max_seen_ts = parsed.timestamp

        if parsed.is_recovery:
            matched = self._match_recovery_incident(parsed)
            if matched is None:
                return None
            seq = self._next_event_seq
            self._next_event_seq += 1
            advanced = matched.add_event(parsed, seq)
            self._index_incident_event(matched, parsed, advanced)
            return matched.to_dict()

        if parsed.is_warning:
            matched_warn = self._match_warning_incident(parsed)
            if matched_warn is None:
                return None
            seq = self._next_event_seq
            self._next_event_seq += 1
            advanced = matched_warn.add_event(parsed, seq)
            self._index_incident_event(matched_warn, parsed, advanced)
            return matched_warn.to_dict()

        if not parsed.is_failure:
            return None

        seq = self._next_event_seq
        self._next_event_seq += 1

        matched_inc = self._find_best_active_incident(parsed)
        if matched_inc is not None:
            advanced = matched_inc.add_event(parsed, seq)
            self._index_incident_event(matched_inc, parsed, advanced)
            return matched_inc.to_dict()

        incident_id = f"INC-{self._next_incident_num:06d}"
        self._next_incident_num += 1
        new_inc = _IncidentState(incident_id, parsed, seq)
        self._active[incident_id] = new_inc
        self._index_incident_event(new_inc, parsed, True)
        return new_inc.to_dict()

    @property
    def active_incidents(self) -> list[dict]:
        return [inc.to_dict() for inc in self._active.values()]

    @property
    def incident_history(self) -> list[dict]:
        return [inc.to_dict() for inc in self._history.values()]

    def close_stale_incidents(self, now: datetime) -> list[dict]:
        parsed_now = _parse_timestamp(now, field_name="now")
        to_close: list[_IncidentState] = []

        for inc in self._active.values():
            if inc.pending_recovery_at is not None:
                elapsed = (
                    parsed_now - inc.pending_recovery_at
                ).total_seconds()
                if elapsed >= self.close_after_seconds:
                    to_close.append(inc)
            else:
                elapsed = (parsed_now - inc.last_seen).total_seconds()
                if elapsed >= self.close_after_seconds:
                    to_close.append(inc)

        if not to_close:
            return []

        closing_all = len(to_close) == len(self._active)
        if closing_all:
            self._active.clear()
            self._by_trace_id.clear()
            self._by_correlation_id.clear()
            self._by_request_id.clear()
            self._by_component.clear()
            self._by_role.clear()
            self._by_token.clear()
            self._monotonic_buckets = True
            self._max_seen_ts = None

        closed_snapshots: list[dict] = []
        for inc in to_close:
            if not closing_all:
                self._unindex_incident(inc)
                del self._active[inc.incident_id]
            inc.status = "closed"
            closed_snapshots.append(inc.to_dict())

        if len(to_close) >= self.history_limit:
            self._history.clear()
            for inc in to_close[-self.history_limit :]:
                self._history[inc.incident_id] = inc
        else:
            for inc in to_close:
                self._history[inc.incident_id] = inc
            while len(self._history) > self.history_limit:
                oldest_id = next(iter(self._history))
                del self._history[oldest_id]

        return closed_snapshots

    def get_incident(self, incident_id: str) -> dict | None:
        if not isinstance(incident_id, str):
            return None
        inc = self._active.get(incident_id)
        if inc is not None:
            return inc.to_dict()
        inc = self._history.get(incident_id)
        if inc is not None:
            return inc.to_dict()
        return None

    @staticmethod
    def _touch_id_map(
        index_map: dict[str, str | dict[str, None]],
        key: str,
        iid: str,
        move: bool,
    ) -> None:
        existing = index_map.get(key)
        if existing is None:
            index_map[key] = iid
        elif isinstance(existing, str):
            if existing != iid:
                index_map[key] = {existing: None, iid: None}
        else:
            if iid not in existing:
                existing[iid] = None
            elif move:
                del existing[iid]
                existing[iid] = None

    @staticmethod
    def _touch_bucket(
        index_map: dict[str, dict[str, None]],
        key: str,
        iid: str,
        move: bool,
    ) -> None:
        bucket = index_map.get(key)
        if bucket is None:
            index_map[key] = {iid: None}
        elif iid not in bucket:
            bucket[iid] = None
        elif move:
            del bucket[iid]
            bucket[iid] = None

    def _index_incident_event(
        self, inc: _IncidentState, ev: _ParsedEvent, advanced: bool
    ) -> None:
        iid = inc.incident_id
        if ev.trace_id:
            self._touch_id_map(self._by_trace_id, ev.trace_id, iid, advanced)
        if ev.correlation_id:
            self._touch_id_map(
                self._by_correlation_id, ev.correlation_id, iid, advanced
            )
        if ev.request_id:
            self._touch_id_map(
                self._by_request_id, ev.request_id, iid, advanced
            )

        inc.indexed_components.add(ev.norm_component)
        if ev.role is not None:
            inc.indexed_roles.add(ev.role)
        inc.indexed_tokens.update(ev.component_tokens)

        if advanced:
            for comp_key in inc.indexed_components:
                self._touch_bucket(self._by_component, comp_key, iid, True)
            for role_key in inc.indexed_roles:
                self._touch_bucket(self._by_role, role_key, iid, True)
            for tok_key in inc.indexed_tokens:
                self._touch_bucket(self._by_token, tok_key, iid, True)
        else:
            self._touch_bucket(self._by_component, ev.norm_component, iid, False)
            if ev.role is not None:
                self._touch_bucket(self._by_role, ev.role, iid, False)
            for tok in ev.component_tokens:
                self._touch_bucket(self._by_token, tok, iid, False)

    @staticmethod
    def _remove_from_id_map(
        index_map: dict[str, str | dict[str, None]], key: str, iid: str
    ) -> None:
        existing = index_map.get(key)
        if existing is None:
            return
        if isinstance(existing, str):
            if existing == iid:
                del index_map[key]
        else:
            existing.pop(iid, None)
            if not existing:
                del index_map[key]

    @staticmethod
    def _remove_from_bucket(
        index_map: dict[str, dict[str, None]], key: str, iid: str
    ) -> None:
        bucket = index_map.get(key)
        if bucket is not None:
            bucket.pop(iid, None)
            if not bucket:
                del index_map[key]

    def _unindex_incident(self, inc: _IncidentState) -> None:
        iid = inc.incident_id
        for tid in inc.trace_ids:
            self._remove_from_id_map(self._by_trace_id, tid, iid)
        for cid in inc.correlation_ids:
            self._remove_from_id_map(self._by_correlation_id, cid, iid)
        for rid in inc.request_ids:
            self._remove_from_id_map(self._by_request_id, rid, iid)
        for comp_key in inc.indexed_components:
            self._remove_from_bucket(self._by_component, comp_key, iid)
        for role_key in inc.indexed_roles:
            self._remove_from_bucket(self._by_role, role_key, iid)
        for tok_key in inc.indexed_tokens:
            self._remove_from_bucket(self._by_token, tok_key, iid)

    def _elapsed_from_span(
        self, ts: datetime, inc: _IncidentState
    ) -> float:
        if ts > inc.last_seen:
            return (ts - inc.last_seen).total_seconds()
        if ts < inc.started_at:
            return (inc.started_at - ts).total_seconds()
        return 0.0

    def _collect_from_bucket(
        self,
        bucket: str | dict[str, None] | None,
        ev_ts: datetime,
        out: set[str],
    ) -> None:
        if not bucket:
            return
        if isinstance(bucket, str):
            if bucket in self._active:
                out.add(bucket)
            return
        max_horizon = self.close_after_seconds
        monotonic = self._monotonic_buckets
        for iid in reversed(bucket):
            inc = self._active.get(iid)
            if inc is None:
                continue
            if monotonic and ev_ts > inc.last_seen:
                if (ev_ts - inc.last_seen).total_seconds() > max_horizon:
                    break
            out.add(iid)

    def _gather_candidate_ids(self, ev: _ParsedEvent) -> set[str]:
        candidates: set[str] = set()
        if ev.trace_id:
            self._collect_from_bucket(
                self._by_trace_id.get(ev.trace_id), ev.timestamp, candidates
            )
        if ev.correlation_id:
            self._collect_from_bucket(
                self._by_correlation_id.get(ev.correlation_id),
                ev.timestamp,
                candidates,
            )
        if ev.request_id:
            self._collect_from_bucket(
                self._by_request_id.get(ev.request_id), ev.timestamp, candidates
            )

        self._collect_from_bucket(
            self._by_component.get(ev.norm_component), ev.timestamp, candidates
        )

        if ev.role is not None:
            self._collect_from_bucket(
                self._by_role.get(ev.role), ev.timestamp, candidates
            )
            for up_role in _DEPENDS_ON.get(ev.role, frozenset()):
                self._collect_from_bucket(
                    self._by_role.get(up_role), ev.timestamp, candidates
                )
            for down_role in _DEPENDED_BY.get(ev.role, frozenset()):
                self._collect_from_bucket(
                    self._by_role.get(down_role), ev.timestamp, candidates
                )

        for code_role in ev.code_roles:
            self._collect_from_bucket(
                self._by_role.get(code_role), ev.timestamp, candidates
            )

        for tok in ev.code_tokens:
            self._collect_from_bucket(
                self._by_token.get(tok), ev.timestamp, candidates
            )

        return candidates

    def _find_best_active_incident(
        self, ev: _ParsedEvent
    ) -> _IncidentState | None:
        candidate_ids = self._gather_candidate_ids(ev)
        if not candidate_ids:
            return None

        best_inc: _IncidentState | None = None
        best_key: tuple[float, float, datetime] | None = None

        for iid in candidate_ids:
            inc = self._active.get(iid)
            if inc is None:
                continue

            elapsed = self._elapsed_from_span(ev.timestamp, inc)
            within_window = elapsed <= self.correlation_window_seconds
            within_probation = False

            if (
                not within_window
                and inc.pending_recovery_at is not None
                and ev.timestamp >= inc.pending_recovery_at
                and (ev.timestamp - inc.pending_recovery_at).total_seconds()
                < self.close_after_seconds
                and self._matches_incident_component(ev, inc)
            ):
                within_probation = True

            if not within_window and not within_probation:
                continue

            score = self._score_correlation(ev, inc)
            if score <= 0.0:
                continue

            rank_key = (score, -elapsed, inc.last_seen)
            if best_key is None or rank_key > best_key:
                best_key = rank_key
                best_inc = inc

        return best_inc

    def _score_correlation(
        self, ev: _ParsedEvent, inc: _IncidentState
    ) -> float:
        shared_id_score = 0.0
        if ev.trace_id and ev.trace_id in inc.trace_ids:
            shared_id_score = 100.0
        elif ev.correlation_id and ev.correlation_id in inc.correlation_ids:
            shared_id_score = 95.0
        elif ev.request_id and ev.request_id in inc.request_ids:
            shared_id_score = 90.0

        if shared_id_score > 0.0:
            return shared_id_score

        has_conflicting_corr = bool(
            (
                ev.correlation_id
                and inc.correlation_ids
                and ev.correlation_id not in inc.correlation_ids
            )
            or (
                ev.trace_id
                and inc.trace_ids
                and ev.trace_id not in inc.trace_ids
            )
        )

        is_client_err = _is_client_validation_code(ev.event_code)
        inc_all_client_err = bool(inc.component_summaries) and all(
            bool(cs.event_codes)
            and all(_is_client_validation_code(c) for c in cs.event_codes)
            for cs in inc.component_summaries.values()
        )

        same_comp_summary: _ComponentSummary | None = None
        for cs in inc.component_summaries.values():
            if _same_logical_component(
                ev.norm_component,
                ev.role,
                ev.qualifiers,
                cs.norm_component,
                cs.role,
                cs.qualifiers,
            ):
                same_comp_summary = cs
                break

        if same_comp_summary is not None:
            inc_ext_roles = {
                r
                for cs in inc.component_summaries.values()
                for r in cs.code_roles
                if r != cs.role
            }
            ev_ext_roles = {r for r in ev.code_roles if r != ev.role}
            if (
                inc_ext_roles
                and ev_ext_roles
                and inc_ext_roles.isdisjoint(ev_ext_roles)
            ):
                return 0.0

            if has_conflicting_corr:
                if (
                    ev.event_code
                    and ev.event_code in same_comp_summary.event_codes
                    and not is_client_err
                ):
                    return 65.0
                return 0.0

            if ev.event_code and ev.event_code in same_comp_summary.event_codes:
                return 75.0
            if is_client_err != inc_all_client_err and (
                ev.event_code and same_comp_summary.event_codes
            ):
                return 0.0
            return 60.0

        if has_conflicting_corr or is_client_err or inc_all_client_err:
            return 0.0

        if ev.role is not None:
            same_role_only = all(
                cs.role == ev.role for cs in inc.component_summaries.values()
            )
            if same_role_only:
                return 0.0

        inc_roles = {
            cs.role
            for cs in inc.component_summaries.values()
            if cs.role is not None
        }
        inc_tokens = {
            tok for cs in inc.component_summaries.values() for tok in cs.tokens
        }
        inc_code_roles = {
            r for cs in inc.component_summaries.values() for r in cs.code_roles
        }
        inc_code_tokens = {
            t
            for cs in inc.component_summaries.values()
            for t in cs.code_tokens
        }

        if (ev.code_roles & inc_roles) or (ev.code_tokens & inc_tokens):
            return 70.0
        if (ev.role is not None and ev.role in inc_code_roles) or (
            set(ev.component_tokens) & inc_code_tokens
        ):
            return 70.0

        inc_qualifiers = {
            q for cs in inc.component_summaries.values() for q in cs.qualifiers
        }
        qualifiers_compatible = (
            not ev.qualifiers and not inc_qualifiers
        ) or bool(ev.qualifiers & inc_qualifiers)

        if qualifiers_compatible and ev.role is not None and inc_roles:
            ev_upstreams = _DEPENDS_ON.get(ev.role, frozenset())
            ev_downstreams = _DEPENDED_BY.get(ev.role, frozenset())
            if (ev_upstreams & inc_roles) or (ev_downstreams & inc_roles):
                foreign_ev_roles = {
                    r
                    for r in ev.code_roles
                    if r != ev.role and r not in inc_roles
                }
                if foreign_ev_roles:
                    return 0.0
                foreign_inc_roles = {
                    r
                    for r in inc_code_roles
                    if r not in inc_roles and r != ev.role
                }
                if foreign_inc_roles and not (ev.role in inc_code_roles):
                    return 0.0
                return 45.0

        return 0.0

    @staticmethod
    def _matches_incident_component(
        ev: _ParsedEvent, inc: _IncidentState
    ) -> bool:
        for cs in inc.component_summaries.values():
            if _same_logical_component(
                ev.norm_component,
                ev.role,
                ev.qualifiers,
                cs.norm_component,
                cs.role,
                cs.qualifiers,
            ):
                return True
        return False

    def _match_warning_incident(
        self, ev: _ParsedEvent
    ) -> _IncidentState | None:
        id_candidates: set[str] = set()
        if ev.trace_id:
            self._collect_from_bucket(
                self._by_trace_id.get(ev.trace_id), ev.timestamp, id_candidates
            )
        if ev.correlation_id:
            self._collect_from_bucket(
                self._by_correlation_id.get(ev.correlation_id),
                ev.timestamp,
                id_candidates,
            )
        if ev.request_id:
            self._collect_from_bucket(
                self._by_request_id.get(ev.request_id),
                ev.timestamp,
                id_candidates,
            )

        best_inc: _IncidentState | None = None
        best_elapsed: float | None = None
        for iid in id_candidates:
            inc = self._active.get(iid)
            if inc is None:
                continue
            elapsed = self._elapsed_from_span(ev.timestamp, inc)
            if elapsed <= self.correlation_window_seconds:
                if best_elapsed is None or elapsed < best_elapsed:
                    best_elapsed = elapsed
                    best_inc = inc
        return best_inc

    def _match_recovery_incident(
        self, ev: _ParsedEvent
    ) -> _IncidentState | None:
        code = ev.event_code or ""
        specific_role = _RECOVERY_CODE_TO_ROLE.get(code)

        candidate_ids: set[str] = set()
        if specific_role is not None:
            rbucket = self._by_role.get(specific_role)
            if rbucket:
                candidate_ids.update(rbucket.keys())
            cbucket = self._by_component.get(ev.norm_component)
            if cbucket:
                candidate_ids.update(cbucket.keys())
        else:
            cbucket = self._by_component.get(ev.norm_component)
            if cbucket:
                candidate_ids.update(cbucket.keys())
            elif ev.role is not None:
                rbucket = self._by_role.get(ev.role)
                if rbucket:
                    candidate_ids.update(rbucket.keys())

        if not candidate_ids:
            return None

        best_inc: _IncidentState | None = None
        best_rank: tuple[int, datetime] | None = None

        for iid in candidate_ids:
            inc = self._active.get(iid)
            if inc is None:
                continue

            relevant_comp = False
            is_root_match = False
            for comp_key, cs in inc.component_summaries.items():
                comp_match = _same_logical_component(
                    ev.norm_component,
                    ev.role,
                    ev.qualifiers,
                    cs.norm_component,
                    cs.role,
                    cs.qualifiers,
                )
                role_match = (
                    specific_role is not None
                    and cs.role == specific_role
                    and (
                        not ev.qualifiers
                        or not cs.qualifiers
                        or ev.qualifiers == cs.qualifiers
                    )
                )
                if (
                    specific_role is not None
                    and cs.role is not None
                    and cs.role != specific_role
                    and not comp_match
                ):
                    continue
                if comp_match or role_match:
                    if ev.timestamp >= cs.earliest_ts:
                        relevant_comp = True
                        if comp_key == inc.root_norm_component:
                            is_root_match = True

            if not relevant_comp:
                continue

            rank = (1 if is_root_match else 0, inc.last_seen)
            if best_rank is None or rank > best_rank:
                best_rank = rank
                best_inc = inc

        return best_inc
