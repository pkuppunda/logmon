"""Runtime configuration for the hotel automation application.

Every setting can be supplied through an environment variable and overridden
on the command line. See ``README.md`` in this directory for details.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
import os
from pathlib import Path
from typing import Mapping, Optional

PACKAGE_DIR = Path(__file__).resolve().parent
DEFAULT_LOG_DIR = PACKAGE_DIR / "logs"
DEFAULT_LOG_FILE = "app.log"
DEFAULT_INTERVAL = 2.0

ENV_LOG_DIR = "HOTEL_AUTOMATION_LOG_DIR"
ENV_LOG_FILE = "HOTEL_AUTOMATION_LOG_FILE"
ENV_INTERVAL = "HOTEL_AUTOMATION_INTERVAL"
ENV_SEED = "HOTEL_AUTOMATION_SEED"
ENV_MAX_EVENTS = "HOTEL_AUTOMATION_MAX_EVENTS"


class ConfigError(ValueError):
    """Raised when a configuration value is invalid."""


def _parse_float(name: str, raw: str) -> float:
    try:
        return float(raw)
    except ValueError as exc:
        raise ConfigError(f"{name} must be a number, got {raw!r}") from exc


def _parse_int(name: str, raw: str) -> int:
    try:
        return int(raw)
    except ValueError as exc:
        raise ConfigError(f"{name} must be an integer, got {raw!r}") from exc


@dataclass(frozen=True)
class AppConfig:
    log_dir: Path = DEFAULT_LOG_DIR
    log_file_name: str = DEFAULT_LOG_FILE
    interval: float = DEFAULT_INTERVAL
    seed: Optional[int] = None
    max_events: Optional[int] = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "log_dir", Path(self.log_dir).expanduser())
        self.validate()

    @property
    def log_file(self) -> Path:
        return self.log_dir / self.log_file_name

    def validate(self) -> None:
        if not self.log_file_name or Path(self.log_file_name).name != self.log_file_name:
            raise ConfigError("log file name must be a plain file name")
        if isinstance(self.interval, bool) or not isinstance(self.interval, (int, float)):
            raise ConfigError("interval must be a number")
        if not self.interval > 0:
            raise ConfigError("interval must be greater than zero")
        if self.seed is not None and (
            isinstance(self.seed, bool) or not isinstance(self.seed, int)
        ):
            raise ConfigError("seed must be an integer")
        if self.max_events is not None and (
            isinstance(self.max_events, bool)
            or not isinstance(self.max_events, int)
            or self.max_events < 1
        ):
            raise ConfigError("max_events must be a positive integer")

    def with_overrides(self, **overrides) -> "AppConfig":
        return replace(
            self, **{key: value for key, value in overrides.items() if value is not None}
        )

    @classmethod
    def from_env(cls, environ: Optional[Mapping[str, str]] = None) -> "AppConfig":
        env = os.environ if environ is None else environ
        values: dict = {}

        if env.get(ENV_LOG_DIR):
            values["log_dir"] = Path(env[ENV_LOG_DIR])
        if env.get(ENV_LOG_FILE):
            values["log_file_name"] = env[ENV_LOG_FILE]
        if env.get(ENV_INTERVAL):
            values["interval"] = _parse_float(ENV_INTERVAL, env[ENV_INTERVAL])
        if env.get(ENV_SEED):
            values["seed"] = _parse_int(ENV_SEED, env[ENV_SEED])
        if env.get(ENV_MAX_EVENTS):
            values["max_events"] = _parse_int(ENV_MAX_EVENTS, env[ENV_MAX_EVENTS])

        return cls(**values)
