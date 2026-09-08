#!/usr/bin/env python
"""
READ-ONLY validation: checks whether every candidate currently returned by
the live posting safety gate (candidate_repository.get_ready_candidates())
can build a technically complete Create User payload against LIVE Benivo
office reference data.

Confirmed 2026-09-08: this exists to answer "can the current READY_TO_POST
population actually post?" without going anywhere near a real Create User
or Case PATCH call, without touching the database beyond the same read
already used by the real safety gate, and without generating or delivering
a report (that belongs to reporting_service.py / report_delivery_service.py,
neither of which this script imports).

Allowed Benivo HTTP, and nothing else:
    POST BENIVO_TOKEN_URL   -- benivo_client.get_access_token() (authentication)
    GET  BENIVO_REFDATA_URL -- benivo_client.get_refdata()      (read-only refdata)

Reuses existing, already-tested production components rather than
reimplementing any business rule:
    candidate_repository.get_ready_candidates()      -- same live safety-gated query
    benivo_client.get_access_token() / get_refdata()  -- same two read-only calls
    office_resolution_service.resolve_office()        -- same mapping + live refdata lookup
    posting_service.build_benivo_payload()             -- same payload shape
    posting_service._validate_payload()                 -- same READY/NOT_READY rule
    start_date_service.resolve_effective_start_date()  -- same start date rule

Usage:
    python scripts/validate_ready_candidates_refdata.py

Exit code 0 if every candidate resolved an office AND every payload is
ready; 1 otherwise (with the failing application_eids listed for
troubleshooting). Never raises to a bare traceback for a configuration or
RefData failure -- both are caught and reported, then the script stops
(fail closed, no partial operation, no alternative endpoint attempted).
"""

import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import config  # noqa: E402
from app.clients import benivo_client  # noqa: E402
from app.logging_config import configure_logging  # noqa: E402
from app.repositories.candidate_repository import get_ready_candidates  # noqa: E402
from app.services.office_resolution_service import resolve_office, resolve_office_name  # noqa: E402
from app.services.posting_service import _validate_payload, build_benivo_payload  # noqa: E402
from app.services.start_date_service import resolve_effective_start_date  # noqa: E402
from app.utils.helpers import mask_email  # noqa: E402

# Superset of posting_service._validate_payload()'s own required-field tuple
# -- that function (reused as-is below for the authoritative READY/NOT_READY
# call, so this script never redefines the business rule) checks 6 fields;
# these 8 are shown here purely as a diagnostic breakdown of which of the
# fields you asked about are empty, never as a second, competing rule.
DIAGNOSTIC_PAYLOAD_FIELDS = (
    "firstName", "lastName", "email", "policy",
    "officeId", "officeName", "startDateOfAssignment", "homeCountry",
)

BANNER = """\
================================================================
READ-ONLY REFDATA VALIDATION
Allowed HTTP:
  POST BENIVO_TOKEN_URL   (authentication)
  GET  BENIVO_REFDATA_URL (reference data)
Benivo business writes: DISABLED
Database writes:        DISABLED
Report delivery:        DISABLED
================================================================\
"""


def print_banner() -> None:
    print(BANNER)


def fetch_refdata() -> Dict[str, Any]:
    """
    The only two Benivo HTTP calls this script ever makes. No retry against
    a different endpoint on failure -- the caller stops validation instead.
    """
    access_token = benivo_client.get_access_token()
    return benivo_client.get_refdata(access_token)


def _missing_fields(payload: Dict[str, Any]) -> List[str]:
    return [field for field in DIAGNOSTIC_PAYLOAD_FIELDS if not payload.get(field)]


def evaluate_candidate(
    candidate: Dict[str, Any],
    refdata: Dict[str, Any],
    execution_timestamp: datetime,
) -> Dict[str, Any]:
    """Pure, in-memory. No database write, no Benivo write, of any kind."""
    office = resolve_office(candidate, refdata)
    effective_start_date, _start_date_source = resolve_effective_start_date(
        candidate.get("start_date"), execution_timestamp
    )
    payload = build_benivo_payload(candidate, office, effective_start_date)
    payload_ready = _validate_payload(payload)

    return {
        "application_eid": candidate.get("application_eid"),
        "workplace": candidate.get("workplace"),
        "email_masked": mask_email(candidate.get("email")),
        "office_resolved": office is not None,
        "resolved_office_name": office.get("officeName") if office else None,
        "resolved_office_id": office.get("officeId") if office else None,
        "payload_ready": payload_ready,
        "missing_fields": _missing_fields(payload),
    }


def aggregate_by_workplace(results: List[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    summary: Dict[str, Dict[str, Any]] = {}

    for result in results:
        workplace = result["workplace"]
        entry = summary.setdefault(
            workplace,
            {
                "workplace": workplace,
                "candidate_count": 0,
                "expected_mapped_office_name": resolve_office_name(workplace),
                "resolved_office_name": None,
                "resolved_office_id": None,
                "status": "UNRESOLVED",
            },
        )
        entry["candidate_count"] += 1

        if result["office_resolved"]:
            entry["resolved_office_name"] = result["resolved_office_name"]
            entry["resolved_office_id"] = result["resolved_office_id"]
            entry["status"] = "RESOLVED"

    return summary


def run_validation(limit: Optional[int] = 1000) -> Dict[str, Any]:
    """
    Orchestrates the whole validation. Raises on RefData failure (caller
    decides how to report/stop) -- never falls back to a second endpoint.
    """
    candidates = get_ready_candidates(limit=limit)  # read-only SELECT, same query as the live safety gate
    total = len(candidates)

    empty_summary = {
        "total": total,
        "workplace_summary": {},
        "office_resolved": 0,
        "office_unresolved": 0,
        "payload_ready": 0,
        "payload_not_ready": 0,
        "missing_field_reasons": {},
        "failed_eids": [],
        "results": [],
    }

    if total == 0:
        return empty_summary

    refdata = fetch_refdata()

    execution_timestamp = datetime.now(timezone.utc)
    results = [evaluate_candidate(c, refdata, execution_timestamp) for c in candidates]

    office_resolved = sum(1 for r in results if r["office_resolved"])
    payload_ready = sum(1 for r in results if r["payload_ready"])

    missing_field_reasons: Counter = Counter()
    failed_eids: List[str] = []

    for result in results:
        if not result["payload_ready"]:
            failed_eids.append(result["application_eid"])
            for field in result["missing_fields"]:
                missing_field_reasons[field] += 1

    return {
        "total": total,
        "workplace_summary": aggregate_by_workplace(results),
        "office_resolved": office_resolved,
        "office_unresolved": total - office_resolved,
        "payload_ready": payload_ready,
        "payload_not_ready": total - payload_ready,
        "missing_field_reasons": dict(missing_field_reasons),
        "failed_eids": failed_eids,
        "results": results,
    }


def print_summary(summary: Dict[str, Any]) -> None:
    print(f"\nTOTAL_READY_CANDIDATES = {summary['total']}")

    if summary["total"] == 0:
        print("No READY_TO_POST candidates to validate.")
        return

    print("\nOffice resolution by workplace:")
    print(f"  {'workplace':30s} {'count':>5s}  {'expected_office':28s} {'resolved_office':28s} {'resolved_office_id':38s} status")

    for entry in sorted(
        summary["workplace_summary"].values(), key=lambda e: (-e["candidate_count"], e["workplace"] or "")
    ):
        print(
            f"  {str(entry['workplace']):30s} {entry['candidate_count']:>5d}  "
            f"{str(entry['expected_mapped_office_name']):28s} {str(entry['resolved_office_name']):28s} "
            f"{str(entry['resolved_office_id']):38s} {entry['status']}"
        )

    print(f"\nOFFICE_RESOLVED = {summary['office_resolved']}")
    print(f"OFFICE_UNRESOLVED = {summary['office_unresolved']}")
    print(f"PAYLOAD_READY = {summary['payload_ready']}")
    print(f"PAYLOAD_NOT_READY = {summary['payload_not_ready']}")

    if summary["missing_field_reasons"]:
        print("\nPAYLOAD_NOT_READY reasons (missing field -> candidate count):")
        for field, count in sorted(summary["missing_field_reasons"].items(), key=lambda kv: -kv[1]):
            print(f"  {field}: {count}")

    if summary["failed_eids"]:
        print("\napplication_eids failing validation (troubleshooting only):")
        for eid in summary["failed_eids"]:
            print(f"  {eid}")


def main(argv: Optional[List[str]] = None) -> int:
    configure_logging()
    print_banner()

    try:
        config.validate()
    except RuntimeError as exc:
        print(f"Configuration error -- stopping, no Benivo call made: {exc}")
        return 1

    try:
        summary = run_validation()
    except Exception as exc:
        print(f"RefData retrieval failed -- stopping validation, no alternative endpoint attempted: {exc}")
        return 1

    print_summary(summary)

    if summary["total"] == 0:
        return 0

    return 0 if summary["office_unresolved"] == 0 and summary["payload_not_ready"] == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
