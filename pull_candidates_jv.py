"""Jobvite -> benivo.candidates synchronization (extraction, filtering, mapping, load)."""

import json
import os
import sys
from typing import Any, Dict, List, Optional

import requests
from dotenv import load_dotenv

import postgres

load_dotenv()

JOBVITE_HEADERS = {
    "x-jvi-api": os.getenv("JOBVITE_X_JVI_API"),
    "x-jvi-sc": os.getenv("JOBVITE_X_JVI_SC"),
    "X-Company-Id": os.getenv("JOBVITE_COMPANY_ID"),
    "Accept": "application/json",
}

JOBVITE_BASE_URL = os.getenv("JOBVITE_BASE_URL", "https://api.jobvite.com")
JOBVITE_CANDIDATE_ENDPOINT = os.getenv("JOBVITE_CANDIDATE_ENDPOINT", "/api/v2/candidate")
JOBVITE_CANDIDATE_URL = JOBVITE_BASE_URL.rstrip("/") + "/" + JOBVITE_CANDIDATE_ENDPOINT.lstrip("/")

WORKFLOW_STATE = "Mobility in process"
REQUEST_TIMEOUT_SECONDS = 30

DEFAULT_PAGE_SIZE = 50
DEFAULT_MAX_PAGES = 20
DEFAULT_PAGINATION_MODE = "start_count"

RELOCATION_TRUE_VALUES = {"yes", "true", "1"}

# Columns sourced from Jobvite application customField entries. Their
# fieldCode is assumed to match the benivo.candidates column name 1:1,
# following the pattern confirmed for "is_relocation_required" in the
# sample payload (test_jsos.json). Not independently verified against the
# live Jobvite custom-field configuration.
CUSTOM_FIELD_COLUMNS = [
    "start_date",
    "population",
    "workplace",
    "host_country",
    "host_city",
    "vip",
    "home_country",
    "home_state_province",
    "home_city",
    "country_of_birth",
    "citizenship",
    "employee_id",
    "billing_entity",
    "host_legal_entity",
    "host_business_unit",
]


def _env_int(name: str, default: int) -> int:
    raw = os.getenv(name)

    if raw is None or raw.strip() == "":
        return default

    try:
        value = int(raw)
    except ValueError:
        return default

    return value if value > 0 else default


def _is_dry_run() -> bool:
    raw = os.getenv("JOBVITE_SYNC_DRY_RUN", "true")
    return raw.strip().lower() not in {"false", "0", "no"}


def _request_candidates_page(params: Dict[str, Any]) -> Dict[str, Any]:
    try:
        response = requests.get(
            JOBVITE_CANDIDATE_URL,
            headers=JOBVITE_HEADERS,
            params=params,
            timeout=REQUEST_TIMEOUT_SECONDS,
        )
    except requests.exceptions.RequestException as exc:
        raise RuntimeError(f"Jobvite request failed: {exc}") from exc

    if response.status_code != 200:
        raise RuntimeError(
            f"Jobvite returned HTTP {response.status_code} for "
            f"start={params.get('start')} count={params.get('count')}: "
            f"{response.text[:500]}"
        )

    return response.json()


def fetch_jobvite_candidates() -> List[Dict[str, Any]]:
    """Fetch all candidates in WORKFLOW_STATE, paginating until exhausted or MAX_PAGES is reached."""
    page_size = _env_int("JOBVITE_PAGE_SIZE", DEFAULT_PAGE_SIZE)
    max_pages = _env_int("JOBVITE_MAX_PAGES", DEFAULT_MAX_PAGES)
    pagination_mode = os.getenv("JOBVITE_PAGINATION_MODE", DEFAULT_PAGINATION_MODE)

    if pagination_mode != DEFAULT_PAGINATION_MODE:
        print(
            f"JOBVITE_PAGINATION_MODE='{pagination_mode}' is not implemented; "
            f"falling back to '{DEFAULT_PAGINATION_MODE}' (start/count)."
        )

    base_params: Dict[str, Any] = {"workflowState": WORKFLOW_STATE}

    start_date = os.getenv("JOBVITE_START_DATE")
    end_date = os.getenv("JOBVITE_END_DATE")

    if start_date:
        base_params["startDate"] = start_date

    if end_date:
        base_params["endDate"] = end_date

    candidates: List[Dict[str, Any]] = []
    start = 0

    for _ in range(max_pages):
        params = dict(base_params, start=start, count=page_size)
        payload = _request_candidates_page(params)

        page_candidates = payload.get("candidates", [])
        candidates.extend(page_candidates)

        total = payload.get("total")
        start += page_size

        if len(page_candidates) < page_size:
            break

        if total is not None and start >= total:
            break
    else:
        print(f"Reached JOBVITE_MAX_PAGES={max_pages}; stopping safely, more records may remain.")

    return candidates


def _get_custom_field_value(custom_fields: Optional[List[Dict[str, Any]]], field_code: str) -> Optional[str]:
    if not custom_fields:
        return None

    target = field_code.strip().lower()

    for field in custom_fields:
        if not isinstance(field, dict):
            continue

        code = str(field.get("fieldCode", "")).strip().lower()

        if code == target:
            value = field.get("value")
            return value if value not in (None, "") else None

    return None


def _is_relocation_required(application: Dict[str, Any]) -> bool:
    value = _get_custom_field_value(application.get("customField"), "is_relocation_required")

    if value is None:
        return False

    return str(value).strip().lower() in RELOCATION_TRUE_VALUES


def map_candidate_to_row(candidate: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Map a raw Jobvite candidate into a benivo.candidates row. Returns None if application_eid is unusable."""
    application = candidate.get("application") or {}
    application_eid = application.get("eId")

    if not application_eid or not str(application_eid).strip():
        return None

    candidate_eid = candidate.get("eId")
    job = application.get("job") or {}
    custom_fields = application.get("customField")

    row: Dict[str, Any] = {
        "application_eid": str(application_eid).strip(),
        "candidate_eid": str(candidate_eid).strip() if candidate_eid else None,
        "email": candidate.get("email") or None,
        "first_name": candidate.get("firstName") or None,
        "last_name": candidate.get("lastName") or None,
        "phone_number": candidate.get("mobile") or candidate.get("homePhone") or candidate.get("workPhone") or None,
        "workflow_state": application.get("workflowState") or None,
        "is_relocation_required": "Yes",
        "job_title": job.get("title") or None,
        "requisition_id": job.get("requisitionId") or None,
        "department": job.get("department") or None,
        "location": candidate.get("location") or None,
        "gender": application.get("gender") or None,
        "source_payload": candidate,
    }

    for column in CUSTOM_FIELD_COLUMNS:
        row[column] = _get_custom_field_value(custom_fields, column)

    row["benivo_status"] = "READY_TO_POST" if row.get("start_date") else "PENDING_MISSING_START_DATE"

    return row


def transform_candidates(raw_candidates: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Filter to relocation candidates only, then map each to a benivo.candidates row."""
    rows: List[Dict[str, Any]] = []

    for candidate in raw_candidates:
        application = candidate.get("application") or {}

        if not _is_relocation_required(application):
            continue

        row = map_candidate_to_row(candidate)

        if row is not None:
            rows.append(row)

    return rows


def _sanitized_sample(rows: List[Dict[str, Any]], limit: int = 3) -> List[Dict[str, Any]]:
    return [
        {
            "application_eid": row["application_eid"],
            "benivo_status": row["benivo_status"],
            "workflow_state": row["workflow_state"],
            "has_start_date": bool(row.get("start_date")),
        }
        for row in rows[:limit]
    ]


def _print_summary(
    fetched: int,
    relocation_count: int,
    ready_count: int,
    pending_count: int,
    inserted: int,
    updated: int,
    skipped: int,
    failed: int,
    dry_run: bool,
) -> None:
    print("\n=== Jobvite -> benivo.candidates Sync Summary ===")
    print(f"Mode: {'DRY RUN' if dry_run else 'SYNC'}")
    print(f"Jobvite records fetched: {fetched}")
    print(f"Relocation records identified: {relocation_count}")
    print(f"READY_TO_POST: {ready_count}")
    print(f"PENDING_MISSING_START_DATE: {pending_count}")
    print(f"Inserted: {inserted}")
    print(f"Updated: {updated}")
    print(f"Skipped: {skipped}")
    print(f"Failed: {failed}")


def main() -> None:
    dry_run = _is_dry_run()

    raw_candidates = fetch_jobvite_candidates()
    rows = transform_candidates(raw_candidates)

    ready_count = sum(1 for row in rows if row["benivo_status"] == "READY_TO_POST")
    pending_count = sum(1 for row in rows if row["benivo_status"] == "PENDING_MISSING_START_DATE")

    inserted = updated = skipped = failed = 0
    exit_code = 0

    if dry_run:
        print("DRY RUN: no database writes will be performed.")
        print(json.dumps(_sanitized_sample(rows), indent=2, ensure_ascii=False))
    else:
        try:
            write_result = postgres.upsert_candidates(rows)
        except RuntimeError as exc:
            print(f"Sync failed: {exc}")
            failed = len(rows)
            exit_code = 1
        else:
            inserted = write_result["inserted"]
            updated = write_result["updated"]
            skipped = write_result["skipped"]
            failed = write_result["failed"]

    _print_summary(
        fetched=len(raw_candidates),
        relocation_count=len(rows),
        ready_count=ready_count,
        pending_count=pending_count,
        inserted=inserted,
        updated=updated,
        skipped=skipped,
        failed=failed,
        dry_run=dry_run,
    )

    sys.exit(exit_code)


if __name__ == "__main__":
    main()
