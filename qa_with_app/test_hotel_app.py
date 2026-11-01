"""End-to-end behaviour of the live hotel_automation application."""

from datetime import datetime, timedelta

import pytest

from hotel_automation.cli import main as cli_main
from hotel_automation.config import AppConfig, ConfigError
from hotel_automation.incidents import IncidentCorrelator
from hotel_automation.monitoring.classifier import classify_entry
from hotel_automation.monitoring.monitor import LogMonitor
from hotel_automation.monitoring.parser import LEVEL_VALUES, parse_line
from hotel_automation.service import HotelAutomationService

from qa_with_app.conftest import STARTED_MESSAGE, STOPPED_MESSAGE, read_log, wait_until


def _event_lines(lines):
    """Return (level, message) pairs for simulated events, skipping lifecycle lines."""
    pairs = []
    for line in lines:
        entry = parse_line(line)
        if entry.message in (STARTED_MESSAGE, STOPPED_MESSAGE):
            continue
        pairs.append((entry.level, entry.message))
    return pairs


def test_live_app_starts_emits_parseable_events_and_shuts_down_gracefully(
    start_hotel_app,
):
    app = start_hotel_app(interval=0.02)

    assert wait_until(lambda: len(app.log_lines()) >= 10, timeout=15)
    assert app.is_running()

    assert app.stop() == 0
    assert not app.is_running()

    lines = app.log_lines()
    assert STARTED_MESSAGE in lines[0]
    assert STOPPED_MESSAGE in lines[-1]
    assert sum(STOPPED_MESSAGE in line for line in lines) == 1

    for line in lines:
        entry = parse_line(line)
        assert entry.timestamp is not None, line
        assert entry.level in LEVEL_VALUES

    events = _event_lines(lines)
    assert len(events) >= 9
    assert all(level in {"INFO", "WARNING", "ERROR"} for level, _ in events)

    size_after_stop = app.log_file.stat().st_size
    assert not wait_until(
        lambda: app.log_file.stat().st_size != size_after_stop, timeout=0.3
    )


def test_seeded_runs_are_repeatable_across_subprocess_and_in_process_service(
    start_hotel_app, tmp_path
):
    app = start_hotel_app(interval=0.01, seed=42, max_events=25, wait_ready=False)
    assert app.wait_for_exit() == 0
    subprocess_events = _event_lines(app.log_lines())

    config = AppConfig(log_dir=tmp_path / "in_process", interval=0.01, seed=42, max_events=25)
    with HotelAutomationService(config) as service:
        assert service.wait(timeout=15)
    assert not service.is_running
    in_process_lines = read_log(config.log_file).splitlines()

    assert len(subprocess_events) == 25
    assert subprocess_events == _event_lines(in_process_lines)
    assert STOPPED_MESSAGE in in_process_lines[-1]

    # The file handle is released after shutdown, so the log can be removed.
    config.log_file.unlink()
    assert not config.log_file.exists()


def test_monitor_follows_live_app_without_duplicates_or_losses(start_hotel_app):
    app = start_hotel_app(interval=0.02)
    monitor = LogMonitor(app_dir=app.app_dir, scan_existing=True, display=False)

    try:
        total = monitor.poll_once()
        assert wait_until(lambda: len(app.log_lines()) >= 15, timeout=15)
        for _ in range(5):
            total += monitor.poll_once()

        assert app.stop() == 0
        total += monitor.poll_once()
        assert monitor.poll_once() == 0
    finally:
        monitor.close()

    lines = app.log_lines()
    assert total == len(lines)
    assert [entry.raw for entry in monitor.analyzer.history] == lines

    expected_counts = {}
    for line in lines:
        level = parse_line(line).level
        expected_counts[level] = expected_counts.get(level, 0) + 1
    assert monitor.analyzer.level_counts() == expected_counts


def test_live_app_failures_correlate_into_one_incident_per_subsystem(start_hotel_app):
    app = start_hotel_app(interval=0.01, seed=7, max_events=60, wait_ready=False)
    assert app.wait_for_exit() == 0

    correlator = IncidentCorrelator(correlation_window_seconds=60, close_after_seconds=120)
    with LogMonitor(
        app_dir=app.app_dir, scan_existing=True, display=False, correlator=correlator
    ) as monitor:
        assert monitor.poll_once() == len(app.log_lines())

    classified = [classify_entry(parse_line(line)) for line in app.log_lines()]
    failures = [e for e in classified if e and e["level"] == "ERROR"]
    recoveries = [e for e in classified if e and e["event_code"].endswith(("RESTORED", "RECOVERED"))]
    assert failures and recoveries

    incidents = correlator.active_incidents
    failing_components = {e["component"] for e in failures}
    assert len(incidents) == len(failing_components) == 2

    for incident in incidents:
        components = {event["component"] for event in incident["events"]}
        assert len(components) == 1
        (component,) = components
        assert incident["root_cause"].startswith(f"{component} (")
        assert 0.0 < incident["root_cause_confidence"] < 1.0
        assert all(event["level"] != "WARNING" for event in incident["events"])

    assert sum(i["event_count"] for i in incidents) == len(failures) + len(recoveries)

    latest = max(datetime.strptime(e["timestamp"], "%Y-%m-%d %H:%M:%S") for e in failures)
    closed = correlator.close_stale_incidents(latest + timedelta(seconds=120))
    assert {c["incident_id"] for c in closed} == {i["incident_id"] for i in incidents}
    assert correlator.active_incidents == []
    assert len(correlator.incident_history) == 2


def test_configuration_from_environment_and_invalid_settings(tmp_path, monkeypatch, capsys):
    config = AppConfig.from_env(
        {
            "HOTEL_AUTOMATION_LOG_DIR": str(tmp_path),
            "HOTEL_AUTOMATION_LOG_FILE": "hotel.log",
            "HOTEL_AUTOMATION_INTERVAL": "0.5",
            "HOTEL_AUTOMATION_SEED": "11",
            "HOTEL_AUTOMATION_MAX_EVENTS": "3",
        }
    )
    assert config.log_file == tmp_path / "hotel.log"
    assert (config.interval, config.seed, config.max_events) == (0.5, 11, 3)
    assert config.with_overrides(interval=1.0, seed=None).seed == 11

    for invalid_env in (
        {"HOTEL_AUTOMATION_INTERVAL": "0"},
        {"HOTEL_AUTOMATION_INTERVAL": "-1"},
        {"HOTEL_AUTOMATION_INTERVAL": "fast"},
        {"HOTEL_AUTOMATION_SEED": "1.5"},
        {"HOTEL_AUTOMATION_MAX_EVENTS": "0"},
        {"HOTEL_AUTOMATION_LOG_FILE": "../escape.log"},
    ):
        with pytest.raises(ConfigError):
            AppConfig.from_env(invalid_env)

    with pytest.raises(ConfigError):
        AppConfig(interval=True)

    monkeypatch.setenv("HOTEL_AUTOMATION_LOG_DIR", str(tmp_path / "cli"))
    with pytest.raises(SystemExit) as excinfo:
        cli_main(["--interval", "0"])
    assert excinfo.value.code == 2
    assert "interval must be greater than zero" in capsys.readouterr().err
    assert not (tmp_path / "cli").exists()
