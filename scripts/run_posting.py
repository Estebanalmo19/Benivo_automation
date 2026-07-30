#!/usr/bin/env python
"""Convenience wrapper: python scripts/run_posting.py  ==  python -m app.main post

Respects BENIVO_DRY_RUN (defaults true). For a controlled single-candidate
UAT posting, set BENIVO_UAT_APPLICATION_EID first -- see .env.example.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.main import main  # noqa: E402

if __name__ == "__main__":
    sys.exit(main(["post"]))
