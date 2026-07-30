"""Read/write access to benivo.candidates. No business rules here -- see app/services."""

from typing import Any, Dict, List, Optional

from app import config
from app.clients.database_client import db_cursor
from app.models.domain import TERMINAL_POST_LOG_STATUSES

READY_CANDIDATE_FIELDS = """
    c.id, c.application_eid, c.candidate_eid, c.email, c.first_name, c.last_name,
    c.job_title, c.requisition_id, c.workplace, c.host_country, c.host_city,
    c.start_date, c.benivo_status, c.created_at, c.updated_at,
    c.phone_number, c.location, c.population, c.vip, c.is_vip, c.gender,
    c.home_country, c.home_state_province, c.home_city, c.country_of_birth,
    c.citizenship, c.employee_id, c.billing_entity, c.host_legal_entity,
    c.host_business_unit
"""

REPORTING_FIELDS = """
    c.id, c.application_eid, c.candidate_eid, c.email, c.first_name, c.last_name,
    c.job_title, c.requisition_id, c.workplace, c.host_country, c.host_city,
    c.start_date, c.benivo_status, c.created_at, c.updated_at
"""

FULL_REPORT_FIELDS = """
    c.id, c.application_eid, c.candidate_eid, c.email, c.first_name, c.last_name,
    c.workflow_state, c.is_relocation_required, c.start_date, c.workplace,
    c.job_title, c.requisition_id, c.department, c.location, c.benivo_status,
    c.is_vip, c.created_at, c.updated_at
"""


def get_max_candidates(default: Optional[int] = None) -> int:
    return default if default is not None else config.legacy_max_candidates()


def get_ready_candidates(limit: Optional[int] = None) -> List[Dict[str, Any]]:
    """Deterministic, oldest-first, SQL-limited selection of postable candidates."""
    max_candidates = limit if limit is not None else get_max_candidates()

    query = f"""
        SELECT {READY_CANDIDATE_FIELDS}
        FROM benivo.candidates c
        WHERE c.benivo_status = 'READY_TO_POST'
          AND c.is_relocation_required = 'Yes'
          AND c.application_eid IS NOT NULL
          AND BTRIM(c.application_eid) <> ''
          AND NOT EXISTS (
              SELECT 1
              FROM benivo.post_log pl
              WHERE pl.application_eid = c.application_eid
                AND pl.status = ANY(%(terminal_statuses)s)
          )
        ORDER BY c.created_at, c.id
        LIMIT %(limit)s
    """

    with db_cursor() as cur:
        cur.execute(query, {"terminal_statuses": list(TERMINAL_POST_LOG_STATUSES), "limit": max_candidates})
        return [dict(row) for row in cur.fetchall()]


def get_candidates_missing_start_date() -> List[Dict[str, Any]]:
    query = f"""
        SELECT {REPORTING_FIELDS}
        FROM benivo.candidates c
        WHERE c.benivo_status = 'PENDING_MISSING_START_DATE'
          AND c.is_relocation_required = 'Yes'
        ORDER BY c.created_at, c.id
    """

    with db_cursor() as cur:
        cur.execute(query)
        return [dict(row) for row in cur.fetchall()]


def get_candidates_needing_review() -> List[Dict[str, Any]]:
    query = f"""
        SELECT {REPORTING_FIELDS}
        FROM benivo.candidates c
        WHERE c.benivo_status = 'NEEDS_RECRUITER_REVIEW'
        ORDER BY c.created_at, c.id
    """

    with db_cursor() as cur:
        cur.execute(query)
        return [dict(row) for row in cur.fetchall()]


def get_all_candidates_for_report() -> List[Dict[str, Any]]:
    """Every currently synced candidate with the full field set the operational report needs."""
    query = f"""
        SELECT {FULL_REPORT_FIELDS}
        FROM benivo.candidates c
        ORDER BY c.created_at, c.id
    """

    with db_cursor() as cur:
        cur.execute(query)
        return [dict(row) for row in cur.fetchall()]


def get_candidate_by_application_eid(application_eid: str) -> Optional[Dict[str, Any]]:
    """Fetch one candidate by application_eid regardless of eligibility -- used for explicit UAT candidate validation."""
    query = f"""
        SELECT {FULL_REPORT_FIELDS}
        FROM benivo.candidates c
        WHERE c.application_eid = %(application_eid)s
    """

    with db_cursor() as cur:
        cur.execute(query, {"application_eid": application_eid})
        row = cur.fetchone()
        return dict(row) if row else None


def update_candidate_after_posting(
    cur,
    application_eid: str,
    benivo_status: str,
    benivo_user_id: Optional[int],
    benivo_assignment_id: Optional[int],
    benivo_profile_url: Optional[str],
) -> None:
    """Runs on a cursor already inside a transaction -- caller owns the commit/rollback boundary."""
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
        (benivo_status, benivo_user_id, benivo_assignment_id, benivo_profile_url, application_eid),
    )
