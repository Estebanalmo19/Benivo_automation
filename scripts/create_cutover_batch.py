#!/usr/bin/env python
"""
Generates the FULL approved historical-cutover batch artifact directly from
a T0 timestamp -- Change 2 of the UAT cutover safeguards (confirmed
2026-09-08). Replaces manually rebuilding
config/approved_batches/*.json by hand from scripts/derive_historical_batch.py's
printed output.

Selection source: candidate_repository.get_historical_batch_candidates(t0)
-- the SAME function derive_historical_batch.py already uses, unchanged, no
new business rule. Membership therefore requires: benivo_status =
READY_TO_POST, is_relocation_required = Yes, a valid non-blank
application_eid, the live authoritative Jobvite workflow re-check
(workflow_state = 'Mobility in process'), no terminal CREATE_USER
SUCCESS/ALREADY_EXISTS result, and scope_history.first_seen_in_scope_at <
T0.

READ-ONLY against the database: this script performs only the one SELECT
inside get_historical_batch_candidates(). It writes a local JSON file and
nothing else -- no row in any table is ever inserted, updated, or deleted.
It imports no Benivo client, no reporting_service, no
report_delivery_service -- no Benivo HTTP request, no Power Automate
delivery, no report is ever produced by this script.

Fails closed: an empty result, a duplicate application_eid (defensive --
should be structurally impossible given application_eid is the primary key
of benivo.candidates, but checked anyway, consistent with
load_approved_batch()'s own duplicate policy), or an existing output file
without --force all abort with no file written.

Usage:
    python scripts/create_cutover_batch.py --t0 "2026-09-08T14:00:00+00:00" \\
        --output config/approved_batches/benivo_first_import_20260908.json \\
        [--batch-name benivo_first_import_20260908] [--approved-at 2026-09-08] \\
        [--approved-by "Mobility/GM"] [--force]

--batch-name defaults to the output file's stem (matches
load_approved_batch()'s own fallback when the key is absent).
--approved-at defaults to today's UTC date. --approved-by has no default --
omitted from the artifact entirely if not given, never guessed.
load_approved_batch() already tolerates and simply carries through any
extra metadata key beyond "application_eids" (confirmed by inspection --
it only requires and validates that one key), so batch_name/approved_at/
approved_by/source_cutover_timestamp need no change there.
"""

import argparse
import json
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import config  # noqa: E402
from app.logging_config import configure_logging  # noqa: E402
from app.repositories.candidate_repository import get_historical_batch_candidates  # noqa: E402


def parse_t0(raw: str) -> datetime:
    return datetime.fromisoformat(raw.strip().replace("Z", "+00:00"))


def build_batch_artifact(
    candidates: List[dict],
    t0: datetime,
    batch_name: str,
    approved_at: Optional[str],
    approved_by: Optional[str],
) -> dict:
    """
    Pure, in-memory. application_eids only in the identity list -- no
    workplace, no first_seen_in_scope_at, no PII of any kind carried into
    the artifact itself (those exist only in the candidates this function
    is given, never written out).
    """
    application_eids = [c["application_eid"] for c in candidates]

    duplicates = {eid for eid, count in Counter(application_eids).items() if count > 1}

    if duplicates:
        raise ValueError(f"Duplicate application_eid(s) in historical query result: {sorted(duplicates)}")

    artifact = {
        "batch_name": batch_name,
        "source_cutover_timestamp": t0.isoformat(),
        "application_eids": application_eids,
    }

    if approved_at:
        artifact["approved_at"] = approved_at

    if approved_by:
        artifact["approved_by"] = approved_by

    return artifact


def write_artifact(artifact: dict, output_path: Path, force: bool) -> None:
    if output_path.exists() and not force:
        raise FileExistsError(f"{output_path} already exists -- pass --force to overwrite.")

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(artifact, indent=2) + "\n", encoding="utf-8")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Generate the full approved historical-cutover batch artifact.")
    parser.add_argument("--t0", required=True, help="Cutover T0, ISO 8601 (e.g. 2026-09-08T14:00:00+00:00).")
    parser.add_argument("--output", required=True, help="Path to write the batch JSON artifact to.")
    parser.add_argument("--batch-name", default=None, help="Defaults to the output file's stem.")
    parser.add_argument("--approved-at", default=None, help="Defaults to today's UTC date. Omitted if not given and no default applies.")
    parser.add_argument("--approved-by", default=None, help="Never guessed -- omitted from the artifact if not given.")
    parser.add_argument("--force", action="store_true", help="Overwrite an existing output file.")
    return parser


def main(argv: Optional[List[str]] = None) -> int:
    configure_logging()
    args = build_parser().parse_args(argv if argv is not None else sys.argv[1:])

    try:
        t0 = parse_t0(args.t0)
    except ValueError as exc:
        print(f"Invalid --t0 timestamp: {args.t0!r} ({exc})")
        return 1

    try:
        config.validate()
    except RuntimeError as exc:
        print(f"Configuration error -- stopping, no query made: {exc}")
        return 1

    output_path = Path(args.output)
    batch_name = args.batch_name or output_path.stem
    approved_at = args.approved_at or datetime.now(timezone.utc).date().isoformat()

    candidates = get_historical_batch_candidates(t0)

    print(f"T0 = {t0.isoformat()}")
    print(f"HISTORICAL_ELIGIBLE_COUNT = {len(candidates)}")

    if not candidates:
        print("No historical candidates found -- refusing to write an empty batch file.")
        return 1

    try:
        artifact = build_batch_artifact(candidates, t0, batch_name, approved_at, args.approved_by)
    except ValueError as exc:
        print(f"Refusing to write batch file: {exc}")
        return 1

    try:
        write_artifact(artifact, output_path, args.force)
    except FileExistsError as exc:
        print(str(exc))
        return 1

    print(f"Wrote {len(artifact['application_eids'])} application_eid(s) to {output_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
