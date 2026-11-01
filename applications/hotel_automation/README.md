# hotel_automation

A self-contained hotel operations application used as the system under test for
`qa_with_app/`. It simulates hotel events (bookings, check-ins, payments,
housekeeping delays, database / payment-gateway outages and recoveries), writes
them to a log file, and ships the tooling that monitors those logs and
correlates failures into incidents.

## Layout

```text
applications/hotel_automation/
├── __init__.py        # public API re-exports
├── __main__.py        # python -m hotel_automation
├── app.py             # python applications/hotel_automation/app.py
├── cli.py             # argument parsing, signal handling
├── config.py          # AppConfig + HOTEL_AUTOMATION_* environment variables
├── service.py         # HotelAutomationService lifecycle (start/run/stop)
├── simulator.py       # HotelEventSimulator (seedable, stateful)
├── monitoring/        # parser, reader, analyzer, report, classifier, LogMonitor
└── incidents/         # IncidentCorrelator, root-cause ranking, lifecycle
```

## Requirements

- Python 3.10+
- Runtime: standard library only
- Tests: `pytest>=8.0` (see the project-root `requirements.txt`)

```bash
python3 -m pip install -r requirements.txt
```

## Running the application

From the project root:

```bash
PYTHONPATH=applications python3 -m hotel_automation --interval 1 --seed 7
# or
python3 applications/hotel_automation/app.py --max-events 20
```

Stop it with `Ctrl+C` (SIGINT) or SIGTERM. Shutdown is graceful: the final log
line is `Hotel automation application stopped` and the process exits with 0.

Monitor its logs with the bundled monitor:

```python
from hotel_automation import IncidentCorrelator, LogMonitor

monitor = LogMonitor(app_dir="applications/hotel_automation", correlator=IncidentCorrelator())
monitor.run(follow=True)
```

## Configuration

Command-line flags override environment variables, which override defaults.

| Environment variable           | Flag           | Default                                 | Meaning                              |
|--------------------------------|----------------|-----------------------------------------|--------------------------------------|
| `HOTEL_AUTOMATION_LOG_DIR`     | `--log-dir`    | `applications/hotel_automation/logs`    | Directory for log files              |
| `HOTEL_AUTOMATION_LOG_FILE`    | `--log-file`   | `app.log`                               | Log file name (plain name, no paths) |
| `HOTEL_AUTOMATION_INTERVAL`    | `--interval`   | `2.0`                                   | Seconds between events (> 0)         |
| `HOTEL_AUTOMATION_SEED`        | `--seed`       | unset (random)                          | Seed for repeatable event sequences  |
| `HOTEL_AUTOMATION_MAX_EVENTS`  | `--max-events` | unset (run until stopped)               | Exit after this many events (>= 1)   |

Invalid values are rejected at startup (`argparse` exit code 2). See
`.env.example` for a template.

Log line format: `YYYY-MM-DD HH:MM:SS LEVEL message`.

## Tests

```bash
python3 -m pytest -q -p no:cacheprovider qa_with_app
```

`qa_with_app/conftest.py` puts `applications/` on `sys.path`. Tests that need a
running app start it as a subprocess writing into a per-test `tmp_path` with
`HOTEL_AUTOMATION_LOG_DIR` set, wait for the startup line, and stop it
during teardown (SIGINT, then kill if needed). No manual startup is required,
and nothing is written to `applications/hotel_automation/logs/`.
