from copy import deepcopy
from datetime import datetime, timedelta

import pytest

from applogmon.incident_correlator import IncidentCorrelator


BASE_TIME = datetime(2026, 10, 28, 10, 0, 0)


def make_event(
    seconds=0,
    *,
    level="ERROR",
    component="database",
    message="Database connection failed",
    event_code=None,
    request_id=None,
    correlation_id=None,
    trace_id=None,
    timestamp=None,
):
    """Create a synthetic log event for testing."""
    event_timestamp = (
        timestamp
        if timestamp is not None
        else BASE_TIME + timedelta(seconds=seconds)
    )

    result = {
        "timestamp": event_timestamp,
        "level": level,
        "component": component,
        "message": message,
    }

    optional_fields = {
        "event_code": event_code,
        "request_id": request_id,
        "correlation_id": correlation_id,
        "trace_id": trace_id,
    }

    result.update(
        {
            key: value
            for key, value in optional_fields.items()
            if value is not None
        }
    )

    return result


def require_incident(correlator, incident_id):
    """Fetch an incident and fail clearly if it is missing."""
    incident = correlator.get_incident(incident_id)
    assert incident is not None, f"Incident {incident_id!r} was not found"
    return incident
	
def test_01_related_events_correlate_into_one_incident():
    correlator = IncidentCorrelator()

    correlator.observe(
        make_event(
            seconds=0,
            component="database",
            message="Database connection failed",
            correlation_id="corr-001",
            event_code="DB_CONNECTION_FAILED",
        )
    )
    correlator.observe(
        make_event(
            seconds=10,
            component="booking",
            message="Booking request failed",
            correlation_id="corr-001",
            event_code="BOOKING_FAILED",
        )
    )

    incidents = correlator.active_incidents

    assert len(incidents) == 1
    assert incidents[0]["event_count"] == 2


def test_02_independent_failures_remain_separate():
    correlator = IncidentCorrelator()

    correlator.observe(
        make_event(
            seconds=0,
            component="payment",
            message="Payment gateway timeout",
            correlation_id="payment-001",
        )
    )
    correlator.observe(
        make_event(
            seconds=5,
            component="housekeeping",
            message="Room cleaning failed",
            correlation_id="cleaning-001",
        )
    )

    assert len(correlator.active_incidents) == 2


def test_03_similar_messages_do_not_merge_unrelated_workflows():
    correlator = IncidentCorrelator()

    correlator.observe(
        make_event(
            seconds=0,
            component="payment",
            message="Request failed",
            request_id="request-A",
        )
    )
    correlator.observe(
        make_event(
            seconds=1,
            component="reporting",
            message="Request failed",
            request_id="request-B",
        )
    )

    assert len(correlator.active_incidents) == 2


def test_04_correlation_window_includes_exactly_60_seconds():
    correlator = IncidentCorrelator(correlation_window_seconds=60)

    correlator.observe(
        make_event(
            seconds=0,
            component="database",
            correlation_id="shared-001",
        )
    )
    correlator.observe(
        make_event(
            seconds=60,
            component="booking",
            message="Booking failed",
            correlation_id="shared-001",
        )
    )

    assert len(correlator.active_incidents) == 1
    assert correlator.active_incidents[0]["event_count"] == 2


def test_05_time_alone_does_not_correlate_events_61_seconds_apart():
    correlator = IncidentCorrelator(correlation_window_seconds=60)

    correlator.observe(
        make_event(
            seconds=0,
            component="database",
            message="Database connection failed",
        )
    )
    correlator.observe(
        make_event(
            seconds=61,
            component="database",
            message="Database connection failed",
        )
    )

    assert len(correlator.active_incidents) == 2
	
def test_06_root_cause_identifies_a_supported_cause():
    correlator = IncidentCorrelator()

    correlator.observe(
        make_event(
            seconds=0,
            component="database",
            message="Database connection refused",
            event_code="DB_CONNECTION_FAILED",
            correlation_id="chain-001",
        )
    )
    correlator.observe(
        make_event(
            seconds=5,
            component="booking",
            message="Booking failed because database is unavailable",
            event_code="BOOKING_FAILED",
            correlation_id="chain-001",
        )
    )

    incident = correlator.active_incidents[0]

    assert incident["root_cause"] is not None
    assert incident["root_cause_confidence"] is not None
    assert 0.0 <= incident["root_cause_confidence"] <= 1.0


def test_07_out_of_order_events_do_not_corrupt_incident_timestamps():
    correlator = IncidentCorrelator()

    correlator.observe(make_event(seconds=30, message="Later event"))
    correlator.observe(make_event(seconds=10, message="Earlier event"))

    incident = correlator.active_incidents[0]

    started_at = incident["started_at"]
    last_seen = incident["last_seen"]

    if isinstance(started_at, str):
        started_at = datetime.strptime(started_at, "%Y-%m-%d %H:%M:%S")

    if isinstance(last_seen, str):
        last_seen = datetime.strptime(last_seen, "%Y-%m-%d %H:%M:%S")

    assert started_at == BASE_TIME + timedelta(seconds=10)
    assert last_seen == BASE_TIME + timedelta(seconds=30)
    assert incident["event_count"] == 2


def test_08_inactive_incident_closes_after_timeout():
    correlator = IncidentCorrelator(close_after_seconds=120)

    correlator.observe(make_event(seconds=0))

    closed = correlator.close_stale_incidents(
        BASE_TIME + timedelta(seconds=121)
    )

    assert len(closed) == 1
    assert closed[0]["status"] == "closed"
    assert len(correlator.active_incidents) == 0


def test_09_incident_closes_at_exact_inactivity_boundary():
    correlator = IncidentCorrelator(close_after_seconds=120)

    correlator.observe(make_event(seconds=0))

    closed = correlator.close_stale_incidents(
        BASE_TIME + timedelta(seconds=120)
    )

    # This suite defines the timeout boundary as inclusive: elapsed >= timeout.
    assert len(closed) == 1
    assert closed[0]["status"] == "closed"
	
def test_10_recovery_does_not_close_an_unrelated_incident():
    correlator = IncidentCorrelator(close_after_seconds=120)

    correlator.observe(
        make_event(
            seconds=0,
            component="database",
            event_code="DB_CONNECTION_FAILED",
            message="Database connection failed",
        )
    )
    correlator.observe(
        make_event(
            seconds=0,
            component="payment",
            event_code="PAYMENT_GATEWAY_TIMEOUT",
            message="Payment gateway timed out",
        )
    )

    correlator.observe(
        make_event(
            seconds=10,
            level="INFO",
            component="database",
            event_code="DB_CONNECTION_RESTORED",
            message="Database connection restored",
        )
    )

    # The database incident is pending resolution, but the payment incident
    # must not be closed by the database recovery event.
    closed = correlator.close_stale_incidents(
        BASE_TIME + timedelta(seconds=20)
    )

    active_components = {
        event["component"]
        for incident in correlator.active_incidents
        for event in incident["events"]
    }

    assert "payment" in active_components
    assert all(incident["status"] == "closed" for incident in closed)


def test_11_new_failure_cancels_pending_recovery():
    correlator = IncidentCorrelator(close_after_seconds=120)

    correlator.observe(
        make_event(
            seconds=0,
            component="database",
            event_code="DB_CONNECTION_FAILED",
            message="Database connection failed",
            correlation_id="db-001",
        )
    )
    correlator.observe(
        make_event(
            seconds=10,
            level="INFO",
            component="database",
            event_code="DB_CONNECTION_RESTORED",
            message="Database connection restored",
            correlation_id="db-001",
        )
    )
    correlator.observe(
        make_event(
            seconds=20,
            component="database",
            event_code="DB_CONNECTION_FAILED",
            message="Database connection failed again",
            correlation_id="db-001",
        )
    )

    correlator.close_stale_incidents(
        BASE_TIME + timedelta(seconds=131)
    )

    assert len(correlator.active_incidents) == 1


def test_12_failure_after_closure_creates_a_new_incident():
    correlator = IncidentCorrelator(close_after_seconds=120)

    correlator.observe(
        make_event(seconds=0, correlation_id="repeat-001")
    )
    original_id = correlator.active_incidents[0]["incident_id"]

    correlator.close_stale_incidents(
        BASE_TIME + timedelta(seconds=120)
    )

    correlator.observe(
        make_event(
            seconds=180,
            correlation_id="repeat-001",
            message="Database failed again",
        )
    )

    assert len(correlator.active_incidents) == 1
    assert correlator.active_incidents[0]["incident_id"] != original_id


def test_13_repeated_genuine_failures_are_not_discarded():
    correlator = IncidentCorrelator()

    correlator.observe(
        make_event(
            seconds=0,
            request_id="request-001",
            correlation_id="batch-001",
        )
    )
    correlator.observe(
        make_event(
            seconds=1,
            request_id="request-002",
            correlation_id="batch-001",
        )
    )

    # These are two genuine failures with different request IDs.
    assert len(correlator.active_incidents) == 1
    assert correlator.active_incidents[0]["event_count"] == 2


def test_14_malformed_timestamps_raise_value_error_without_mutation():
    correlator = IncidentCorrelator()

    correlator.observe(make_event(seconds=0))
    before = deepcopy(correlator.active_incidents)

    malformed_events = [
        {
            "timestamp": "not-a-timestamp",
            "level": "ERROR",
            "component": "database",
            "message": "Bad timestamp",
        },
        {
            "timestamp": None,
            "level": "ERROR",
            "component": "database",
            "message": "Missing timestamp value",
        },
        {
            "timestamp": 12345,
            "level": "ERROR",
            "component": "database",
            "message": "Wrong timestamp type",
        },
    ]

    for bad_event in malformed_events:
        with pytest.raises(ValueError):
            correlator.observe(bad_event)

        assert correlator.active_incidents == before

    # A later valid event must still be processed.
    correlator.observe(
        make_event(
            seconds=1,
            correlation_id="valid-after-error",
            component="payment",
        )
    )

    assert len(correlator.active_incidents) == 2
	
def test_15_optional_identifiers_are_not_required():
    correlator = IncidentCorrelator()

    # Only the four required fields are provided.
    correlator.observe(
        {
            "timestamp": BASE_TIME,
            "level": "ERROR",
            "component": "reporting",
            "message": "Report generation failed",
        }
    )

    assert len(correlator.active_incidents) == 1

    incident = correlator.active_incidents[0]
    assert incident["event_count"] == 1
    assert incident["events"][0]["message"] == "Report generation failed"


def test_16_public_accessors_return_defensive_copies():
    correlator = IncidentCorrelator()

    correlator.observe(
        make_event(
            seconds=0,
            correlation_id="defensive-001",
        )
    )

    # Mutating a returned active incident must not mutate internal state.
    active_copy = correlator.active_incidents
    active_copy[0]["event_count"] = 999
    active_copy[0]["events"][0]["message"] = "Tampered message"

    actual = correlator.active_incidents[0]
    assert actual["event_count"] == 1
    assert actual["events"][0]["message"] == "Database connection failed"

    # Closed history must also be protected.
    correlator.close_stale_incidents(
        BASE_TIME + timedelta(seconds=120)
    )

    history_copy = correlator.incident_history
    assert len(history_copy) == 1

    history_copy[0]["status"] = "tampered"
    history_copy[0]["events"].clear()

    actual_history = correlator.incident_history
    assert actual_history[0]["status"] == "closed"
    assert len(actual_history[0]["events"]) == 1

    # get_incident must return a defensive copy too.
    incident_id = actual_history[0]["incident_id"]
    incident_copy = correlator.get_incident(incident_id)
    incident_copy["status"] = "tampered"

    assert correlator.get_incident(incident_id)["status"] == "closed"


def test_17_closed_history_is_bounded_but_active_incidents_are_preserved():
    correlator = IncidentCorrelator(
        correlation_window_seconds=5,
        close_after_seconds=10,
        history_limit=2,
    )

    closed_ids = []

    for index, component in enumerate(
        ["service-A", "service-B", "service-C"]
    ):
        seconds = index * 20

        correlator.observe(
            make_event(
                seconds=seconds,
                component=component,
                message=f"Failure in {component}",
            )
        )

        incident_id = correlator.active_incidents[0]["incident_id"]
        closed = correlator.close_stale_incidents(
            BASE_TIME + timedelta(seconds=seconds + 10)
        )
        assert len(closed) == 1
        closed_ids.append(incident_id)

    # The newest two closed incidents remain in history.
    history = correlator.incident_history
    assert len(history) == 2
    assert [item["incident_id"] for item in history] == closed_ids[-2:]

    # An active incident must not be evicted by the history limit.
    correlator.observe(
        make_event(
            seconds=100,
            component="active-service",
            message="Active failure",
        )
    )

    assert len(correlator.active_incidents) == 1
    assert correlator.active_incidents[0]["events"][0]["component"] == (
        "active-service"
    )


def test_18_interleaved_incidents_keep_their_events_separate():
    correlator = IncidentCorrelator(correlation_window_seconds=60)

    correlator.observe(
        make_event(
            seconds=0,
            component="database",
            message="Database failure",
            correlation_id="database-chain",
        )
    )
    correlator.observe(
        make_event(
            seconds=1,
            component="payment",
            message="Payment failure",
            correlation_id="payment-chain",
        )
    )
    correlator.observe(
        make_event(
            seconds=2,
            component="booking",
            message="Booking failed due to database",
            correlation_id="database-chain",
        )
    )
    correlator.observe(
        make_event(
            seconds=3,
            component="payment",
            message="Payment retry failed",
            correlation_id="payment-chain",
        )
    )

    incidents = correlator.active_incidents
    assert len(incidents) == 2

    events_by_correlation = {
        incident["events"][0].get("correlation_id"): incident["events"]
        for incident in incidents
    }

    assert len(events_by_correlation["database-chain"]) == 2
    assert len(events_by_correlation["payment-chain"]) == 2

    database_components = {
        item["component"]
        for item in events_by_correlation["database-chain"]
    }
    payment_components = {
        item["component"]
        for item in events_by_correlation["payment-chain"]
    }

    assert database_components == {"database", "booking"}
    assert payment_components == {"payment"}


def test_19_large_event_volume_and_invalid_configuration():
    # Invalid settings must fail fast. Booleans are not valid integers here.
    invalid_settings = [
        {"correlation_window_seconds": 0},
        {"correlation_window_seconds": -1},
        {"close_after_seconds": 0},
        {"close_after_seconds": -1},
        {"history_limit": 0},
        {"history_limit": -1},
        {"correlation_window_seconds": True},
        {"close_after_seconds": False},
        {"history_limit": True},
        {"correlation_window_seconds": 1.5},
    ]

    for settings in invalid_settings:
        with pytest.raises((TypeError, ValueError)):
            IncidentCorrelator(**settings)

    # Process 100,000 genuine failures without growing closed history.
    correlator = IncidentCorrelator(
        correlation_window_seconds=60,
        close_after_seconds=120,
        history_limit=5,
    )

    for index in range(100_000):
        correlator.observe(
            make_event(
                seconds=0,
                component="database",
                message=f"Connection failure {index}",
                correlation_id="large-test-chain",
                request_id=f"request-{index}",
            )
        )

    assert len(correlator.active_incidents) == 1
    assert correlator.active_incidents[0]["event_count"] == 100_000
    assert len(correlator.incident_history) <= 5
	
