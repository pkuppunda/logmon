"""Human-readable terminal reporting."""

from hotel_automation.monitoring.parser import LEVEL_VALUES, LogEntry


def should_display(entry: LogEntry, minimum_level: str) -> bool:
    threshold = LEVEL_VALUES[minimum_level.upper()]
    return LEVEL_VALUES.get(entry.level, 0) >= threshold


def format_entry(entry: LogEntry) -> str:
    timestamp = entry.timestamp or "-"
    source = f" [{entry.source}]" if entry.source else ""
    return f"{timestamp} {entry.level:<8}{source} {entry.message}"


def display_entry(entry: LogEntry, minimum_level: str) -> None:
    if should_display(entry, minimum_level):
        print(format_entry(entry), flush=True)


def display_summary(analyzer) -> None:
    print("\n--- Monitoring summary ---")
    print(f"Events retained: {len(analyzer.history)}")
    print(f"Level counts: {analyzer.level_counts()}")

    repeated = analyzer.repeated_errors()
    if repeated:
        print("Repeated errors:")
        for level, message, count in repeated:
            print(f"  {level} x{count}: {message}")
    else:
        print("No repeated errors detected in the retained error history.")
