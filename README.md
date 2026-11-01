# log Monitor

A Python log-monitoring application for reading, parsing, analyzing, and displaying application logs. The project also includes regression tests for event history, subscriber cleanup, and memory-management behavior.

## Quick start

Create and activate a virtual environment:

```bash
python -m venv .venv
```

**Linux / WSL:**

```bash
source .venv/bin/activate
```

**Windows PowerShell:**

```powershell
.venv\Scripts\Activate.ps1
```

Install dependencies and run the test suite:

```bash
python -m pip install -r requirements.txt
python -m pytest -q
```

## Run the application

From the project root, run the CLI with the source directory on the Python import path.

**Linux / WSL:**

```bash
PYTHONPATH=src python -m applogmon --help
```

**Windows PowerShell:**

```powershell
$env:PYTHONPATH = "src"
python -m applogmon --help
```

The CLI supports options including:

- `--app`: application directory to monitor.
- `--pattern`: log-file pattern; defaults to `logs/*.log`.
- `--level`: minimum displayed log level.
- `--follow`: continue monitoring for new log entries.
- `--scan-existing`: scan existing log content.
- `--interval`: polling interval in seconds.
- `--history-limit`: maximum number of events retained in history.

Use `--help` to see the supported options.

## What to investigate

- Keep event history within `history_limit` while retaining the newest events.
- Ensure `unsubscribe()` removes its callback and repeated calls are safe.
- Use `weakref.ref()` and `gc.collect()` to verify that a closed dashboard can be collected.
- Optionally use the standard-library `tracemalloc` module to compare memory snapshots.

Deterministic checks, such as history length, subscriber count, and weak references, are generally more reliable than exact memory-byte thresholds.

## Project layout

```text
logmon/
├── src/
│   └── applogmon/
│       ├── __init__.py
│       ├── __main__.py
│       ├── monitor.py
│       ├── reader.py
│       ├── parser.py
│       ├── analyzer.py
│       ├── report.py
│       └── spike_detector.py
├── applications/
│   ├── hotel_automation/
│   └── csv_processor/
├── qa/
│   ├── test_reader.py
│   ├── test_parser.py
│   ├── test_monitor.py
│   └── test_memory_leaks.py
├── pyproject.toml
├── requirements.txt
├── EXERCISES.md
└── README.md
```

## Testing

Run all tests:

```bash
python -m pytest -q
```

Run a specific test module:

```bash
python -m pytest qa/test_reader.py -v
```

## hotel_automation application and `qa_with_app`

`applications/hotel_automation/` is a standalone application (standard library
only) that simulates hotel operations, writes logs, and provides the log
monitoring and incident-correlation interfaces exercised by `qa_with_app/`.
Setup, configuration, and `HOTEL_AUTOMATION_*` environment variables are
documented in `applications/hotel_automation/README.md`.

Run the suite from the project root (the app is started and stopped
automatically by the test fixtures):

```bash
python3 -m pytest -q -p no:cacheprovider qa_with_app
```
