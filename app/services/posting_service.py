"""Candidate selection, Benivo posting orchestration, post_log + candidate status persistence.

BENIVO_DRY_RUN defaults to true, so no real Benivo call happens unless
explicitly overridden. For a controlled one-candidate UAT test,
BENIVO_UAT_APPLICATION_EID pins selection to exactly one explicit, fully
re-validated candidate -- see select_postable_candidates() and
validate_uat_candidate() -- instead of the normal SQL-LIMIT bulk selection.
"""

import logging
from datetime import datetime
from typing import Any, Dict, List, Optional

import psycopg2

from app import config
from app.clients import benivo_client
from app.clients.database_client import transaction
from app.models.domain import ACTION_CREATE_USER, POST_LOG_STATUS_TO_CANDIDATE_STATUS
from app.repositories import candidate_repository, post_log_repository
from app.repositories.candidate_repository import get_candidate_by_application_eid, get_ready_candidates
from app.repositories.post_log_repository import get_terminal_post_log_application_eids, insert_post_log_row
from app.services.office_resolution_service import resolve_office
from app.services.policy_service import resolve_policy_values
from app.utils.helpers import mask_email

logger = logging.getLogger(__name__)


def is_dry_run() -> bool:
    return config.is_dry_run()


def allow_reference_data_calls() -> bool:
    """
    Dry run never calls create_user() (POST) or find_user_by_email() (POST)
    regardless of this flag -- it only gates get_access_token() (auth) and
    get_refdata() (GET, read-only) so office resolution can be validated for
    real without risking a create-user call.
    """
    return config.allow_reference_data_calls()


def _get_max_candidates(default: Optional[int] = None) -> int:
    return default if default is not None else config.max_candidates()


def _get_uat_application_eid() -> Optional[str]:
    return config.uat_application_eid()


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

    office = resolve_office(candidate, refdata) if refdata is not None else None
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
        "email_masked": mask_email(candidate.get("email")),
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
        access_token = benivo_client.get_access_token()
        refdata = benivo_client.get_refdata(access_token)
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

    if isinstance(start_date, datetime):
        return start_date.strftime("%Y-%m-%dT%H:%M:%SZ")

    return str(start_date)


def _sanitize_payload(payload: Dict[str, Any]) -> Dict[str, Any]:
    sanitized = dict(payload)

    if "email" in sanitized:
        sanitized["email"] = mask_email(sanitized["email"])

    return sanitized


def post_single_candidate(access_token: str, candidate: Dict[str, Any], refdata: Dict[str, Any]) -> Dict[str, Any]:
    """One real Benivo attempt for one candidate. Never called during dry run (dry_run gates it out)."""
    office = resolve_office(candidate, refdata)

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
    lookup = benivo_client.find_user_by_email(access_token, email) if email else {"found": False}

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

    create_result = benivo_client.create_user(access_token, payload)

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
        # Not confirmed in any real Benivo response inspected so far.
        "benivo_profile_url": created.get("profileUrl"),
    }


def _post_log_status_from_outcome(outcome: str) -> str:
    return {"success": "SUCCESS", "already_exists": "ALREADY_EXISTS"}.get(outcome, "FAILED")


def _candidate_status_from_post_log_status(post_log_status: str) -> str:
    return POST_LOG_STATUS_TO_CANDIDATE_STATUS.get(post_log_status, "POST_FAILED")


def post_candidates(candidates: List[Dict[str, Any]], dry_run: bool) -> List[Dict[str, Any]]:
    """
    Dry run: builds/returns sanitized preview payloads. Calls no create-user
    or user-lookup API under any configuration. If
    BENIVO_ALLOW_REFERENCE_DATA_CALLS=true, it additionally authenticates and
    fetches real refdata (GET, read-only) so office resolution can be
    validated for real; otherwise office resolution is skipped entirely and
    reported as such.
    Real run: fetches one token/refdata, then posts each candidate for real.
    """
    if dry_run:
        refdata = None
        refdata_note = "refdata not fetched (BENIVO_ALLOW_REFERENCE_DATA_CALLS is not enabled)"

        if allow_reference_data_calls():
            try:
                access_token = benivo_client.get_access_token()
                refdata = benivo_client.get_refdata(access_token)
                refdata_note = "refdata fetched live (BENIVO_ALLOW_REFERENCE_DATA_CALLS=true); no create-user or lookup call made"
            except Exception as exc:
                logger.exception("BENIVO_ALLOW_REFERENCE_DATA_CALLS was set but fetching refdata failed.")
                refdata_note = f"refdata fetch failed: {exc}"

        previews = []

        for candidate in candidates:
            office = resolve_office(candidate, refdata) if refdata is not None else None
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

    access_token = benivo_client.get_access_token()
    refdata = benivo_client.get_refdata(access_token)

    results = []

    for candidate in candidates:
        result = post_single_candidate(access_token, candidate, refdata)
        results.append(result)

    return results


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
    """Writes post_log + updates candidate status atomically, in one transaction."""
    post_log_row = build_post_log_insert(run_id, candidate, result)
    candidate_status = _candidate_status_from_post_log_status(post_log_row["status"])
    application_eid = post_log_row["application_eid"]

    try:
        with transaction() as cur:
            insert_post_log_row(cur, post_log_row)
            candidate_repository.update_candidate_after_posting(
                cur,
                application_eid=application_eid,
                benivo_status=candidate_status,
                benivo_user_id=post_log_row["benivo_user_id"],
                benivo_assignment_id=post_log_row["benivo_assignment_id"],
                benivo_profile_url=post_log_row["benivo_profile_url"],
            )
    except psycopg2.Error:
        logger.exception("Failed to record post result for application_eid=%s.", application_eid)
        raise
