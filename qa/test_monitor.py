from applogmon.monitor import LogMonitor


def test_monitor_reads_and_counts_log_entries(tmp_path):
    app_dir = tmp_path / "myapp"
    log_dir = app_dir / "logs"
    log_dir.mkdir(parents=True)
    log_file = log_dir / "app.log"
    log_file.write_text(
        "2026-10-28 14:30:01 INFO Started\n"
        "2026-10-28 14:30:02 ERROR Failed\n",
        encoding="utf-8",
    )

    monitor = LogMonitor(
        app_dir=app_dir,
        minimum_level="ERROR",
        scan_existing=True,
    )

    assert monitor.poll_once() == 2
    assert len(monitor.analyzer.history) == 2
    assert monitor.analyzer.level_counts() == {"INFO": 1, "ERROR": 1}

    for reader in monitor.readers.values():
        reader.close()


def test_monitor_does_not_duplicate_old_entries(tmp_path):
    log_dir = tmp_path / "myapp" / "logs"
    log_dir.mkdir(parents=True)
    log_file = log_dir / "app.log"
    log_file.write_text("old line\n", encoding="utf-8")

    monitor = LogMonitor(
        app_dir=log_dir.parent,
        scan_existing=True,
    )

    try:
        assert monitor.poll_once() == 1
        assert monitor.poll_once() == 0
    finally:
        for reader in monitor.readers.values():
            reader.close()
