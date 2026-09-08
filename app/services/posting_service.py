"""Candidate selection, Benivo posting orchestration, post_log + candidate status persistence.

BENIVO_DRY_RUN defaults to true, so no real Benivo call happens unless
explicitly overridden. For a controlled one-candidate UAT test,
BENIVO_UAT_APPLICATION_EID pins selection to exactly one explicit, fully
re-validated candidate -- see select_postable_candidates() and
validate_uat_candidate() -- instead of the normal SQL-LIMIT bulk selection.
"""

import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import psycopg2

from app import config
from app.clients import benivo_client
from app.clients.database_client import transaction
from app.models.domain import (
    ACTION_CREATE_USER,
    ACTION_UPDATE_CASE,
    MOBILITY_WORKFLOW_STATE,
    POST_LOG_STATUS_TO_CANDIDATE_STATUS,
)
from app.repositories import candidate_repository, post_log_repository
from app.repositories.candidate_repository import (
    get_candidate_by_application_eid,
    get_ready_candidates,
    get_source_workflow_state,
)
from app.repositories.post_log_repository import get_terminal_post_log_application_eids, insert_post_log_row
from app.services.country_code_service import resolve_iso2 as resolve_country_iso2
from app.services.home_country_service import resolve_effective_home_country
from app.services.office_resolution_service import resolve_office
from app.services.population_service import resolve_population_values
from app.services.start_date_service import resolve_effective_start_date
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


def validate_uat_candidate(
    application_eid: str,
    refdata: Optional[Dict[str, Any]],
    execution_timestamp: Optional[datetime] = None,
) -> Dict[str, Any]:
    """
    Full explicit-candidate safety gate for the one-candidate UAT. Checks
    every required condition individually (rather than a single WHERE
    clause) so a failure can be reported exactly, and builds the sanitized
    pre-post summary. Never substitutes a different candidate.

    execution_timestamp defaults to "now" -- this validates exactly one
    candidate, so a single internal generation here does not violate the
    "one execution_timestamp per run, reused for every candidate" rule
    (there is only one candidate in this path). Callers driving a batch
    (post_candidates()) generate their own single timestamp and never go
    through this function.
    """
    if execution_timestamp is None:
        execution_timestamp = datetime.now(timezone.utc)

    candidate = get_candidate_by_application_eid(application_eid)

    checks: Dict[str, bool] = {"candidate_exists": candidate is not None}

    if candidate is None:
        return {"eligible": False, "checks": checks, "candidate": None, "office": None, "summary": None}

    checks["workflow_state_is_mobility_in_process"] = candidate.get("workflow_state") == MOBILITY_WORKFLOW_STATE
    # FINAL POSTING SAFETY GATE, live re-check (confirmed 2026-09-08,
    # defense in depth): the check above trusts the CACHED benivo.candidates
    # .workflow_state, which this path (unlike the bulk get_ready_candidates()
    # selection) does not otherwise re-verify against the authoritative
    # source at all. A single-candidate UAT override that skips the bulk
    # query's own EXISTS clause needs the exact same live re-verification,
    # or it would remain exactly as vulnerable as the bug this session fixed
    # (application_eid=pP98MxwU) -- a stale cache alone is never sufficient.
    checks["source_still_mobility_in_process"] = get_source_workflow_state(application_eid) == MOBILITY_WORKFLOW_STATE
    checks["is_relocation_required_is_yes"] = (candidate.get("is_relocation_required") or "").strip().lower() == "yes"

    effective_start_date, start_date_source = resolve_effective_start_date(candidate.get("start_date"), execution_timestamp)
    checks["effective_start_date_resolved"] = effective_start_date is not None

    checks["benivo_status_is_ready_to_post"] = candidate.get("benivo_status") == "READY_TO_POST"

    terminal_eids = get_terminal_post_log_application_eids()
    checks["no_terminal_post_log_result"] = application_eid not in terminal_eids

    office = resolve_office(candidate, refdata) if refdata is not None else None
    checks["office_resolved"] = office is not None

    population_name, population_api_value = resolve_population_values(
        candidate.get("dealer_shuffler"), candidate.get("is_vip")
    )
    checks["population_api_value_confirmed"] = population_api_value is not None

    payload = build_benivo_payload(candidate, office, effective_start_date)
    payload_valid = _validate_payload(payload)
    checks["payload_valid"] = payload_valid

    summary = {
        "application_eid": candidate.get("application_eid"),
        "candidate_eid": candidate.get("candidate_eid"),
        "email_masked": mask_email(candidate.get("email")),
        "workplace": candidate.get("workplace"),
        "resolved_office_name": office.get("officeName") if office else None,
        "resolved_office_id": office.get("officeId") if office else None,
        "start_date": _format_start_date(effective_start_date),
        "start_date_source": start_date_source,
        "execution_date": execution_timestamp.isoformat(),
        "is_vip": candidate.get("is_vip"),
        "dealer_shuffler": candidate.get("dealer_shuffler"),
        "population_name": population_name,
        "population_api_value": population_api_value,
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


def build_benivo_payload(
    candidate: Dict[str, Any],
    office: Optional[Dict[str, str]],
    effective_start_date: Any,
) -> Dict[str, Any]:
    """
    "policy" sends population_api_value (see population_service.py).
    CONFIRMED 2026-09-04 by Mobility: real UAT evidence shows the Create
    User "policy" value is exactly what appears as Population in the
    Benivo UI -- so this is no longer the circumstantial inference it was
    before (matching refdata['policies'] value sets + a live UAT success
    sending "Tier 1" through it on 2026-07-30 was already strong evidence;
    it is now a confirmed fact, not merely the best available guess).
    "policy" carries Population only -- see below for why VIP Status is
    deliberately absent from this payload.

    Separately, and NOT implemented anywhere in this payload: no Benivo API
    field or endpoint for a standalone "VIP Status" has ever been confirmed
    (refdata exposes no such key; the create-user and Case PATCH field lists
    Gina has confirmed contain none). mobility_vip/is_vip therefore is NOT
    sent to Benivo under any field name here -- it remains an internal/
    reporting value only (see reporting_service.py's "Mobility VIP" column)
    and one input to resolve_population_values() above. If Benivo requires
    VIP Status to be sent as its own field, that field name/endpoint must be
    confirmed before it can be added.

    If population_api_value is somehow unconfirmed (not reachable today --
    resolve_population_values() always returns one of the three known
    values), this is None, which _validate_payload() correctly treats as
    invalid -- blocking the create-user call rather than sending a guessed
    value.

    effective_start_date is the already-resolved date (Jobvite-sourced or
    calculated -- see start_date_service.resolve_effective_start_date()),
    passed in rather than re-derived here, so this stays a pure formatter
    and every caller controls exactly which execution_timestamp produced it.

    Job Title: deliberately NOT included in create-user. Investigated and
    confirmed -- no Benivo API field name for job title exists in this
    payload (not in refdata, not in any captured create-user request/
    response, not in legacy/create_user_benivo.py). Confirmed 2026-08-10 by
    Gina (Benivo): Job Title maps to hostJobRole, but that field belongs to
    the Case PATCH payload, not create-user -- see
    build_case_update_payload(). Sending a guessed create-user key risks
    the same kind of silent rejection "policy": "Basic" caused before
    "Tier 1" was confirmed.

    homeCountry: confirmed 2026-08-10 by Gina -- create-user must populate
    it from the candidate's effective home country. Sent as-is with no
    required-field validation: unlike officeId/policy/etc., a missing
    value doesn't block posting, it's just sent as None.

    Uses home_country_service.resolve_effective_home_country() (primary:
    candidates.home_country, synced from Jobvite's candidate_home_country
    custom field; fallback: candidates.current_country, synced from
    Jobvite's own countryName field) rather than candidate.get("home_country")
    directly -- confirmed 2026-08-10 during the Country Data Issues
    investigation that candidate_home_country alone leaves real gaps
    current_country reliably fills. See build_case_update_payload(), which
    uses the exact same resolution for homeLocation.country.
    """
    _, population_api_value = resolve_population_values(candidate.get("dealer_shuffler"), candidate.get("is_vip"))
    effective_home_country, _home_country_source = resolve_effective_home_country(candidate)

    return {
        "firstName": candidate.get("first_name"),
        "lastName": candidate.get("last_name"),
        "email": candidate.get("email"),
        "policy": population_api_value,
        "officeId": office["officeId"] if office else None,
        "officeName": office["officeName"] if office else None,
        "startDateOfAssignment": _format_start_date(effective_start_date),
        "homeCountry": effective_home_country,
    }


def build_case_update_payload(candidate: Dict[str, Any], case_id: Any) -> Dict[str, Any]:
    """
    PATCH /clients/v1/Case payload. Confirmed 2026-08-10 by Gina (Benivo):
      - assignmentId returned by create-user IS the caseId this endpoint
        requires (case_id is passed in by the caller -- see
        post_single_candidate(), which uses create_result["created"]["assignmentId"]).
      - Job Title -> hostJobRole.
      - effective home country -> homeLocation.country.

    Contract confirmed 2026-08-19 by Gina (Benivo) after a real UAT PATCH
    returned error 999: the endpoint requires a findBy/data envelope, not a
    flat body -- caseId identifies the case under "findBy", and the fields
    being updated go under "data". Sending a flat {"caseId": ..., ...} body
    is what caused the 999.

    Contract confirmed 2026-08-21 by a real Benivo UAT PATCH: unlike
    create-user's homeCountry, homeLocation.country here must be a 2-character
    ISO 3166-1 alpha-2 code, not the full country name (a full name causes
    error 4422). See country_code_service.resolve_iso2() -- used ONLY here,
    never for create-user's homeCountry (build_benivo_payload()), since
    there is no evidence create-user needs the same conversion. Fails
    safely: an unresolvable country name becomes None here (never a guessed
    code), the same way officeId/policy fail safely elsewhere in this file.

    Only these two data fields are sent (hostJobRole, homeLocation.country).
    No other Case field has been confirmed -- guessing one risks the same
    silent-rejection failure mode that "policy": "Basic" caused before
    "Tier 1" was confirmed. Values are sent as-is (including None when
    nothing resolves) -- this call is best-effort and never blocks or
    reverses the create-user result, see post_single_candidate().

    The underlying country resolution (home_country_service.
    resolve_effective_home_country()) is the exact same one
    build_benivo_payload() uses for homeCountry -- so both payloads for the
    same candidate always agree on WHICH country, even though this one goes
    on to convert it to an ISO code and the other doesn't.
    """
    effective_home_country, _home_country_source = resolve_effective_home_country(candidate)
    home_country_iso2 = resolve_country_iso2(effective_home_country)

    return {
        "findBy": {"caseId": case_id},
        "data": {
            "hostJobRole": candidate.get("job_title"),
            "homeLocation": {"country": home_country_iso2},
        },
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


def post_single_candidate(
    access_token: str,
    candidate: Dict[str, Any],
    refdata: Dict[str, Any],
    execution_timestamp: datetime,
) -> Dict[str, Any]:
    """
    One real Benivo attempt for one candidate: create-user, then (only on a
    successful create-user) an immediate follow-up Case PATCH -- see
    build_case_update_payload(). Never called during dry run (dry_run gates
    it out).

    execution_timestamp must be the single value generated once by the
    calling run (see post_candidates()) -- never datetime.now() per call.
    """
    office = resolve_office(candidate, refdata)

    effective_start_date, start_date_source = resolve_effective_start_date(candidate.get("start_date"), execution_timestamp)
    audit_fields = {
        "execution_date": execution_timestamp,
        "effective_start_date": effective_start_date,
        "start_date_source": start_date_source,
    }

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
            **audit_fields,
        }

    payload = build_benivo_payload(candidate, office, effective_start_date)

    if not _validate_payload(payload):
        return {
            "outcome": "failed",
            "request_payload": payload,
            "response_payload": None,
            "error_message": (
                f"Payload invalid before sending to Benivo -- likely no confirmed population_api_value for "
                f"dealer_shuffler={candidate.get('dealer_shuffler')!r}, is_vip={candidate.get('is_vip')!r}. "
                f"Not attempting create-user with an unconfirmed/guessed value."
            ),
            "benivo_user_id": None,
            "benivo_assignment_id": None,
            "benivo_profile_url": None,
            **audit_fields,
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
            **audit_fields,
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
            "status_code": create_result.get("status_code"),
            **audit_fields,
        }

    created = create_result["created"]
    case_id = created.get("assignmentId")

    # Immediately follow a successful create-user with the Case PATCH --
    # confirmed 2026-08-10 by Gina (Benivo) that assignmentId IS the
    # required caseId. Best-effort: a PATCH failure is recorded (see
    # build_case_update_post_log_insert()/record_post_result()) but never
    # changes the create-user outcome or the candidate's POSTED status.
    case_payload = build_case_update_payload(candidate, case_id)
    case_patch_result = benivo_client.update_case(access_token, case_payload)

    if not case_patch_result["success"]:
        logger.error(
            "Case PATCH failed for application_eid=%s (caseId=%s, http_status_code=%s): %s -- "
            "create-user already succeeded, candidate status is unaffected.",
            candidate.get("application_eid"),
            case_id,
            case_patch_result.get("status_code"),
            case_patch_result.get("error"),
        )

    case_update = {
        "attempted": True,
        "success": case_patch_result["success"],
        "case_id": case_id,
        "status_code": case_patch_result.get("status_code"),
        "request_payload": case_payload,
        "response_payload": case_patch_result.get("raw_response"),
        "error_message": case_patch_result.get("error"),
    }

    return {
        "outcome": "success",
        "request_payload": payload,
        "response_payload": create_result.get("raw_response"),
        "error_message": None,
        "benivo_user_id": created.get("benivoId"),
        "benivo_assignment_id": case_id,
        # Not confirmed in any real Benivo response inspected so far.
        "benivo_profile_url": created.get("profileUrl"),
        "status_code": create_result.get("status_code"),
        "case_update": case_update,
        **audit_fields,
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

    execution_timestamp is generated exactly once here and reused for every
    candidate in `candidates` -- see start_date_service.resolve_effective_start_date().
    """
    # Generated once for the whole run and reused for every candidate below
    # -- never call datetime.now() per-candidate.
    execution_timestamp = datetime.now(timezone.utc)

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
            effective_start_date, start_date_source = resolve_effective_start_date(
                candidate.get("start_date"), execution_timestamp
            )
            payload = build_benivo_payload(candidate, office, effective_start_date)
            population_name, population_api_value = resolve_population_values(
                candidate.get("dealer_shuffler"), candidate.get("is_vip")
            )

            # caseId is only known once create-user actually succeeds (it's
            # the returned assignmentId), so the preview shows the Case
            # PATCH payload shape with caseId=None -- everything else
            # (hostJobRole, homeLocation.country) is exactly what would be
            # sent for real.
            case_update_payload_preview = build_case_update_payload(candidate, case_id=None)

            previews.append(
                {
                    "application_eid": candidate.get("application_eid"),
                    "office_resolved": office is not None,
                    "office_resolution_note": refdata_note,
                    "is_vip": candidate.get("is_vip"),
                    "dealer_shuffler": candidate.get("dealer_shuffler"),
                    "population_name": population_name,
                    "population_api_value": population_api_value,
                    "start_date_source": start_date_source,
                    "effective_start_date": str(effective_start_date) if effective_start_date else None,
                    "execution_date": execution_timestamp.isoformat(),
                    "payload": _sanitize_payload(payload),
                    "case_update_payload_preview": case_update_payload_preview,
                }
            )

        return previews

    access_token = benivo_client.get_access_token()
    refdata = benivo_client.get_refdata(access_token)

    results = []

    for candidate in candidates:
        result = post_single_candidate(access_token, candidate, refdata, execution_timestamp)
        results.append(result)

    return results


def build_post_log_insert(run_id: str, candidate: Dict[str, Any], result: Dict[str, Any]) -> Dict[str, Any]:
    """
    Pure: maps a candidate + posting result to the exact benivo.post_log
    column values. No I/O.

    is_vip/policy_name/policy_api_value: post_log's DB columns are still
    literally named policy_name/policy_api_value (see
    post_log_repository.insert_post_log_row(), which is a fixed-column
    positional INSERT) -- renaming them requires a migration, which is
    proposed but deliberately NOT applied as part of this change (this
    investigation was scoped to read-only DB queries; see the accompanying
    report for the exact migration to run before this repurposing should be
    considered permanent). Until then, these two columns are repurposed to
    hold the corrected Population values (population_name/
    population_api_value from population_service.py) rather than the
    retired Basic/VIP business label -- the dict keys below must stay
    "policy_name"/"policy_api_value" to match insert_post_log_row(), but
    what they now contain is Population, not Policy.

    execution_date/effective_start_date/start_date_source/http_status_code
    come from `result` (set by post_single_candidate()) via .get() --
    callers/tests that construct a `result` dict without them (predating
    these rules) still work, just recording NULL. http_status_code needs
    migrations/0009 (proposed, not yet applied) before it can actually be
    persisted -- see post_log_repository.insert_post_log_row().
    """
    post_log_status = _post_log_status_from_outcome(result["outcome"])
    is_vip = candidate.get("is_vip")
    population_name, population_api_value = resolve_population_values(candidate.get("dealer_shuffler"), is_vip)

    return {
        "run_id": run_id,
        "application_eid": candidate["application_eid"],
        "candidate_eid": candidate.get("candidate_eid"),
        "email": candidate.get("email"),
        "action": ACTION_CREATE_USER,
        "status": post_log_status,
        "is_vip": is_vip,
        "policy_name": population_name,
        "policy_api_value": population_api_value,
        "benivo_user_id": result["benivo_user_id"],
        "benivo_assignment_id": result["benivo_assignment_id"],
        "benivo_profile_url": result["benivo_profile_url"],
        "request_payload": result["request_payload"],
        "response_payload": result["response_payload"],
        "error_message": result["error_message"],
        "execution_date": result.get("execution_date"),
        "effective_start_date": result.get("effective_start_date"),
        "start_date_source": result.get("start_date_source"),
        "http_status_code": result.get("status_code"),
    }


def build_case_update_post_log_insert(run_id: str, candidate: Dict[str, Any], case_update: Dict[str, Any]) -> Dict[str, Any]:
    """
    Pure: maps a candidate + case_update result (the "case_update" key set
    by post_single_candidate() on a successful create-user) to a benivo.
    post_log row with action=UPDATE_CASE. No I/O.

    This is a SEPARATE row from the CREATE_USER row build_post_log_insert()
    produces -- see migrations/0006, which scopes the terminal-status
    unique index by (application_eid, action) specifically so this row
    never collides with the create-user row's own terminal status.
    policy_name/policy_api_value are not applicable to a Case update (no
    policy decision is made here) and are left None; is_vip is carried over
    for informational/audit purposes only.
    """
    status = "SUCCESS" if case_update.get("success") else "FAILED"

    return {
        "run_id": run_id,
        "application_eid": candidate["application_eid"],
        "candidate_eid": candidate.get("candidate_eid"),
        "email": candidate.get("email"),
        "action": ACTION_UPDATE_CASE,
        "status": status,
        "is_vip": candidate.get("is_vip"),
        "policy_name": None,
        "policy_api_value": None,
        "benivo_user_id": None,
        "benivo_assignment_id": case_update.get("case_id"),
        "benivo_profile_url": None,
        "request_payload": case_update.get("request_payload"),
        "response_payload": case_update.get("response_payload"),
        "error_message": case_update.get("error_message"),
        "execution_date": None,
        "effective_start_date": None,
        "start_date_source": None,
        "http_status_code": case_update.get("status_code"),
    }


def record_post_result(candidate: Dict[str, Any], result: Dict[str, Any], run_id: str) -> None:
    """
    Writes post_log + updates candidate status atomically, in one
    transaction. If `result` carries a "case_update" entry (set only when
    post_single_candidate() attempted the Case PATCH after a successful
    create-user), a second, independent post_log row (action=UPDATE_CASE)
    is written in the same transaction.

    candidate_status is derived ONLY from the create-user outcome
    (post_log_row["status"]) -- a failed Case PATCH is fully audited via
    the second row but never changes the candidate's POSTED status, per
    the confirmed rule that a successful create-user always remains POSTED.
    """
    post_log_row = build_post_log_insert(run_id, candidate, result)
    candidate_status = _candidate_status_from_post_log_status(post_log_row["status"])
    application_eid = post_log_row["application_eid"]

    case_update = result.get("case_update")
    case_update_row = build_case_update_post_log_insert(run_id, candidate, case_update) if case_update else None

    try:
        with transaction() as cur:
            insert_post_log_row(cur, post_log_row)

            if case_update_row is not None:
                insert_post_log_row(cur, case_update_row)

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
