#!/usr/bin/env python
"""
One-time baseline backfill for benivo.scope_history -- REQUIRED before
go-live (setting BENIVO_GO_LIVE_AT) so the existing READY_TO_POST backlog
is correctly recognized as pre-existing, not "new".

Must run, in order:
  1. AFTER migrations/0008_add_scope_history_table.sql is applied.
  2. BEFORE BENIVO_GO_LIVE_AT is ever set.

What it does: inserts one row per application_eid known from EITHER
  - benivo.candidates (the current backlog, still in scope), UNION
  - benivo.post_log (every application_eid ever attempted, including ones
    whose benivo.candidates row has since been deleted by
    synchronization_service._DELETE_OUT_OF_SCOPE_SQL after they left
    Mobility)
with first_seen_in_scope_at = NOW() (the backfill run time -- guaranteed
before go-live, since go-live isn't enabled yet when this runs).
ON CONFLICT DO NOTHING makes this safe to run more than once.

KNOWN LIMITATION (accepted, not fixable from this data alone): a candidate
who entered scope, then fully left scope again, before ever being posted
(no post_log row) and before this backfill ran, has no trace in either
source table and cannot be backfilled -- if they later re-enter Mobility
post-go-live, they will look "new" even though they technically existed
pre-go-live. Recovering that population would require querying raw
Jobvite workflow history directly (jv_arrise_data_schema.jobvite_applications),
which is out of scope for this script. Run this backfill as close to the
actual go-live cutover as practical to minimize this window.

Usage:
    python scripts/backfill_scope_history.py --check   # counts only, no write
    python scripts/backfill_scope_history.py --apply   # perform the backfill
"""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import config  # noqa: E402
from app.clients.database_client import transaction  # noqa: E402
from app.logging_config import configure_logging  # noqa: E402

_COUNT_SQL = """
SELECT
    (SELECT COUNT(*) FROM benivo.scope_history) AS already_backfilled,
    (
        SELECT COUNT(*) FROM (
            SELECT application_eid FROM benivo.candidates WHERE application_eid IS NOT NULL
            UNION
            SELECT application_eid FROM benivo.post_log WHERE application_eid IS NOT NULL
        ) known
    ) AS known_application_eids
"""

_BACKFILL_SQL = """
INSERT INTO benivo.scope_history (application_eid, candidate_eid, first_seen_in_scope_at)
SELECT known.application_eid, MAX(known.candidate_eid), NOW()
FROM (
    SELECT application_eid, candidate_eid FROM benivo.candidates WHERE application_eid IS NOT NULL
    UNION ALL
    SELECT application_eid, candidate_eid FROM benivo.post_log WHERE application_eid IS NOT NULL
) known
GROUP BY known.application_eid
ON CONFLICT (application_eid) DO NOTHING
"""


def check() -> int:
    with transaction() as cur:
        cur.execute(_COUNT_SQL)
        row = cur.fetchone()

    print(f"Already in benivo.scope_history: {row['already_backfilled']}")
    print(f"Known application_eids (benivo.candidates UNION benivo.post_log): {row['known_application_eids']}")
    print(f"Would insert up to: {row['known_application_eids'] - row['already_backfilled']}")
    return 0


def apply_backfill() -> int:
    with transaction() as cur:
        cur.execute(_BACKFILL_SQL)
        inserted = cur.rowcount

    print(f"Backfilled {inserted} row(s) into benivo.scope_history.")
    return 0


def main(argv=None) -> int:
    configure_logging()

    try:
        config.validate()
    except RuntimeError as exc:
        print(f"Configuration error: {exc}")
        return 1

    parser = argparse.ArgumentParser(description="One-time benivo.scope_history baseline backfill.")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--check", action="store_true", help="Print counts only; write nothing.")
    group.add_argument("--apply", action="store_true", help="Perform the backfill.")

    args = parser.parse_args(argv if argv is not None else sys.argv[1:])

    return check() if args.check else apply_backfill()


if __name__ == "__main__":
    sys.exit(main())
