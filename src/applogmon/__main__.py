"""Command-line entry point."""

import argparse
from pathlib import Path

from applogmon.monitor import LogMonitor


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="applogmon",
        description="Monitor application log files in real time.",
    )
    parser.add_argument(
        "--app",
        required=True,
        help="Application directory containing log files.",
    )
    parser.add_argument(
        "--pattern",
        default="logs/*.log",
        help="Log file glob relative to the application directory.",
    )
    parser.add_argument(
        "--level",
        choices=["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"],
        default="WARNING",
        help="Minimum log level to display.",
    )
    parser.add_argument(
        "--follow",
        action="store_true",
        help="Wait for and display newly appended log entries.",
    )
    parser.add_argument(
        "--scan-existing",
        action="store_true",
        help="Read existing log contents before following new entries.",
    )
    parser.add_argument(
        "--interval",
        type=float,
        default=0.5,
        help="Polling interval in seconds (default: 0.5).",
    )
    parser.add_argument(
        "--history-limit",
        type=int,
        default=1000,
        help="Maximum number of events retained in memory.",
    )
    
    parser.add_argument(
        "--spike-window",
        type=int,
        default=60,
        help="Rolling window in seconds (default: 60).",
    )
    parser.add_argument(
        "--spike-threshold",
        type=int,
        default=10,
        help="Number of errors needed to trigger an alert (default: 10).",
    )
    parser.add_argument(
        "--spike-levels",
        default="ERROR,CRITICAL",
        help="Comma-separated levels to count (default: ERROR,CRITICAL).",
    )
    parser.add_argument(
        "--spike-cooldown",
        type=int,
        default=60,
        help="Minimum seconds between alerts (default: 60).",
    )

    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()

    if args.interval <= 0:
        parser.error("--interval must be greater than zero.")

    if args.history_limit < 1:
        parser.error("--history-limit must be at least 1.")
        
    if args.spike_window <= 0:
    parser.error("--spike-window must be greater than zero.")

    if args.spike_threshold <= 0:
        parser.error("--spike-threshold must be greater than zero.")

    if args.spike_cooldown < 0:
        parser.error("--spike-cooldown cannot be negative.")

    spike_levels = tuple(
        level.strip().upper()
        for level in args.spike_levels.split(",")
        if level.strip()
    )

    if not spike_levels:
        parser.error("--spike-levels must contain at least one level.")

    valid_levels = {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}
    invalid_levels = set(spike_levels) - valid_levels
    if invalid_levels:
        parser.error(
            f"Invalid spike levels: {', '.join(sorted(invalid_levels))}"
        )

    app_dir = Path(args.app).expanduser().resolve()
    if not app_dir.is_dir():
        parser.error(f"Application directory does not exist: {app_dir}")

    monitor = LogMonitor(
        app_dir=app_dir,
        pattern=args.pattern,
        minimum_level=args.level,
        history_limit=args.history_limit,
        poll_interval=args.interval,
        scan_existing=args.scan_existing,
        spike_window=args.spike_window,
        spike_threshold=args.spike_threshold,
        spike_levels=spike_levels,
        spike_cooldown=args.spike_cooldown,
    )

    try:
        monitor.run(follow=args.follow)
    except KeyboardInterrupt:
        print("\nMonitoring stopped.")


if __name__ == "__main__":
    main()
