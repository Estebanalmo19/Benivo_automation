#!/usr/bin/env python
"""Convenience wrapper: python scripts/run_sync.py  ==  python -m app.main sync"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.main import main  # noqa: E402

if __name__ == "__main__":
    sys.exit(main(["sync"]))
