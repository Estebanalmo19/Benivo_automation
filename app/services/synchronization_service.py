"""Structural synchronization of benivo.candidates from jv_arrise_data_schema.jobvite_applications.

Pure data synchronization only: no business classification, no posting
decisions. See classification_service.py for business rules.
"""

import logging
import time
from typing import Any, Dict

import psycopg2

from app.clients.database_client import transaction

logger = logging.getLogger(__name__)

WORKFLOW_STATE = "Mobility in process"
RELOCATION_FIELD_CODE = "is_relocation_required"
RELOCATION_VALUES = ("Yes", "No")
DEFAULT_BENIVO_STATUS = "PENDING"

# benivo_status, benivo_user_id, benivo_assignment_id, benivo_profile_url,
# benivo_response, and is_vip are integration-owned: intentionally absent
# from both the INSERT column list's dependency on source data and DO
# UPDATE SET, so a sync never overwrites them. benivo_status is only set to
# DEFAULT_BENIVO_STATUS on first insert; classification happens later, in
# classification_service.
#
# is_vip specifically: there is currently no confirmed Jobvite source field
# for VIP (application/job customFields were inspected for this same reason
# host_country/host_city/population/the old "vip" text field were ruled
# out -- see below). is_vip is deliberately excluded from this UPSERT so new
# rows get the column's own DEFAULT FALSE (migrations/0003) and existing
# rows keep whatever value was set by classification/posting/manual review.
# If a confirmed Jobvite source for VIP is ever identified, add it as a new
# SELECT expression here (same pattern as start_date/workplace below) AND
# add it to the DO UPDATE SET list so it becomes source-refreshed like them.
#
# start_date and workplace ARE source-owned and must be refreshed every
# sync (per confirmed evidence, see git history):
#   start_date <- application.startDate (epoch-ms string), formula verified
#     against 5 rows where both source and legacy target values existed:
#     to_timestamp(ms::bigint / 1000)::date matched exactly in all 5.
#   workplace  <- application.job.customField['site'], verified against 816
#     existing target rows: 812 (99.5%) already matched this value exactly.
# host_country, host_city, population, vip have NO confirmed source field
# anywhere in jv_arrise_data_schema.jobvite_applications (application
# customFields, job customFields, and top-level candidate/application keys
# were all inspected) and are deliberately NOT included here.
_UPSERT_SQL = """
INSERT INTO benivo.candidates (
    application_eid,
    candidate_eid,
    email,
    first_name,
    last_name,
    phone_number,
    workflow_state,
    is_relocation_required,
    job_title,
    requisition_id,
    department,
    location,
    start_date,
    workplace,
    source_payload,
    benivo_status,
    updated_at
)
SELECT
    j.application_eid,
    j.candidate_eid,
    LOWER(TRIM(j.email)) AS email,
    j.first_name,
    j.last_name,
    j.mobile AS phone_number,
    j.workflow_state,
    cf->>'value' AS is_relocation_required,
    j.job_title,
    j.requisition_id,
    j.department,
    j.location,
    CASE
        WHEN (j.raw_payload->'application'->>'startDate') ~ '^[0-9]+$'
        THEN to_timestamp((j.raw_payload->'application'->>'startDate')::bigint / 1000)::date
        ELSE NULL
    END AS start_date,
    (
        SELECT job_cf->>'value'
        FROM jsonb_array_elements(j.raw_payload->'application'->'job'->'customField') job_cf
        WHERE job_cf->>'fieldCode' = 'site'
        LIMIT 1
    ) AS workplace,
    j.raw_payload,
    %(default_status)s,
    NOW()
FROM jv_arrise_data_schema.jobvite_applications j
CROSS JOIN LATERAL jsonb_array_elements(
    j.raw_payload->'application'->'customField'
) cf
WHERE j.workflow_state = %(workflow_state)s
  AND cf->>'fieldCode' = %(relocation_field_code)s
  AND cf->>'value' IN %(relocation_values)s
ON CONFLICT (application_eid)
DO UPDATE SET
    candidate_eid = EXCLUDED.candidate_eid,
    email = EXCLUDED.email,
    first_name = EXCLUDED.first_name,
    last_name = EXCLUDED.last_name,
    phone_number = EXCLUDED.phone_number,
    workflow_state = EXCLUDED.workflow_state,
    is_relocation_required = EXCLUDED.is_relocation_required,
    job_title = EXCLUDED.job_title,
    requisition_id = EXCLUDED.requisition_id,
    department = EXCLUDED.department,
    location = EXCLUDED.location,
    start_date = EXCLUDED.start_date,
    workplace = EXCLUDED.workplace,
    source_payload = EXCLUDED.source_payload,
    updated_at = NOW();
"""

# Removes candidates that no longer belong to the active source universe
# (workflow_state moved on, relocation field changed/disappeared, or the
# source row itself disappeared). Membership only -- no readiness judgment,
# so this stays a synchronization concern, not classification.
_DELETE_OUT_OF_SCOPE_SQL = """
DELETE FROM benivo.candidates c
WHERE NOT EXISTS (
    SELECT 1
    FROM jv_arrise_data_schema.jobvite_applications j
    CROSS JOIN LATERAL jsonb_array_elements(
        j.raw_payload->'application'->'customField'
    ) cf
    WHERE j.application_eid = c.application_eid
      AND j.workflow_state = %(workflow_state)s
      AND cf->>'fieldCode' = %(relocation_field_code)s
      AND cf->>'value' IN %(relocation_values)s
);
"""


def sync_candidates() -> Dict[str, Any]:
    """
    Refresh benivo.candidates from jv_arrise_data_schema.jobvite_applications:
    upsert current matches, remove rows no longer in scope. One transaction;
    rolls back entirely on failure. Contains no business classification.
    """
    logger.info(
        "Candidate synchronization started "
        "(jv_arrise_data_schema.jobvite_applications -> benivo.candidates)."
    )

    params = {
        "default_status": DEFAULT_BENIVO_STATUS,
        "workflow_state": WORKFLOW_STATE,
        "relocation_field_code": RELOCATION_FIELD_CODE,
        "relocation_values": RELOCATION_VALUES,
    }

    started_at = time.perf_counter()

    try:
        with transaction() as cur:
            cur.execute(_UPSERT_SQL, params)
            upserted = cur.rowcount

            cur.execute(_DELETE_OUT_OF_SCOPE_SQL, params)
            removed = cur.rowcount
    except psycopg2.Error:
        duration_seconds = time.perf_counter() - started_at
        logger.exception(
            "Candidate synchronization failed after %.2fs.", duration_seconds
        )
        raise

    duration_seconds = time.perf_counter() - started_at

    logger.info(
        "Candidate synchronization finished: %d row(s) upserted, %d row(s) removed, in %.2fs.",
        upserted,
        removed,
        duration_seconds,
    )

    return {
        "inserted_or_updated": upserted,
        "removed": removed,
        "duration_seconds": round(duration_seconds, 2),
    }
