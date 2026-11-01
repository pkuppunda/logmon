"""Hotel automation application: event simulation, log monitoring, and incidents."""

__version__ = "1.0.0"

from hotel_automation.config import AppConfig, ConfigError
from hotel_automation.incidents import MAX_EVENTS_PER_INCIDENT, IncidentCorrelator
from hotel_automation.monitoring import (
    ErrorAnalyzer,
    LogEntry,
    LogMonitor,
    LogReader,
    classify_entry,
    parse_line,
)
from hotel_automation.service import HotelAutomationService
from hotel_automation.simulator import HotelEventSimulator

__all__ = [
    "AppConfig",
    "ConfigError",
    "ErrorAnalyzer",
    "HotelAutomationService",
    "HotelEventSimulator",
    "IncidentCorrelator",
    "LogEntry",
    "LogMonitor",
    "LogReader",
    "MAX_EVENTS_PER_INCIDENT",
    "classify_entry",
    "parse_line",
]
