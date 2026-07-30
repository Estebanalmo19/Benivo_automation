#!/usr/bin/env python
"""
Read-only pre-flight check for a single candidate, without selecting,
posting, or writing anything. Never calls create-user or find-user; only
authenticates and fetches refdata (GET) to validate office resolution.

Usage:
    python scripts/validate_uat_candidate.py <application_eid>

Exit code 0 if every eligibility/pre-post check passes, 1 otherwise (with
the exact failed checks printed).
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import config  # noqa: E402
from app.clients import benivo_client  # noqa: E402
from app.logging_config import configure_logging  # noqa: E402
from app.services import posting_service  # noqa: E402


def main(argv=None) -> int:
    configure_logging()

    try:
        config.validate()
    except RuntimeError as exc:
        print(f"Configuration error: {exc}")
        return 1

    argv = argv if argv is not None else sys.argv[1:]

    if len(argv) != 1:
        print(f"Usage: python {Path(__file__).name} <application_eid>")
        return 1

    application_eid = argv[0]

    refdata = None
    try:
        token = benivo_client.get_access_token()
        refdata = benivo_client.get_refdata(token)
    except Exception as exc:
        print(f"Warning: could not fetch refdata ({exc}); office_resolved will read False.")

    result = posting_service.validate_uat_candidate(application_eid, refdata)

    print(f"application_eid: {application_eid}")
    print(f"eligible: {result['eligible']}")
    print("checks:")
    for name, passed in result["checks"].items():
        print(f"  {name}: {passed}")

    if result["summary"] is not None:
        print("summary:")
        for key, value in result["summary"].items():
            print(f"  {key}: {value}")

    return 0 if result["eligible"] else 1


if __name__ == "__main__":
    sys.exit(main())
