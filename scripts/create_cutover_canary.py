#!/usr/bin/env python
"""
Generates the 3-candidate UAT canary batch artifact from an already-existing
approved full historical batch -- Change 3 of the UAT cutover safeguards
(confirmed 2026-09-08).

Selects exactly one application_eid from each of three workplace groups:
  1. {"RAK Live Casino", "UAE"}          -> exercises UAE (Live Casino)
  2. {"Serbia Live Casino"}                -> exercises Serbia (Live Casino)
  3. {"Romania Live Casino", "Latvia", "Malta"} -> exercises a third distinct office

Within each group: earliest first_seen_in_scope_at, then lowest
application_eid as a deterministic tie-break (ORDER BY first_seen_in_scope_at,
application_eid -- the same ordering candidate_repository.
get_workplace_and_first_seen() and get_historical_batch_candidates() both
already produce).

STRUCTURALLY cannot introduce an application_eid outside the full batch:
the workplace/first_seen lookup is restricted to exactly the full batch
file's own application_eids (WHERE application_eid = ANY(...)) -- there is
no other source of candidate identities anywhere in this script.

READ-ONLY against the database (the one lookup query inside
get_workplace_and_first_seen()) -- no row in any table is ever inserted,
updated, or deleted. No Benivo client, no reporting_service, no
report_delivery_service imported -- no Benivo HTTP request, no report, no
Power Automate delivery.

Fails closed if any of the three required groups has no eligible member in
the full batch, or if the full batch file itself is invalid (reuses
load_approved_batch()'s own fail-closed validation -- no separate JSON
parsing logic here).

Output: the same batch artifact schema as create_cutover_batch.py,
containing exactly the 3 selected application_eids -- workplace and
first_seen_in_scope_at are used only to choose them, never written into
the output artifact (no PII, no operational metadata beyond the identity
list itself).

Usage:
    python scripts/create_cutover_canary.py \\
        --batch config/approved_batches/benivo_first_import_20260908.json \\
        --output config/approved_batches/benivo_first_import_20260908_canary.json \\
        [--batch-name benivo_first_import_20260908_canary] [--force]
"""

import argparse
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import config  # noqa: E402
from app.logging_config import configure_logging  # noqa: E402
from app.repositories.candidate_repository import get_workplace_and_first_seen  # noqa: E402
from app.services.approved_batch_service import ApprovedBatchLoadError, load_approved_batch  # noqa: E402
from scripts.create_cutover_batch import write_artifact  # noqa: E402

CANARY_GROUPS = [
    ("uae_or_rak", {"RAK Live Casino", "UAE"}),
    ("serbia", {"Serbia Live Casino"}),
    ("romania_or_latvia_or_malta", {"Romania Live Casino", "Latvia", "Malta"}),
]


def select_canary_eids(workplace_rows: List[Dict[str, Any]]) -> List[str]:
    """
    workplace_rows: [{"application_eid", "workplace", "first_seen_in_scope_at"}, ...],
    already ORDER BY first_seen_in_scope_at, application_eid (see
    get_workplace_and_first_seen()). Picks the first row whose workplace
    falls in each of CANARY_GROUPS, in order. Raises ValueError naming
    exactly which group had no member -- fail closed, no partial canary.
    """
    selected: List[str] = []

    for group_name, workplaces in CANARY_GROUPS:
        match = next((row for row in workplace_rows if row["workplace"] in workplaces), None)

        if match is None:
            raise ValueError(
                f"No eligible candidate found for canary group {group_name!r} "
                f"(workplaces {sorted(workplaces)}) in the full batch."
            )

        selected.append(match["application_eid"])

    return selected


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Generate the 3-EID UAT canary batch artifact from an existing full approved batch."
    )
    parser.add_argument("--batch", required=True, help="Path to the full approved batch JSON artifact.")
    parser.add_argument("--output", required=True, help="Path to write the canary JSON artifact to.")
    parser.add_argument("--batch-name", default=None, help="Defaults to the output file's stem.")
    parser.add_argument("--approved-at", default=None, help="Never guessed -- omitted from the artifact if not given.")
    parser.add_argument("--approved-by", default=None, help="Never guessed -- omitted from the artifact if not given.")
    parser.add_argument("--force", action="store_true", help="Overwrite an existing output file.")
    return parser


def main(argv: Optional[List[str]] = None) -> int:
    configure_logging()
    args = build_parser().parse_args(argv if argv is not None else sys.argv[1:])

    try:
        config.validate()
    except RuntimeError as exc:
        print(f"Configuration error -- stopping, no query made: {exc}")
        return 1

    try:
        full_batch = load_approved_batch(args.batch)
    except ApprovedBatchLoadError as exc:
        print(f"Full batch file is invalid -- stopping, no query made: {exc}")
        return 1

    full_batch_eids = full_batch["application_eids"]
    workplace_rows = get_workplace_and_first_seen(full_batch_eids)

    try:
        canary_eids = select_canary_eids(workplace_rows)
    except ValueError as exc:
        print(f"Refusing to write canary file: {exc}")
        return 1

    output_path = Path(args.output)
    batch_name = args.batch_name or output_path.stem

    artifact: Dict[str, Any] = {
        "batch_name": batch_name,
        "source_full_batch": full_batch.get("batch_name") or Path(args.batch).stem,
        "application_eids": canary_eids,
    }

    if args.approved_at:
        artifact["approved_at"] = args.approved_at

    if args.approved_by:
        artifact["approved_by"] = args.approved_by

    try:
        write_artifact(artifact, output_path, args.force)
    except FileExistsError as exc:
        print(str(exc))
        return 1

    print(f"CANARY_EIDS_COUNT = {len(canary_eids)}")
    print(f"Wrote canary batch to {output_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
