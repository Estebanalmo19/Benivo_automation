"""
One-time explicit approved-application_eid batch selection.

Confirmed 2026-09-08 (first production/UAT import, application_eid=pP98MxwU
lifecycle-fix investigation): neither BENIVO_GO_LIVE_AT (a time-based scope
cutover, see synchronization_service.py/candidate_repository.py) nor
BENIVO_UAT_APPLICATION_EID (a single explicit candidate override, see
posting_service._select_explicit_uat_candidate()) can express "post exactly
this business-approved set of candidates, and nothing else, even if the
live READY_TO_POST population has since changed." get_ready_candidates()
re-queries live state on every call with no snapshot/frozen-batch concept
-- so a normal LIMIT-based selection at a later execution time is NOT
guaranteed to match an earlier reviewed/approved report (new candidates may
have entered scope; approved candidates may have left it).

This module is the fix: a small, version-controlled JSON artifact
(config/approved_batches/*.json, structure documented in
load_approved_batch()) names the exact application_eids that were reviewed
and approved. Selection starts from THAT explicit identity set -- never
from get_ready_candidates(limit=N) intersected afterward -- and
individually re-validates each one against the exact same live safety
checks the rest of the codebase already enforces (live Jobvite workflow
re-check via candidate_repository.get_source_workflow_state(), the
post_log terminal/duplicate check, benivo_status, is_relocation_required).
A candidate who was approved but has since become ineligible is reported
with a precise reason and simply excluded -- never force-posted, never
silently dropped without explanation.

Selection control only: this module builds no Benivo payload, resolves no
office, applies no Population/start-date/country rule, calls no Benivo
HTTP endpoint, and writes nothing to any table. Eligible candidates are
handed back as the exact same candidate dicts get_ready_candidates() would
have produced, to flow into the existing, unmodified post_candidates()/
post_single_candidate() pipeline -- see posting_service.select_postable_
candidates(), which is this module's only caller.
"""

import json
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

from app.models.domain import MOBILITY_WORKFLOW_STATE
from app.repositories.candidate_repository import get_candidate_by_application_eid, get_source_workflow_state
from app.repositories.post_log_repository import get_terminal_post_log_application_eids

logger = logging.getLogger(__name__)

REASON_INVALID_APPLICATION_EID = "invalid_application_eid"
REASON_CANDIDATE_NOT_FOUND = "candidate_not_found"
REASON_ALREADY_POSTED = "already_posted"
REASON_NOT_READY_TO_POST = "not_ready_to_post"
REASON_RELOCATION_NOT_REQUIRED = "relocation_not_required"
REASON_JOBVITE_WORKFLOW_NOT_MOBILITY = "jobvite_workflow_not_mobility"


class ApprovedBatchLoadError(Exception):
    """
    The approved batch file is missing, unreadable, malformed, or invalid.
    Callers must fail closed on this: select zero candidates, never fall
    back to any other selection path (normal LIMIT-based or UAT).
    """


def load_approved_batch(file_path: str) -> Dict[str, Any]:
    """
    Loads and validates one approved-batch JSON artifact. Expected shape:

        {
          "batch_name": "benivo_first_import_20260908",
          "approved_at": "2026-09-08",
          "approved_by": "Mobility/GM",
          "application_eids": ["...", "..."]
        }

    Only application_eid values and non-sensitive batch metadata belong in
    this file -- never candidate names, emails, phone numbers, or any other
    PII; this module never reads or requires any field beyond
    "application_eids" (the rest is carried through only for audit
    logging/traceability, e.g. batch_name).

    Fails closed (raises ApprovedBatchLoadError) on: file not found, unreadable,
    not valid JSON, not a JSON object, missing "application_eids", "application_eids"
    not a list, an empty list, any entry that isn't a non-blank string, or
    any duplicate application_eid.

    Duplicate policy (explicit, deliberate choice -- confirmed 2026-09-08):
    a duplicate application_eid in a one-time, hand-reviewed production-import
    artifact most likely signals a copy-paste or generation mistake.
    Silently de-duplicating would hide that mistake and could mask a
    reviewer's actual intent (e.g. two different entries that were meant to
    be two different eids). The whole file is treated as invalid rather
    than silently corrected -- consistent with every other failure mode
    here being a hard stop, not a best-effort recovery.
    """
    path = Path(file_path)

    if not path.is_file():
        raise ApprovedBatchLoadError(f"Approved batch file not found: {file_path!r}")

    try:
        raw_text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise ApprovedBatchLoadError(f"Approved batch file could not be read: {file_path!r} ({exc})") from exc

    try:
        data = json.loads(raw_text)
    except json.JSONDecodeError as exc:
        raise ApprovedBatchLoadError(f"Approved batch file is not valid JSON: {file_path!r} ({exc})") from exc

    if not isinstance(data, dict):
        raise ApprovedBatchLoadError(f"Approved batch file must contain a JSON object: {file_path!r}")

    if "application_eids" not in data:
        raise ApprovedBatchLoadError(
            f"Approved batch file is missing required key 'application_eids': {file_path!r}"
        )

    application_eids = data["application_eids"]

    if not isinstance(application_eids, list):
        raise ApprovedBatchLoadError(f"'application_eids' must be a JSON list: {file_path!r}")

    if len(application_eids) == 0:
        raise ApprovedBatchLoadError(
            f"'application_eids' is empty -- an approved batch must name at least one candidate: {file_path!r}"
        )

    normalized: List[str] = []

    for index, raw_eid in enumerate(application_eids):
        if not isinstance(raw_eid, str) or not raw_eid.strip():
            raise ApprovedBatchLoadError(
                f"application_eids[{index}] is not a non-blank string: {raw_eid!r} in {file_path!r}"
            )
        normalized.append(raw_eid.strip())

    seen: Set[str] = set()
    duplicates: Set[str] = set()

    for eid in normalized:
        if eid in seen:
            duplicates.add(eid)
        seen.add(eid)

    if duplicates:
        raise ApprovedBatchLoadError(
            f"Approved batch file contains duplicate application_eid(s) {sorted(duplicates)}: {file_path!r}"
        )

    data["application_eids"] = normalized
    return data


def _evaluate_single(
    application_eid: str,
    terminal_eids: Set[str],
) -> Tuple[bool, Optional[Dict[str, Any]], Optional[str]]:
    """
    One approved application_eid, checked against exactly the same live
    safety conditions candidate_repository.get_ready_candidates()'s SQL and
    posting_service.validate_uat_candidate() already enforce -- no new
    business rule, no relaxed rule. Order matters only for which single
    reason is reported when more than one condition fails; every check
    still runs against live data regardless of order.
    """
    if not application_eid or not application_eid.strip():
        return False, None, REASON_INVALID_APPLICATION_EID

    candidate = get_candidate_by_application_eid(application_eid)

    if candidate is None:
        return False, None, REASON_CANDIDATE_NOT_FOUND

    if application_eid in terminal_eids:
        return False, candidate, REASON_ALREADY_POSTED

    if candidate.get("benivo_status") != "READY_TO_POST":
        return False, candidate, REASON_NOT_READY_TO_POST

    if (candidate.get("is_relocation_required") or "").strip().lower() != "yes":
        return False, candidate, REASON_RELOCATION_NOT_REQUIRED

    # Live authoritative re-check -- the same pP98MxwU-fix safety gate as
    # get_ready_candidates()'s own EXISTS clause and validate_uat_candidate()'s
    # source_still_mobility_in_process check. Never trust the cached
    # benivo.candidates.workflow_state alone.
    if get_source_workflow_state(application_eid) != MOBILITY_WORKFLOW_STATE:
        return False, candidate, REASON_JOBVITE_WORKFLOW_NOT_MOBILITY

    return True, candidate, None


def evaluate_approved_batch(application_eids: List[str]) -> Dict[str, Any]:
    """
    Individually revalidates every explicit application_eid. Returns
    {"eligible": [candidate, ...], "ineligible": [{"application_eid", "reason"}, ...]}.
    Never a bulk get_ready_candidates(limit=N) intersected afterward -- an
    approved candidate who has since become ineligible gets a precise
    reason here instead of silently vanishing from a LIMIT-based result.
    """
    terminal_eids = get_terminal_post_log_application_eids()  # one bulk read, reused for every eid below

    eligible: List[Dict[str, Any]] = []
    ineligible: List[Dict[str, Any]] = []

    for application_eid in application_eids:
        is_eligible, candidate, reason = _evaluate_single(application_eid, terminal_eids)

        if is_eligible:
            eligible.append(candidate)
        else:
            ineligible.append({"application_eid": application_eid, "reason": reason})

    return {"eligible": eligible, "ineligible": ineligible}


def select_approved_batch_candidates(file_path: str) -> List[Dict[str, Any]]:
    """
    Entry point used by posting_service.select_postable_candidates() when
    BENIVO_APPROVED_BATCH_FILE is set. Fail closed: any problem loading the
    batch file logs exactly why and returns zero candidates -- never falls
    back to normal LIMIT-based or UAT selection (mirrors
    _select_explicit_uat_candidate()'s "no fallback" contract).

    Ignores any candidate-count limit entirely (BENIVO_MAX_CANDIDATES
    included) -- the approved batch file itself is the explicit, reviewed
    upper bound for this one-time selection; every eligible approved
    candidate is returned, deliberately never truncated by an unrelated
    default (BENIVO_MAX_CANDIDATES defaults to 1 and is otherwise silently
    reused everywhere else in posting_service.py). Normal, non-batch
    posting is untouched by this and keeps enforcing BENIVO_MAX_CANDIDATES
    exactly as before.

    Logs, and never prints/returns, any candidate PII: only application_eid
    (never a real secret/identifier of a person on its own -- it is
    Jobvite's own tracking id, already used unmasked everywhere else in
    this codebase's logs) and the batch/eligibility bookkeeping.
    """
    try:
        batch = load_approved_batch(file_path)
    except ApprovedBatchLoadError as exc:
        logger.error("Approved batch selection stopped -- %s. No candidates selected, no fallback.", exc)
        return []

    application_eids = batch["application_eids"]
    result = evaluate_approved_batch(application_eids)

    batch_name = batch.get("batch_name") or Path(file_path).stem

    logger.info(
        "Approved batch audit: BATCH_NAME=%s APPROVED_EIDS_COUNT=%d ELIGIBLE_APPROVED_COUNT=%d INELIGIBLE_APPROVED_COUNT=%d",
        batch_name,
        len(application_eids),
        len(result["eligible"]),
        len(result["ineligible"]),
    )

    for entry in result["ineligible"]:
        logger.info(
            "Approved batch ineligible: application_eid=%s reason=%s",
            entry["application_eid"],
            entry["reason"],
        )

    return result["eligible"]
