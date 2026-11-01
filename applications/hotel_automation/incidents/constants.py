"""Shared vocabulary and tuning constants for incident correlation."""

TIMESTAMP_FORMAT = "%Y-%m-%d %H:%M:%S"

VALID_LEVELS = frozenset(
    {"DEBUG", "INFO", "WARNING", "WARN", "ERROR", "CRITICAL", "FATAL"}
)
FAILURE_LEVELS = frozenset({"ERROR", "CRITICAL", "FATAL"})
WARNING_LEVELS = frozenset({"WARNING", "WARN"})
CRITICAL_LEVELS = frozenset({"CRITICAL", "FATAL"})

OPTIONAL_ID_FIELDS = ("event_code", "request_id", "correlation_id", "trace_id")

MAX_EVENTS_PER_INCIDENT = 64
ANCHOR_EVENT_COUNT = 16

# Recovery codes and the logical role whose failures they resolve.
# ``None`` means the recovery applies only to the emitting component.
RECOVERY_CODE_TO_ROLE: dict[str, str | None] = {
    "SERVICE_RECOVERED": None,
    "DB_CONNECTION_RESTORED": "database",
    "PAYMENT_GATEWAY_RECOVERED": "payment_gateway",
    "NOTIFICATION_SERVICE_RECOVERED": "notification_service",
    "BOOKING_SERVICE_RECOVERED": "booking_service",
    "REPORTING_SERVICE_RECOVERED": "reporting_service",
    "HOUSEKEEPING_RECOVERED": "housekeeping",
}

COMPONENT_TOKEN_TO_ROLE: dict[str, str] = {
    "db": "database",
    "database": "database",
    "postgres": "database",
    "postgresql": "database",
    "mysql": "database",
    "sqlite": "database",
    "mongodb": "database",
    "datastore": "database",
    "sql": "database",
    "cache": "cache",
    "redis": "cache",
    "memcached": "cache",
    "queue": "queue",
    "broker": "queue",
    "kafka": "queue",
    "rabbitmq": "queue",
    "network": "network",
    "dns": "network",
    "auth": "auth",
    "identity": "auth",
    "oauth": "auth",
    "sso": "auth",
    "payment": "payment_gateway",
    "payments": "payment_gateway",
    "billing": "payment_gateway",
    "booking": "booking_service",
    "bookings": "booking_service",
    "reservation": "booking_service",
    "reservations": "booking_service",
    "reporting": "reporting_service",
    "report": "reporting_service",
    "reports": "reporting_service",
    "analytics": "reporting_service",
    "notification": "notification_service",
    "notifications": "notification_service",
    "email": "notification_service",
    "sms": "notification_service",
    "housekeeping": "housekeeping",
    "cleaning": "housekeeping",
    "frontdesk": "front_desk",
    "front": "front_desk",
    "desk": "front_desk",
    "checkin": "front_desk",
    "api": "api_gateway",
    "frontend": "api_gateway",
    "web": "api_gateway",
}

# Roles that identify a component even when preceded by a qualifier,
# e.g. ``billing_db`` is a database qualified by "billing".
HEAD_INFRA_ROLES = frozenset({"database", "cache", "queue", "network"})

DEPENDS_ON: dict[str, frozenset[str]] = {
    "database": frozenset({"network"}),
    "cache": frozenset({"network"}),
    "queue": frozenset({"network"}),
    "auth": frozenset({"database", "cache", "network"}),
    "payment_gateway": frozenset({"network"}),
    "booking_service": frozenset(
        {"database", "cache", "payment_gateway", "auth", "queue"}
    ),
    "reporting_service": frozenset({"database", "cache"}),
    "notification_service": frozenset({"queue", "booking_service"}),
    "housekeeping": frozenset({"database"}),
    "front_desk": frozenset({"booking_service", "database"}),
    "api_gateway": frozenset(
        {"auth", "booking_service", "reporting_service", "payment_gateway"}
    ),
}

INFRA_ROLES = frozenset(
    {"database", "cache", "queue", "network", "auth", "payment_gateway"}
)
EDGE_ROLES = frozenset({"api_gateway", "notification_service"})

STOP_COMPONENT_TOKENS = frozenset(
    {
        "service",
        "services",
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

INITIATING_CODE_MARKERS = (
    "CONNECTION_FAILED",
    "CONNECTION_REFUSED",
    "CONNECTION_LOST",
    "CONNECTION_RESET",
    "POOL_EXHAUSTED",
    "DISK_FULL",
    "OUT_OF_MEMORY",
    "DEADLOCK",
    "CORRUPTION",
    "CRASH",
    "UNREACHABLE",
    "DNS_FAILURE",
    "CERT_EXPIRED",
    "QUOTA_EXCEEDED",
    "REFUSED",
    "EXHAUSTED",
)
INFRA_INITIATING_MARKERS = ("TIMEOUT", "UNAVAILABLE", "FAILED")

SYMPTOM_CODE_MARKERS = ("UPSTREAM", "DOWNSTREAM", "CASCAD", "DEPENDENCY")
SYMPTOM_CODE_SUFFIXES = (
    "_SAVE_FAILED",
    "_SKIPPED",
    "_ABORTED",
    "_CANCELED",
    "_CANCELLED",
    "_FALLBACK",
)
SYMPTOM_MESSAGE_PHRASES = (
    "upstream service failed",
    "upstream error",
    "upstream unavailable",
    "cascading failure",
    "secondary failure",
    "dependency failed",
)
CAUSAL_DOWNSTREAM_PHRASES = (
    "due to ",
    "caused by ",
    "because of ",
    "waiting for ",
    "upstream ",
)
OPAQUE_MESSAGES = frozenset(
    {
        "error",
        "unknown error",
        "unexpected error",
        "unspecified error",
        "operation failed",
        "failed",
        "failure",
        "something went wrong",
    }
)
