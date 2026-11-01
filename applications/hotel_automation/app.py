"""Script entry point: ``python applications/hotel_automation/app.py``."""

from pathlib import Path
import sys

if __package__ in (None, ""):
    package_dir = Path(__file__).resolve().parent
    sys.path[:] = [p for p in sys.path if Path(p or ".").resolve() != package_dir]
    sys.path.insert(0, str(package_dir.parent))

from hotel_automation.cli import main  # noqa: E402

if __name__ == "__main__":
    sys.exit(main())
