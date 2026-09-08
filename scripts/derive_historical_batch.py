#!/usr/bin/env python
"""
READ-ONLY listing of the pre-cutover historical backlog population for a
given T0 timestamp -- Phase 2 of the approved-batch UAT cutover
preparation (confirmed 2026-09-08).

Selection semantics: every candidate who currently passes every normal
posting safety gate (benivo_status = READY_TO_POST, is_relocation_required
= Yes, a valid non-blank application_eid, the live authoritative Jobvite
workflow re-check, no terminal CREATE_USER SUCCESS/ALREADY_EXISTS result)
AND whose benivo.scope_history.first_seen_in_scope_at is strictly BEFORE
T0. See app.repositories.candidate_repository.get_historical_batch_candidates()
for the exact query -- deliberately the mirror image of
get_ready_candidates()'s go-live clause (first_seen_in_scope_at >=
go_live_at), never first_seen >= T0, which would be the FUTURE automatic
population instead.

This script performs ONLY read-only SELECT queries (via the repository
function above). It imports no Benivo client, no reporting_service, no
report_delivery_service -- it makes no Benivo HTTP request, generates no
report, delivers nothing to Power Automate, and writes nothing to any
table. It does not itself create or write the approved-batch JSON artifact
-- capturing this output into config/approved_batches/*.json remains a
separate, later, deliberate, human-reviewed step.

Usage:
    python scripts/derive_historical_batch.py <T0 ISO8601 timestamp>
    python scripts/derive_historical_batch.py 2026-09-08T14:00:00+00:00

Output: T0, HISTORICAL_ELIGIBLE_COUNT, then one line per candidate --
application_eid, workplace, first_seen_in_scope_at only. No name, email,
or phone number is ever fetched (the underlying query never selects them)
or printed.
"""

import sys
from datetime import datetime
from pathlib import Path
from typing import List, Optional

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import config  # noqa: E402
from app.logging_config import configure_logging  # noqa: E402
from app.repositories.candidate_repository import get_historical_batch_candidates  # noqa: E402


def parse_t0(raw: str) -> datetime:
    return datetime.fromisoformat(raw.strip().replace("Z", "+00:00"))


def print_listing(t0: datetime, candidates: List[dict]) -> None:
    print(f"T0 = {t0.isoformat()}")
    print(f"HISTORICAL_ELIGIBLE_COUNT = {len(candidates)}")

    if not candidates:
        return

    print(f"\n{'application_eid':32s} {'workplace':30s} first_seen_in_scope_at")
    for row in candidates:
        print(f"{str(row['application_eid']):32s} {str(row['workplace']):30s} {row['first_seen_in_scope_at']}")


def main(argv: Optional[List[str]] = None) -> int:
    configure_logging()
    argv = argv if argv is not None else sys.argv[1:]

    if len(argv) != 1:
        print(f"Usage: python {Path(__file__).name} <T0 ISO8601 timestamp>")
        return 1

    try:
        t0 = parse_t0(argv[0])
    except ValueError as exc:
        print(f"Invalid T0 timestamp: {argv[0]!r} ({exc})")
        return 1

    try:
        config.validate()
    except RuntimeError as exc:
        print(f"Configuration error -- stopping, no query made: {exc}")
        return 1

    candidates = get_historical_batch_candidates(t0)
    print_listing(t0, candidates)
    return 0


if __name__ == "__main__":
    sys.exit(main())
