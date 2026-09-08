"""Business classification of benivo.candidates rows.

The only place candidate-readiness business rules live. Runs after
synchronization_service.sync_candidates() and re-evaluates every
non-terminal candidate on every execution, so a recruiter fixing
is_relocation_required -- or a new office mapping being added -- is picked
up automatically on the next run.
"""

import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import psycopg2

from app.clients.database_client import transaction
from app.models.domain import (
    EXCLUDED_MOBILITY_SUPPORT,
    MOBILITY_WORKFLOW_STATE,
    NEEDS_RECRUITER_REVIEW,
    NO_LONGER_ELIGIBLE,
    PENDING_MISSING_START_DATE,
    PENDING_OFFICE_MAPPING,
    POSTED,
    READY_TO_POST,
    TERMINAL_CANDIDATE_STATUSES,
)
from app.repositories.candidate_repository import MOBILITY_SUPPORT_SUBQUERY
from app.services.mobility_scope_service import mobility_support_qualifies
from app.services.office_resolution_service import resolve_office_name
from app.services.start_date_service import resolve_effective_start_date

logger = logging.getLogger(__name__)


def is_terminal(status: Optional[str]) -> bool:
    return status in TERMINAL_CANDIDATE_STATUSES


def classify(
    is_relocation_required: Optional[str],
    start_date: Any,
    workplace: Optional[str],
    execution_timestamp: datetime,
    mobility_support: Optional[str] = None,
    workflow_state: Optional[str] = None,
) -> str:
    """
    Pure business rule, no network I/O (office resolution is against the
    static, explicit office_resolution_service.WORKPLACE_TO_OFFICE_NAME
    mapping only -- no API call):
      workflow_state != MOBILITY_WORKFLOW_STATE                  -> NO_LONGER_ELIGIBLE
      relocation != Yes                                          -> NEEDS_RECRUITER_REVIEW
      mobility_support doesn't request Relocation/Visa/Accommodation
                                                                   -> EXCLUDED_MOBILITY_SUPPORT
      effective start date NOT resolved                          -> PENDING_MISSING_START_DATE
      workplace unmapped                                         -> PENDING_OFFICE_MAPPING
      otherwise                                                  -> READY_TO_POST

    workflow_state is checked FIRST, ahead of every other rule (confirmed
    2026-09-08, defense in depth -- Layer 2 of 3 alongside
    synchronization_service._MARK_OUT_OF_SCOPE_SQL and
    candidate_repository.get_ready_candidates()'s own live re-check). It
    must be the candidate's row-level benivo.candidates.workflow_state
    (never re-derived here, never a live Jobvite call) -- that column is
    now ALWAYS kept fresh by synchronization_service.py, whether the
    candidate is currently in scope (refreshed by the UPSERT) or has left
    it (refreshed by _MARK_OUT_OF_SCOPE_SQL, which sets both the value and
    NO_LONGER_ELIGIBLE together). Trusting it here means a candidate whose
    workflow moved on gets reclassified to NO_LONGER_ELIGIBLE (not
    incorrectly reclassified back to READY_TO_POST from other, now-stale,
    cached fields) on every run, without waiting for the next sync.
    NO_LONGER_ELIGIBLE is NOT in TERMINAL_CANDIDATE_STATUSES: a candidate
    who legitimately returns to MOBILITY_WORKFLOW_STATE is re-adopted by
    the UPSERT (which refreshes their other fields once they match again)
    and re-enters this function with fresh data on the next run, exactly
    like any other non-terminal status.

    mobility_support defaults to None only so this signature stays
    introspectable; every real caller (classify_candidates() below) always
    passes it explicitly -- a None mobility_support fails the qualification
    check the same way a missing Jobvite field does (see
    mobility_scope_service.mobility_support_qualifies()), it is never
    silently treated as "qualifies". workflow_state follows the same
    convention: None fails closed to NO_LONGER_ELIGIBLE rather than being
    assumed to mean "still in scope".

    CORRECTED 2026-09-05: this function previously also excluded "domestic
    relocation" candidates (home country == the resolved Benivo office's
    host country) whose destination wasn't UAE. Mobility explicitly
    corrected this: domestic/local status is NOT a general exclusion gate
    for any country -- mobility_support alone determines scope. See
    mobility_scope_service.py's module docstring for the full correction;
    that domestic-comparison logic has been removed from this codebase
    entirely, not merely disabled.

    start_date is Jobvite-sourced and may be missing. When it is,
    resolve_effective_start_date() (temporary business rule, see that
    module) supplies a calculated fallback instead of blocking readiness --
    so PENDING_MISSING_START_DATE is now a defensive branch only, reachable
    only if a future rule change makes resolution genuinely fail.
    """
    if workflow_state != MOBILITY_WORKFLOW_STATE:
        return NO_LONGER_ELIGIBLE

    value = (is_relocation_required or "").strip().lower()

    if value != "yes":
        return NEEDS_RECRUITER_REVIEW

    if not mobility_support_qualifies(mobility_support):
        return EXCLUDED_MOBILITY_SUPPORT

    effective_start_date, _source = resolve_effective_start_date(start_date, execution_timestamp)

    if not effective_start_date:
        return PENDING_MISSING_START_DATE

    if resolve_office_name(workplace) is None:
        return PENDING_OFFICE_MAPPING

    return READY_TO_POST


def _fetch_classifiable_candidates(cur) -> List[Dict[str, Any]]:
    cur.execute(
        f"""
        SELECT c.id, c.is_relocation_required, c.start_date, c.workplace, c.workflow_state,
               {MOBILITY_SUPPORT_SUBQUERY}
        FROM benivo.candidates c
        WHERE c.benivo_status IS DISTINCT FROM %s
        """,
        (POSTED,),
    )
    return cur.fetchall()


def classify_candidates() -> Dict[str, int]:
    """Re-derive benivo_status for every non-terminal candidate, in one transaction."""
    logger.info("Candidate classification started.")

    # Generated once for the whole run and reused for every candidate below
    # -- never call datetime.now() per-candidate, or candidates processed
    # moments apart could land in different calculated months for no
    # business reason. See start_date_service.resolve_effective_start_date().
    execution_timestamp = datetime.now(timezone.utc)

    counts = {
        READY_TO_POST: 0,
        PENDING_MISSING_START_DATE: 0,
        PENDING_OFFICE_MAPPING: 0,
        NEEDS_RECRUITER_REVIEW: 0,
        EXCLUDED_MOBILITY_SUPPORT: 0,
        NO_LONGER_ELIGIBLE: 0,
        "updated": 0,
        "unchanged": 0,
    }

    try:
        with transaction() as cur:
            rows = _fetch_classifiable_candidates(cur)

            for row in rows:
                new_status = classify(
                    row["is_relocation_required"],
                    row["start_date"],
                    row["workplace"],
                    execution_timestamp,
                    mobility_support=row["mobility_support"],
                    workflow_state=row["workflow_state"],
                )
                counts[new_status] += 1

                cur.execute(
                    """
                    UPDATE benivo.candidates
                    SET benivo_status = %s, updated_at = NOW()
                    WHERE id = %s AND benivo_status IS DISTINCT FROM %s
                    """,
                    (new_status, row["id"], new_status),
                )

                if cur.rowcount > 0:
                    counts["updated"] += 1
                else:
                    counts["unchanged"] += 1
    except psycopg2.Error:
        logger.exception("Candidate classification failed.")
        raise

    logger.info("Candidate classification finished: %s", counts)
    return counts
