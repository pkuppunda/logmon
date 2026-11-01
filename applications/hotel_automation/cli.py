"""Command-line entry point for the hotel automation application."""

from __future__ import annotations

import argparse
import signal
import sys
from typing import Optional, Sequence

from hotel_automation.config import AppConfig, ConfigError
from hotel_automation.service import HotelAutomationService


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="hotel_automation",
        description=(
            "Simulate hotel operations and write log events. Defaults come from "
            "HOTEL_AUTOMATION_* environment variables."
        ),
    )
    parser.add_argument("--log-dir", help="Directory for log files.")
    parser.add_argument("--log-file", help="Log file name (default: app.log).")
    parser.add_argument(
        "--interval", type=float, help="Seconds between events (default: 2.0)."
    )
    parser.add_argument("--seed", type=int, help="Random seed for repeatable runs.")
    parser.add_argument(
        "--max-events",
        type=int,
        help="Stop after emitting this many events (default: run until stopped).",
    )
    return parser


def load_config(args: argparse.Namespace) -> AppConfig:
    base = AppConfig.from_env()
    return base.with_overrides(
        log_dir=args.log_dir,
        log_file_name=args.log_file,
        interval=args.interval,
        seed=args.seed,
        max_events=args.max_events,
    )


def _install_signal_handlers(service: HotelAutomationService) -> None:
    def _handle(signum, frame):
        service.request_stop()

    signal.signal(signal.SIGINT, _handle)
    if hasattr(signal, "SIGTERM"):
        signal.signal(signal.SIGTERM, _handle)
    if hasattr(signal, "SIGBREAK"):
        signal.signal(signal.SIGBREAK, _handle)


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    try:
        config = load_config(args)
    except ConfigError as exc:
        parser.error(str(exc))

    service = HotelAutomationService(config)
    _install_signal_handlers(service)

    print(f"Hotel automation logging to {config.log_file}", flush=True)
    emitted = service.run()
    print(f"Hotel automation stopped after {emitted} events", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
