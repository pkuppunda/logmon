from datetime import date, datetime, timedelta, timezone
import pytest

from applogmon.incident_correlator import IncidentCorrelator, MAX_EVENTS_PER_INCIDENT


def test_correlation_window_boundaries_and_root_cause_ranking():
    correlator = IncidentCorrelator(
        correlation_window_seconds=60,
        close_after_seconds=120,
    )

    # Downstream symptom arrives before out-of-order upstream DB failure.
    symptom = correlator.observe(
        {
            "timestamp": "2026-10-28 10:00:10",
            "level": "ERROR",
            "component": "booking_service",
            "event_code": "BOOKING_SAVE_FAILED",
            "message": "Failed while saving booking",
            "correlation_id": "corr-101",
        }
    )
    initiating = correlator.observe(
        {
            "timestamp": datetime(2026, 10, 28, 10, 0, 5),
            "level": "CRITICAL",
            "component": "database",
            "event_code": "DB_CONNECTION_REFUSED",
            "message": "Database connection refused",
            "correlation_id": "corr-101",
        }
    )
    assert symptom["incident_id"] == initiating["incident_id"]
    assert initiating["started_at"] == datetime(2026, 10, 28, 10, 0, 5)
    assert initiating["last_seen"] == datetime(2026, 10, 28, 10, 0, 10)
    assert initiating["events"][0]["component"] == "database"
    assert initiating["root_cause"].startswith("database (DB_CONNECTION_REFUSED):")
    assert 0.0 < initiating["root_cause_confidence"] < 1.0

    # Inclusive +60s boundary correlates; +61s after updated last_seen opens new incident.
    at_boundary = correlator.observe(
        {
            "timestamp": "2026-10-28 10:01:10",
            "level": "ERROR",
            "component": "database",
            "event_code": "DB_CONNECTION_REFUSED",
            "message": "Database connection still refused",
        }
    )
    assert at_boundary["incident_id"] == initiating["incident_id"]
    assert at_boundary["event_count"] == 3

    outside_boundary = correlator.observe(
        {
            "timestamp": "2026-10-28 10:02:11",
            "level": "ERROR",
            "component": "database",
            "event_code": "DB_CONNECTION_REFUSED",
            "message": "Separate outage outside window",
        }
    )
    assert outside_boundary["incident_id"] != initiating["incident_id"]

    # Ambiguous simultaneous peer failures represent uncertainty with None.
    correlator.observe(
        {
            "timestamp": "2026-10-28 11:00:00",
            "level": "ERROR",
            "component": "alpha_worker",
            "message": "Peer failure",
            "trace_id": "trace-tie",
        }
    )
    ambiguous = correlator.observe(
        {
            "timestamp": "2026-10-28 11:00:00",
            "level": "ERROR",
            "component": "beta_worker",
            "message": "Peer failure",
            "trace_id": "trace-tie",
        }
    )
    assert ambiguous["root_cause"] is None
    assert ambiguous["root_cause_confidence"] is None


def test_separation_of_unrelated_interleaved_incidents():
    correlator = IncidentCorrelator()

    # Routine INFO / DEBUG events do not open incidents.
    assert (
        correlator.observe(
            {
                "timestamp": "2026-10-28 12:00:00",
                "level": "INFO",
                "component": "booking_service",
                "message": "Booking confirmed",
            }
        )
        is None
    )

    # Identical messages and close timestamps across unrelated components stay separate.
    inc_pg = correlator.observe(
        {
            "timestamp": "2026-10-28 12:00:01",
            "level": "ERROR",
            "component": "payment_gateway",
            "message": "Connection timeout",
        }
    )
    inc_rep = correlator.observe(
        {
            "timestamp": "2026-10-28 12:00:02",
            "level": "ERROR",
            "component": "reporting_service",
            "message": "Connection timeout",
        }
    )
    inc_users_db = correlator.observe(
        {
            "timestamp": "2026-10-28 12:00:02",
            "level": "ERROR",
            "component": "users_db",
            "message": "Connection timeout",
        }
    )
    inc_billing_db = correlator.observe(
        {
            "timestamp": "2026-10-28 12:00:03",
            "level": "ERROR",
            "component": "billing_db",
            "message": "Connection timeout",
        }
    )
    ids = {
        inc_pg["incident_id"],
        inc_rep["incident_id"],
        inc_users_db["incident_id"],
        inc_billing_db["incident_id"],
    }
    assert len(ids) == 4

    # Genuine repeated failures at the same second merge into the same incident and are counted.
    rep_again = correlator.observe(
        {
            "timestamp": "2026-10-28 12:00:02",
            "level": "ERROR",
            "component": "reporting_service",
            "message": "Connection timeout",
        }
    )
    assert rep_again["incident_id"] == inc_rep["incident_id"]
    assert rep_again["event_count"] == 2


def test_recovery_lifecycle_relapse_and_closure_boundaries():
    correlator = IncidentCorrelator(
        correlation_window_seconds=60,
        close_after_seconds=120,
    )

    db_inc = correlator.observe(
        {
            "timestamp": "2026-10-28 14:00:00",
            "level": "ERROR",
            "component": "database",
            "event_code": "DB_CONNECTION_FAILED",
            "message": "DB unreachable",
        }
    )
    pg_inc = correlator.observe(
        {
            "timestamp": "2026-10-28 14:00:20",
            "level": "ERROR",
            "component": "payment_gateway",
            "event_code": "PAYMENT_TIMEOUT",
            "message": "Gateway timeout",
        }
    )

    # Unrelated recovery is ignored; matching recovery keeps status open pending confirmation.
    assert (
        correlator.observe(
            {
                "timestamp": "2026-10-28 14:00:25",
                "level": "INFO",
                "component": "notification_service",
                "event_code": "NOTIFICATION_SERVICE_RECOVERED",
                "message": "Recovered",
            }
        )
        is None
    )
    pending = correlator.observe(
        {
            "timestamp": "2026-10-28 14:00:30",
            "level": "INFO",
            "component": "database",
            "event_code": "DB_CONNECTION_RESTORED",
            "message": "Connection restored",
        }
    )
    assert pending["incident_id"] == db_inc["incident_id"]
    assert pending["status"] == "open"

    # At 14:02:20: PG has 120s of inactivity and closes; DB is only 110s post-recovery and stays open.
    closed_first = correlator.close_stale_incidents(datetime(2026, 10, 28, 14, 2, 20))
    assert [c["incident_id"] for c in closed_first] == [pg_inc["incident_id"]]
    assert correlator.get_incident(db_inc["incident_id"])["status"] == "open"

    # Subsequent failure during probation cancels pending resolution.
    relapse = correlator.observe(
        {
            "timestamp": "2026-10-28 14:02:25",
            "level": "ERROR",
            "component": "database",
            "event_code": "DB_CONNECTION_FAILED",
            "message": "Dropped again",
        }
    )
    assert relapse["incident_id"] == db_inc["incident_id"]
    assert correlator.close_stale_incidents(datetime(2026, 10, 28, 14, 2, 35)) == []

    # Second recovery closes strictly once close_after_seconds (120s) has elapsed.
    correlator.observe(
        {
            "timestamp": "2026-10-28 14:02:40",
            "level": "INFO",
            "component": "database",
            "event_code": "DB_CONNECTION_RESTORED",
            "message": "Restored cleanly",
        }
    )
    assert correlator.close_stale_incidents(datetime(2026, 10, 28, 14, 4, 39)) == []
    closed_second = correlator.close_stale_incidents(datetime(2026, 10, 28, 14, 4, 40))
    assert [c["incident_id"] for c in closed_second] == [db_inc["incident_id"]]
    assert closed_second[0]["status"] == "closed"

    # Post-closure failure creates a new incident without mutating the closed record.
    reopened = correlator.observe(
        {
            "timestamp": "2026-10-28 14:04:45",
            "level": "ERROR",
            "component": "database",
            "event_code": "DB_CONNECTION_FAILED",
            "message": "Fresh incident",
        }
    )
    assert reopened["incident_id"] != db_inc["incident_id"]
    assert correlator.get_incident(db_inc["incident_id"])["event_count"] == 4


def test_validation_atomicity_and_configuration_constraints():
    for invalid_cfg in (
        {"correlation_window_seconds": 0},
        {"correlation_window_seconds": True},
        {"close_after_seconds": False},
        {"history_limit": -1},
        {"history_limit": True},
        {"correlation_window_seconds": 90, "close_after_seconds": 60},
    ):
        with pytest.raises(ValueError):
            IncidentCorrelator(**invalid_cfg)

    correlator = IncidentCorrelator()
    for bad_event in (
        {},
        {"timestamp": None, "level": "ERROR", "component": "db", "message": "err"},
        {"timestamp": "2026-10-28T10:00:00", "level": "ERROR", "component": "db", "message": "err"},
        {"timestamp": "2026-02-29 10:00:00", "level": "ERROR", "component": "db", "message": "err"},
        {"timestamp": 1730000000, "level": "ERROR", "component": "db", "message": "err"},
        {"timestamp": date(2026, 10, 28), "level": "ERROR", "component": "db", "message": "err"},
        {
            "timestamp": datetime(2026, 10, 28, 10, 0, 0, tzinfo=timezone.utc),
            "level": "ERROR",
            "component": "db",
            "message": "err",
        },
        {"timestamp": "2026-10-28 10:00:00", "level": "UNKNOWN", "component": "db", "message": "err"},
        {"timestamp": "2026-10-28 10:00:00", "level": "ERROR", "component": "", "message": "err"},
    ):
        with pytest.raises(ValueError):
            correlator.observe(bad_event)

    assert correlator.active_incidents == []
    valid = correlator.observe(
        {
            "timestamp": "2026-10-28 10:00:00",
            "level": "ERROR",
            "component": "db",
            "message": "Connection lost",
        }
    )
    assert valid["incident_id"] == "INC-000001"
    assert valid["event_count"] == 1


def test_history_limit_defensive_copies_and_bounded_scaling():
    correlator = IncidentCorrelator(
        correlation_window_seconds=10,
        close_after_seconds=20,
        history_limit=2,
    )
    base = datetime(2026, 10, 28, 8, 0, 0)

    # Create 3 incidents that close sequentially and 1 active incident.
    closed_ids = []
    for idx in range(3):
        t = base + timedelta(seconds=idx * 40)
        inc = correlator.observe(
            {
                "timestamp": t,
                "level": "ERROR",
                "component": f"svc_{idx}",
                "message": f"Failure {idx}",
            }
        )
        closed_ids.append(inc["incident_id"])
        correlator.close_stale_incidents(t + timedelta(seconds=20))

    active_inc = correlator.observe(
        {
            "timestamp": base + timedelta(seconds=200),
            "level": "ERROR",
            "component": "active_svc",
            "message": "Ongoing burst",
        }
    )
    for step in range(MAX_EVENTS_PER_INCIDENT + 25):
        correlator.observe(
            {
                "timestamp": base + timedelta(seconds=200 + (step % 5)),
                "level": "ERROR",
                "component": "active_svc",
                "message": f"Burst {step}",
            }
        )

    assert [i["incident_id"] for i in correlator.incident_history] == closed_ids[1:]
    assert correlator.get_incident(closed_ids[0]) is None
    assert correlator.get_incident(closed_ids[1])["status"] == "closed"

    fetched_active = correlator.get_incident(active_inc["incident_id"])
    assert fetched_active["status"] == "open"
    assert fetched_active["event_count"] == MAX_EVENTS_PER_INCIDENT + 26
    assert len(fetched_active["events"]) == MAX_EVENTS_PER_INCIDENT

    # Mutating returned snapshots must never alter internal correlator state.
    fetched_active["status"] = "closed"
    fetched_active["events"].clear()
    hist_copy = correlator.incident_history
    hist_copy[0]["events"][0]["message"] = "tampered"

    assert correlator.get_incident(active_inc["incident_id"])["status"] == "open"
    assert len(correlator.get_incident(active_inc["incident_id"])["events"]) == MAX_EVENTS_PER_INCIDENT
    assert correlator.incident_history[0]["events"][0]["message"] != "tampered"
