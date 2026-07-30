#!/usr/bin/env python
"""Convenience wrapper: python scripts/run_report.py  ==  python -m app.main report

Never posts for real, regardless of BENIVO_DRY_RUN -- generates the
operational Excel report reflecting the currently synced/classified data.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.main import main  # noqa: E402

if __name__ == "__main__":
    sys.exit(main(["report"]))
