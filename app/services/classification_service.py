"""Business classification of benivo.candidates rows.

The only place candidate-readiness business rules live. Runs after
synchronization_service.sync_candidates() and re-evaluates every
non-terminal candidate on every execution, so a recruiter fixing
is_relocation_required -- or a new office mapping being added -- is picked
up automatically on the next run.
"""

import logging
from typing import Any, Dict, List, Optional

import psycopg2

from app.clients.database_client import transaction
from app.models.domain import (
    NEEDS_RECRUITER_REVIEW,
    PENDING_MISSING_START_DATE,
    PENDING_OFFICE_MAPPING,
    POSTED,
    READY_TO_POST,
    TERMINAL_CANDIDATE_STATUSES,
)
from app.services.office_resolution_service import resolve_office_name

logger = logging.getLogger(__name__)


def is_terminal(status: Optional[str]) -> bool:
    return status in TERMINAL_CANDIDATE_STATUSES


def classify(is_relocation_required: Optional[str], start_date: Any, workplace: Optional[str]) -> str:
    """
    Pure business rule, no network I/O (office check is against the static,
    explicit office_resolution_service.WORKPLACE_TO_OFFICE_NAME mapping
    only -- no API call):
      Yes + start_date present + workplace mapped   -> READY_TO_POST
      Yes + start_date present + workplace unmapped  -> PENDING_OFFICE_MAPPING
      Yes + start_date missing                       -> PENDING_MISSING_START_DATE
      No / null / unrecognized                       -> NEEDS_RECRUITER_REVIEW
    """
    value = (is_relocation_required or "").strip().lower()

    if value != "yes":
        return NEEDS_RECRUITER_REVIEW

    if not start_date:
        return PENDING_MISSING_START_DATE

    if resolve_office_name(workplace) is None:
        return PENDING_OFFICE_MAPPING

    return READY_TO_POST


def _fetch_classifiable_candidates(cur) -> List[Dict[str, Any]]:
    cur.execute(
        """
        SELECT id, is_relocation_required, start_date, workplace
        FROM benivo.candidates
        WHERE benivo_status IS DISTINCT FROM %s
        """,
        (POSTED,),
    )
    return cur.fetchall()


def classify_candidates() -> Dict[str, int]:
    """Re-derive benivo_status for every non-terminal candidate, in one transaction."""
    logger.info("Candidate classification started.")

    counts = {
        READY_TO_POST: 0,
        PENDING_MISSING_START_DATE: 0,
        PENDING_OFFICE_MAPPING: 0,
        NEEDS_RECRUITER_REVIEW: 0,
        "updated": 0,
        "unchanged": 0,
    }

    try:
        with transaction() as cur:
            rows = _fetch_classifiable_candidates(cur)

            for row in rows:
                new_status = classify(row["is_relocation_required"], row["start_date"], row["workplace"])
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
