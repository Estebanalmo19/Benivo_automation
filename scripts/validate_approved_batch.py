#!/usr/bin/env python
"""
READ-ONLY preview of an approved-batch JSON artifact's eligibility --
Phase 5 of the UAT cutover preparation (confirmed 2026-09-08).

Loads and evaluates an approved-batch file using the exact same,
already-tested functions posting_service.select_postable_candidates() uses
for real -- app.services.approved_batch_service.load_approved_batch() /
evaluate_approved_batch(). No new selection logic, no new safety rule, no
relaxed rule: this is a preview of what the real mechanism would do, not a
separate implementation of it.

This script performs ONLY read-only SELECT queries -- the exact same three
repository reads evaluate_approved_batch() always performs
(candidate_repository.get_candidate_by_application_eid(),
candidate_repository.get_source_workflow_state(),
post_log_repository.get_terminal_post_log_application_eids()). It imports
no Benivo client, no reporting_service, no report_delivery_service, no
posting_service -- it makes no Benivo HTTP request, generates no report,
delivers nothing to Power Automate, calls no Create User/Case PATCH, and
writes nothing to any table or to the batch file itself.

Usage:
    python scripts/validate_approved_batch.py config/approved_batches/<file>.json

Output (non-PII only): BATCH_NAME, APPROVED_EIDS_COUNT, ELIGIBLE_APPROVED_COUNT,
INELIGIBLE_APPROVED_COUNT, the eligible application_eids, and for each
ineligible one, its application_eid and reason. Never prints a candidate's
name/email/phone/any other field -- only application_eid ever reaches
output, exactly like evaluate_approved_batch()'s own audit logging.

Exit code 0 only if every approved application_eid is currently eligible;
1 otherwise (including a missing/invalid batch file or configuration
error) -- so this script's own exit code can gate a later step (e.g. "only
proceed to canary posting if this returns 0").
"""

import sys
from pathlib import Path
from typing import List, Optional

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import config  # noqa: E402
from app.logging_config import configure_logging  # noqa: E402
from app.services.approved_batch_service import (  # noqa: E402
    ApprovedBatchLoadError,
    evaluate_approved_batch,
    load_approved_batch,
)


def main(argv: Optional[List[str]] = None) -> int:
    configure_logging()
    argv = argv if argv is not None else sys.argv[1:]

    if len(argv) != 1:
        print(f"Usage: python {Path(__file__).name} <approved batch JSON file>")
        return 1

    file_path = argv[0]

    try:
        config.validate()
    except RuntimeError as exc:
        print(f"Configuration error -- stopping, no query made: {exc}")
        return 1

    try:
        batch = load_approved_batch(file_path)
    except ApprovedBatchLoadError as exc:
        print(f"Approved batch file is invalid -- stopping, no query made: {exc}")
        return 1

    application_eids = batch["application_eids"]
    batch_name = batch.get("batch_name") or Path(file_path).stem
    result = evaluate_approved_batch(application_eids)

    print(f"BATCH_NAME = {batch_name}")
    print(f"APPROVED_EIDS_COUNT = {len(application_eids)}")
    print(f"ELIGIBLE_APPROVED_COUNT = {len(result['eligible'])}")
    print(f"INELIGIBLE_APPROVED_COUNT = {len(result['ineligible'])}")

    if result["eligible"]:
        print("\neligible application_eids:")
        for candidate in result["eligible"]:
            print(f"  {candidate.get('application_eid')}")

    if result["ineligible"]:
        print("\nineligible application_eids (reason):")
        for entry in result["ineligible"]:
            print(f"  {entry['application_eid']}: {entry['reason']}")

    return 0 if len(result["ineligible"]) == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
