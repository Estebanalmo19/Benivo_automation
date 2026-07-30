"""Benivo posting: candidate selection, API calls, post_log + status persistence.

BENIVO_DRY_RUN defaults to true, so no real Benivo call happens unless
explicitly overridden. For a controlled one-candidate UAT test,
BENIVO_UAT_APPLICATION_EID pins selection to exactly one explicit, fully
re-validated candidate -- see select_postable_candidates() and
validate_uat_candidate() -- instead of the normal SQL-LIMIT bulk selection.
"""

import json
import logging
import os
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

import psycopg2
import psycopg2.extras
import requests
from dotenv import load_dotenv

from postgres import (
    get_candidate_by_application_eid,
    get_ready_candidates,
    get_terminal_post_log_application_eids,
    transaction,
)

load_dotenv()

logger = logging.getLogger(__name__)

if not logging.getLogger().handlers:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

CLIENT_ID = os.getenv("BENIVO_CLIENT_ID")
CLIENT_SECRET = os.getenv("BENIVO_CLIENT_SECRET")
GRANT_TYPE = os.getenv("BENIVO_GRANT_TYPE")

TOKEN_URL = "https://externalapi.uat.benivo.com/idm/v1/Token/OAuth2"
REFDATA_URL = "https://hubapi.uat.benivo.com/v3/api/user/refdata"
CREATE_USER_URL = "https://hubapi.uat.benivo.com/v3/api/user/create"
USER_ASSIGNMENT_URL = "https://hubapi.uat.benivo.com/v3/api/user/userassignment"

REQUEST_TIMEOUT_SECONDS = 30
DEFAULT_MAX_CANDIDATES = 1

# post_log status <-> benivo.candidates status mapping. SUCCESS and
# ALREADY_EXISTS are terminal (never posted again -- see
# postgres.TERMINAL_POST_LOG_STATUSES, which must stay consistent with this).
# FAILED is retryable: it maps to the non-terminal POST_FAILED status, which
# classify_candidates() will freely re-evaluate on the next run.
_POST_LOG_TO_CANDIDATE_STATUS = {
    "SUCCESS": "POSTED",
    "ALREADY_EXISTS": "POSTED",
    "FAILED": "POST_FAILED",
}


def is_dry_run() -> bool:
    raw = os.getenv("BENIVO_DRY_RUN", "true")
    return raw.strip().lower() not in {"false", "0", "no"}


def allow_reference_data_calls() -> bool:
    """
    Dry run never calls create_user() (POST) or find_user_by_email() (POST)
    regardless of this flag -- it only gates get_access_token() (auth) and
    get_refdata() (GET, read-only) so office resolution can be validated for
    real without risking a create-user call.
    """
    raw = os.getenv("BENIVO_ALLOW_REFERENCE_DATA_CALLS", "false")
    return raw.strip().lower() in {"true", "1", "yes"}


def _get_max_candidates(default: int = DEFAULT_MAX_CANDIDATES) -> int:
    raw = os.getenv("BENIVO_MAX_CANDIDATES")

    if raw is None or raw.strip() == "":
        return default

    try:
        value = int(raw)
    except ValueError:
        return default

    return value if value > 0 else default


def _get_uat_application_eid() -> Optional[str]:
    raw = os.getenv("BENIVO_UAT_APPLICATION_EID")
    return raw.strip() if raw and raw.strip() else None


def select_postable_candidates(limit: Optional[int] = None) -> List[Dict[str, Any]]:
    """
    Normal path: READY_TO_POST candidates, oldest first, SQL-limited by
    BENIVO_MAX_CANDIDATES.

    Safety override: if BENIVO_UAT_APPLICATION_EID is set, selection is
    pinned to exactly that application_eid, fully re-validated against every
    eligibility condition (not just LIMIT 1). If it fails any check, this
    returns [] -- no posting happens and it never falls back to another
    candidate. See validate_uat_candidate() for the exact checks.
    """
    uat_application_eid = _get_uat_application_eid()

    if uat_application_eid is not None:
        return _select_explicit_uat_candidate(uat_application_eid)

    max_candidates = limit if limit is not None else _get_max_candidates()
    return get_ready_candidates(limit=max_candidates)


def _validate_payload(payload: Dict[str, Any]) -> bool:
    required_fields = ("firstName", "lastName", "email", "policy", "officeId", "startDateOfAssignment")
    return all(payload.get(field) for field in required_fields)


def validate_uat_candidate(application_eid: str, refdata: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """
    Full explicit-candidate safety gate for the one-candidate UAT. Checks
    every required condition individually (rather than a single WHERE
    clause) so a failure can be reported exactly, and builds the sanitized
    pre-post summary. Never substitutes a different candidate.
    """
    candidate = get_candidate_by_application_eid(application_eid)

    checks: Dict[str, bool] = {"candidate_exists": candidate is not None}

    if candidate is None:
        return {"eligible": False, "checks": checks, "candidate": None, "office": None, "summary": None}

    checks["workflow_state_is_mobility_in_process"] = candidate.get("workflow_state") == "Mobility in process"
    checks["is_relocation_required_is_yes"] = (candidate.get("is_relocation_required") or "").strip().lower() == "yes"
    checks["start_date_present"] = candidate.get("start_date") is not None
    checks["benivo_status_is_ready_to_post"] = candidate.get("benivo_status") == "READY_TO_POST"

    terminal_eids = get_terminal_post_log_application_eids()
    checks["no_terminal_post_log_result"] = application_eid not in terminal_eids

    office = _resolve_office(candidate, refdata) if refdata is not None else None
    checks["office_resolved"] = office is not None

    policy_name, policy_api_value = resolve_policy_values(candidate.get("is_vip"))
    checks["policy_name_is_basic"] = policy_name == "Basic"
    checks["policy_api_value_confirmed"] = policy_api_value is not None

    payload = build_benivo_payload(candidate, office)
    payload_valid = _validate_payload(payload)
    checks["payload_valid"] = payload_valid

    summary = {
        "application_eid": candidate.get("application_eid"),
        "candidate_eid": candidate.get("candidate_eid"),
        "email_masked": _mask_email(candidate.get("email")),
        "workplace": candidate.get("workplace"),
        "resolved_office_name": office.get("officeName") if office else None,
        "resolved_office_id": office.get("officeId") if office else None,
        "start_date": _format_start_date(candidate.get("start_date")),
        "is_vip": candidate.get("is_vip"),
        "policy_name": policy_name,
        "policy_api_value": policy_api_value,
        "payload_valid": payload_valid,
    }

    return {
        "eligible": all(checks.values()),
        "checks": checks,
        "candidate": candidate,
        "office": office,
        "summary": summary,
    }


def _select_explicit_uat_candidate(application_eid: str) -> List[Dict[str, Any]]:
    refdata = None

    try:
        access_token = get_access_token()
        refdata = get_refdata(access_token)
    except Exception:
        logger.exception(
            "BENIVO_UAT_APPLICATION_EID=%s is set but fetching refdata for office validation failed.",
            application_eid,
        )

    validation = validate_uat_candidate(application_eid, refdata)

    if validation["summary"] is not None:
        logger.info("UAT pre-post validation summary: %s", validation["summary"])

    if not validation["eligible"]:
        failed_checks = [name for name, passed in validation["checks"].items() if not passed]
        logger.error(
            "UAT candidate application_eid=%s is NOT eligible for posting -- stopping, no fallback. "
            "Failed checks: %s. Full checks: %s",
            application_eid,
            failed_checks,
            validation["checks"],
        )
        return []

    logger.info("UAT candidate application_eid=%s passed all eligibility and pre-post checks.", application_eid)
    return [validation["candidate"]]


def _mask_email(email: Optional[str]) -> Optional[str]:
    if not email or "@" not in email:
        return email

    local, domain = email.split("@", 1)
    masked_local = (local[0] + "***") if local else "***"
    return f"{masked_local}@{domain}"


def resolve_policy(is_vip: Optional[bool]) -> str:
    """
    The one centralized policy-resolution rule:
      is_vip IS TRUE       -> 'VIP'
      is_vip IS FALSE/NULL -> 'Basic'

    Deliberately does not consider job_title, department, salary, office, or
    the old unconfirmed candidates.vip text field -- is_vip is the only
    input. VIP has no confirmed Jobvite source yet (see sync_candidates.py),
    so is_vip is business/integration-owned and defaults to FALSE.
    """
    return "VIP" if is_vip is True else "Basic"


# policy_name (business label) and policy_api_value (exact string Benivo's
# API accepts) are deliberately separate concepts. The first real UAT
# attempt (application_eid=pCu0IxwQ, 2026-07-29) sent policy="Basic" and
# Benivo rejected it: "Policy is misspelled". Confirmed via two independent
# sources that "Tier 1" is the real API value for the general/non-VIP case:
#   1. Live refdata['policies'] (read-only, no create-user call) returns
#      exactly: "Tier 1", "Tier 2", "Tier 3", "Game Presenters and
#      Shufflers" -- "Basic"/"VIP" appear nowhere in it.
#   2. create_user_benivo.py's original PREFERRED_POLICY was already
#      "Tier 1", and a historical request using it was accepted by policy
#      validation (it failed later, on LastName format -- not on policy).
#
# No confirmed Benivo API value exists for VIP anywhere (not in refdata, not
# in any historical payload) -- deliberately left unmapped so VIP candidates
# are blocked from posting (via _validate_payload) rather than guessed.
POLICY_NAME_TO_API_VALUE = {
    "Basic": "Tier 1",
}


def resolve_policy_api_value(policy_name: str) -> Optional[str]:
    """Exact Benivo API value for a business policy_name, or None if unconfirmed (blocks posting)."""
    return POLICY_NAME_TO_API_VALUE.get(policy_name)


def resolve_policy_values(is_vip: Optional[bool]) -> Tuple[str, Optional[str]]:
    """(policy_name, policy_api_value) -- policy_name is never overwritten by the API-specific label."""
    policy_name = resolve_policy(is_vip)
    return policy_name, resolve_policy_api_value(policy_name)


# Explicit, centrally maintained Jobvite workplace -> Benivo officeName
# mapping. Keys are pre-normalized (see _normalize_for_lookup): lowercased,
# single-spaced. Values are exact Benivo officeName strings, confirmed live
# against Benivo UAT refdata on 2026-07-29 (13 offices returned; see
# delivery notes for the full list).
#
# officeId is intentionally NEVER stored here. It must always be looked up
# from the current environment's live refdata response at resolution time --
# UAT and production office UUIDs are expected to differ, so no UUID is
# hardcoded anywhere in this mapping or this module.
WORKPLACE_TO_OFFICE_NAME = {
    "rak live casino": "UAE (Live Casino)",
    "serbia live casino": "Serbia (Live Casino)",
    "colombia live casino": "Colombia (Live Casino)",
    "bulgaria live casino": "Bulgaria (Live Casino)",
    "malta": "Malta (Global)",
}


def _normalize_for_lookup(value: Optional[str]) -> str:
    """Whitespace/casing normalization for lookup only -- never stored or sent to Benivo."""
    if not value:
        return ""
    return " ".join(value.strip().split()).lower()


def resolve_office_name(workplace: Optional[str]) -> Optional[str]:
    """Explicit mapping only, no fuzzy/partial matching. Returns the Benivo officeName to look up, or None."""
    return WORKPLACE_TO_OFFICE_NAME.get(_normalize_for_lookup(workplace))


def _resolve_office(candidate: Dict[str, Any], refdata: Optional[Dict[str, Any]]) -> Optional[Dict[str, str]]:
    """
    1. Read candidate.workplace.
    2/3. Normalize + translate via the explicit WORKPLACE_TO_OFFICE_NAME mapping.
    4/5. Find that exact officeName in live refdata, retrieve the real officeId.
    Never guesses, never generates or infers an officeId, no fuzzy matching.
    """
    if refdata is None:
        return None

    workplace = candidate.get("workplace")
    office_name = resolve_office_name(workplace)

    if office_name is None:
        logger.info("Unresolved workplace: %r has no entry in WORKPLACE_TO_OFFICE_NAME.", workplace)
        return None

    offices = refdata.get("offices")

    if not isinstance(offices, list):
        return None

    for office in offices:
        if isinstance(office, dict) and office.get("officeName") == office_name:
            return {"officeId": office.get("id"), "officeName": office.get("officeName", "")}

    logger.info(
        "Unresolved office: workplace=%r translated to expected Benivo officeName=%r, "
        "but no matching office exists in the current environment's reference data.",
        workplace,
        office_name,
    )
    return None


def build_benivo_payload(candidate: Dict[str, Any], office: Optional[Dict[str, str]]) -> Dict[str, Any]:
    """
    "policy" sends policy_api_value (the exact string Benivo's API accepts,
    e.g. "Tier 1"), never policy_name (the business label "Basic"/"VIP").
    If policy_api_value is unconfirmed (currently: any VIP candidate), this
    is None, which _validate_payload() correctly treats as invalid --
    blocking the create-user call rather than sending a guessed value.
    """
    _, policy_api_value = resolve_policy_values(candidate.get("is_vip"))

    return {
        "firstName": candidate.get("first_name"),
        "lastName": candidate.get("last_name"),
        "email": candidate.get("email"),
        "policy": policy_api_value,
        "officeId": office["officeId"] if office else None,
        "officeName": office["officeName"] if office else None,
        "startDateOfAssignment": _format_start_date(candidate.get("start_date")),
    }


def _format_start_date(start_date: Any) -> Optional[str]:
    if start_date is None:
        return None

    if isinstance(start_date, (datetime,)):
        return start_date.strftime("%Y-%m-%dT%H:%M:%SZ")

    return str(start_date)


def _sanitize_payload(payload: Dict[str, Any]) -> Dict[str, Any]:
    sanitized = dict(payload)

    if "email" in sanitized:
        sanitized["email"] = _mask_email(sanitized["email"])

    return sanitized


# ---------------------------------------------------------------------------
# Benivo API calls -- generalized from create_user_benivo.py's proven flow.
# create_user_benivo.py itself is left untouched (kept as a standalone
# single-candidate test script), not imported from here.
# ---------------------------------------------------------------------------

def get_access_token() -> str:
    headers = {
        "Accept": "application/json",
        "Content-Type": "application/x-www-form-urlencoded",
        "User-Agent": "PostmanRuntime/7.43.0",
    }
    data = {"grant_type": GRANT_TYPE, "client_id": CLIENT_ID, "client_secret": CLIENT_SECRET}

    response = requests.post(TOKEN_URL, headers=headers, data=data, timeout=REQUEST_TIMEOUT_SECONDS)

    if response.status_code != 200:
        raise RuntimeError(f"Failed to obtain Benivo token: {response.status_code} - {response.text[:500]}")

    access_token = response.json().get("access_token")

    if not access_token:
        raise RuntimeError("Benivo token response did not include access_token.")

    return access_token


def _get_headers(access_token: str) -> Dict[str, str]:
    return {
        "Authorization": f"Bearer {access_token}",
        "Accept": "application/json",
        "Content-Type": "application/json",
        "User-Agent": "PostmanRuntime/7.43.0",
    }


def get_refdata(access_token: str) -> Dict[str, Any]:
    response = requests.get(REFDATA_URL, headers=_get_headers(access_token), timeout=REQUEST_TIMEOUT_SECONDS)

    if response.status_code != 200:
        raise RuntimeError(f"Failed to fetch Benivo refdata: {response.status_code} - {response.text[:500]}")

    payload = response.json()

    if payload.get("hasError") is True:
        raise RuntimeError(f"Benivo refdata returned an error: {payload}")

    return payload.get("data", {})


def find_user_by_email(access_token: str, email: str) -> Dict[str, Any]:
    response = requests.post(
        USER_ASSIGNMENT_URL,
        headers=_get_headers(access_token),
        json={"email": email},
        timeout=REQUEST_TIMEOUT_SECONDS,
    )

    body = response.json() if response.content else {}
    found = False
    benivo_user_id = None
    benivo_assignment_id = None

    if response.status_code == 200 and body.get("hasError") is not True:
        data = body.get("data") or {}
        user = data.get("user") if isinstance(data, dict) else None

        if isinstance(user, dict) and (user.get("email") or "").lower() == email.lower():
            found = True
            benivo_user_id = user.get("benivoId") or user.get("benivoID") or user.get("BenivoID")

        assignments = data.get("assignments") if isinstance(data, dict) else None

        if isinstance(assignments, list) and assignments:
            first_assignment = assignments[0]

            if isinstance(first_assignment, dict):
                benivo_assignment_id = first_assignment.get("assignmentId") or first_assignment.get("assignmentID")

    return {
        "found": found,
        "status_code": response.status_code,
        "benivo_user_id": benivo_user_id,
        "benivo_assignment_id": benivo_assignment_id,
        "raw_response": body,
    }


def create_user(access_token: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    response = requests.post(
        CREATE_USER_URL,
        headers=_get_headers(access_token),
        json=[payload],
        timeout=REQUEST_TIMEOUT_SECONDS,
    )

    body = response.json() if response.content else {}

    result = {"success": False, "status_code": response.status_code, "created": None, "raw_response": body, "error": None}

    if response.status_code != 200 or body.get("hasError") is True:
        result["error"] = json.dumps(body, ensure_ascii=False)
        return result

    created_rows = body.get("data")

    if not isinstance(created_rows, list) or not created_rows:
        result["error"] = "Create response did not include data."
        return result

    created = created_rows[0]
    missing = [field for field in ("benivoId", "assignmentId", "email") if field not in created]

    if missing:
        result["error"] = f"Create response missing fields: {missing}"
        return result

    result["success"] = True
    result["created"] = created
    return result


def post_single_candidate(access_token: str, candidate: Dict[str, Any], refdata: Dict[str, Any]) -> Dict[str, Any]:
    """One real Benivo attempt for one candidate. Never called during this phase (dry_run gates it out)."""
    office = _resolve_office(candidate, refdata)

    if office is None:
        return {
            "outcome": "failed",
            "request_payload": None,
            "response_payload": None,
            "error_message": (
                f"Could not resolve Benivo officeId for workplace={candidate.get('workplace')!r}, "
                f"host_city={candidate.get('host_city')!r}, host_country={candidate.get('host_country')!r}."
            ),
            "benivo_user_id": None,
            "benivo_assignment_id": None,
            "benivo_profile_url": None,
        }

    payload = build_benivo_payload(candidate, office)

    if not _validate_payload(payload):
        return {
            "outcome": "failed",
            "request_payload": payload,
            "response_payload": None,
            "error_message": (
                f"Payload invalid before sending to Benivo -- likely no confirmed policy_api_value for "
                f"is_vip={candidate.get('is_vip')!r}. Not attempting create-user with an unconfirmed/guessed value."
            ),
            "benivo_user_id": None,
            "benivo_assignment_id": None,
            "benivo_profile_url": None,
        }

    email = candidate.get("email")
    lookup = find_user_by_email(access_token, email) if email else {"found": False}

    if lookup.get("found"):
        return {
            "outcome": "already_exists",
            "request_payload": payload,
            "response_payload": lookup.get("raw_response"),
            "error_message": None,
            "benivo_user_id": lookup.get("benivo_user_id"),
            "benivo_assignment_id": lookup.get("benivo_assignment_id"),
            "benivo_profile_url": None,
        }

    create_result = create_user(access_token, payload)

    if not create_result["success"]:
        return {
            "outcome": "failed",
            "request_payload": payload,
            "response_payload": create_result.get("raw_response"),
            "error_message": create_result.get("error"),
            "benivo_user_id": None,
            "benivo_assignment_id": None,
            "benivo_profile_url": None,
        }

    created = create_result["created"]

    return {
        "outcome": "success",
        "request_payload": payload,
        "response_payload": create_result.get("raw_response"),
        "error_message": None,
        "benivo_user_id": created.get("benivoId"),
        "benivo_assignment_id": created.get("assignmentId"),
        # Not confirmed in any real Benivo response inspected so far -- see
        # delivery notes. Left as None until a live response is captured.
        "benivo_profile_url": created.get("profileUrl"),
    }


def _post_log_status_from_outcome(outcome: str) -> str:
    return {"success": "SUCCESS", "already_exists": "ALREADY_EXISTS"}.get(outcome, "FAILED")


def _candidate_status_from_post_log_status(post_log_status: str) -> str:
    return _POST_LOG_TO_CANDIDATE_STATUS.get(post_log_status, "POST_FAILED")


def post_candidates(candidates: List[Dict[str, Any]], dry_run: bool) -> List[Dict[str, Any]]:
    """
    Dry run: builds/returns sanitized preview payloads. Calls no create-user
    or user-lookup API under any configuration. If
    BENIVO_ALLOW_REFERENCE_DATA_CALLS=true, it additionally authenticates and
    fetches real refdata (GET, read-only) so office resolution can be
    validated for real; otherwise office resolution is skipped entirely and
    reported as such.
    Real run (not exercised this phase): fetches one token/refdata, then
    posts each candidate for real.
    """
    if dry_run:
        refdata = None
        refdata_note = "refdata not fetched (BENIVO_ALLOW_REFERENCE_DATA_CALLS is not enabled)"

        if allow_reference_data_calls():
            try:
                access_token = get_access_token()
                refdata = get_refdata(access_token)
                refdata_note = "refdata fetched live (BENIVO_ALLOW_REFERENCE_DATA_CALLS=true); no create-user or lookup call made"
            except Exception as exc:
                logger.exception("BENIVO_ALLOW_REFERENCE_DATA_CALLS was set but fetching refdata failed.")
                refdata_note = f"refdata fetch failed: {exc}"

        previews = []

        for candidate in candidates:
            office = _resolve_office(candidate, refdata) if refdata is not None else None
            payload = build_benivo_payload(candidate, office)
            policy_name, policy_api_value = resolve_policy_values(candidate.get("is_vip"))

            previews.append(
                {
                    "application_eid": candidate.get("application_eid"),
                    "office_resolved": office is not None,
                    "office_resolution_note": refdata_note,
                    "is_vip": candidate.get("is_vip"),
                    "policy_name": policy_name,
                    "policy_api_value": policy_api_value,
                    "payload": _sanitize_payload(payload),
                }
            )

        return previews

    access_token = get_access_token()
    refdata = get_refdata(access_token)

    results = []

    for candidate in candidates:
        result = post_single_candidate(access_token, candidate, refdata)
        results.append(result)

    return results


ACTION_CREATE_USER = "CREATE_USER"


def build_post_log_insert(run_id: str, candidate: Dict[str, Any], result: Dict[str, Any]) -> Dict[str, Any]:
    """Pure: maps a candidate + posting result to the exact benivo.post_log column values. No I/O."""
    post_log_status = _post_log_status_from_outcome(result["outcome"])
    is_vip = candidate.get("is_vip")
    policy_name, policy_api_value = resolve_policy_values(is_vip)

    return {
        "run_id": run_id,
        "application_eid": candidate["application_eid"],
        "candidate_eid": candidate.get("candidate_eid"),
        "email": candidate.get("email"),
        "action": ACTION_CREATE_USER,
        "status": post_log_status,
        "is_vip": is_vip,
        "policy_name": policy_name,
        "policy_api_value": policy_api_value,
        "benivo_user_id": result["benivo_user_id"],
        "benivo_assignment_id": result["benivo_assignment_id"],
        "benivo_profile_url": result["benivo_profile_url"],
        "request_payload": result["request_payload"],
        "response_payload": result["response_payload"],
        "error_message": result["error_message"],
    }


def record_post_result(candidate: Dict[str, Any], result: Dict[str, Any], run_id: str) -> None:
    """Writes post_log + updates candidate status atomically, in one transaction. Never called during this phase."""
    post_log_row = build_post_log_insert(run_id, candidate, result)
    candidate_status = _candidate_status_from_post_log_status(post_log_row["status"])
    application_eid = post_log_row["application_eid"]

    try:
        with transaction() as cur:
            cur.execute(
                """
                INSERT INTO benivo.post_log (
                    run_id, application_eid, candidate_eid, email, action, status,
                    is_vip, policy_name, policy_api_value,
                    benivo_user_id, benivo_assignment_id, benivo_profile_url,
                    request_payload, response_payload, error_message, posted_at
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, NOW())
                """,
                (
                    post_log_row["run_id"],
                    post_log_row["application_eid"],
                    post_log_row["candidate_eid"],
                    post_log_row["email"],
                    post_log_row["action"],
                    post_log_row["status"],
                    post_log_row["is_vip"],
                    post_log_row["policy_name"],
                    post_log_row["policy_api_value"],
                    post_log_row["benivo_user_id"],
                    post_log_row["benivo_assignment_id"],
                    post_log_row["benivo_profile_url"],
                    psycopg2.extras.Json(post_log_row["request_payload"]) if post_log_row["request_payload"] is not None else None,
                    psycopg2.extras.Json(post_log_row["response_payload"]) if post_log_row["response_payload"] is not None else None,
                    post_log_row["error_message"],
                ),
            )

            cur.execute(
                """
                UPDATE benivo.candidates
                SET benivo_status = %s,
                    benivo_user_id = %s,
                    benivo_assignment_id = %s,
                    benivo_profile_url = %s,
                    updated_at = NOW()
                WHERE application_eid = %s
                """,
                (
                    candidate_status,
                    post_log_row["benivo_user_id"],
                    post_log_row["benivo_assignment_id"],
                    post_log_row["benivo_profile_url"],
                    application_eid,
                ),
            )
    except psycopg2.Error:
        logger.exception("Failed to record post result for application_eid=%s.", application_eid)
        raise
