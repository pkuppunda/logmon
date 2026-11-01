"""Allow ``python -m hotel_automation`` (with ``applications`` on PYTHONPATH)."""

import sys

from hotel_automation.cli import main

if __name__ == "__main__":
    sys.exit(main())
