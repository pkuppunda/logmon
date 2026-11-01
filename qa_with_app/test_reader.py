from pathlib import Path

from hotel_automation.monitoring.reader import LogReader


def test_reader_reads_only_new_lines(tmp_path: Path):
    path = tmp_path / "app.log"
    path.write_text("first\n", encoding="utf-8")

    reader = LogReader(path)
    try:
        assert reader.read_new_lines() == []

        with path.open("a", encoding="utf-8") as file:
            file.write("second\n")

        assert reader.read_new_lines() == ["second\n"]
        assert reader.read_new_lines() == []
    finally:
        reader.close()


def test_reader_can_scan_existing_content(tmp_path: Path):
    path = tmp_path / "app.log"
    path.write_text("existing\n", encoding="utf-8")

    reader = LogReader(path, scan_existing=True)
    try:
        assert reader.read_new_lines() == ["existing\n"]
    finally:
        reader.close()


def test_reader_detects_truncation(tmp_path: Path):
    path = tmp_path / "app.log"
    path.write_text("a line that is long\n", encoding="utf-8")

    reader = LogReader(path, scan_existing=True)
    try:
        assert reader.read_new_lines() == ["a line that is long\n"]

        path.write_text("new\n", encoding="utf-8")
        assert reader.read_new_lines() == ["new\n"]
    finally:
        reader.close()
