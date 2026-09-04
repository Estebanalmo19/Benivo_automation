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
# and benivo_response are integration-owned: intentionally absent from both
# the INSERT column list's dependency on source data and DO UPDATE SET, so
# a sync never overwrites them. benivo_status is only set to
# DEFAULT_BENIVO_STATUS on first insert; classification happens later, in
# classification_service.
#
# is_vip: confirmed 2026-08-24 -- application.customField[fieldCode=
# 'mobility_vip'] (values "Yes"/"No") is the real Jobvite source for VIP,
# superseding the earlier finding that no confirmed source existed (that
# finding covered host_country/host_city/population/the old unconfirmed
# "vip" text column -- NOT this field, which didn't exist in Jobvite yet at
# the time). is_vip IS now source-owned and refreshed every sync, exactly
# like start_date/workplace/home_country below: TRUE only when
# mobility_vip = 'Yes', FALSE when it's 'No' or the field is absent
# entirely (COALESCE guards the "absent" case, since a raw SQL comparison
# against NULL would itself evaluate to NULL, not FALSE). See
# app/services/population_service.py for the one centralized place this
# value (alongside dealer_shuffler, joined separately -- see
# candidate_repository.DEALER_SHUFFLER_SUBQUERY) feeds into a Benivo
# Population value -- never duplicate that mapping here or anywhere else.
#
# start_date, workplace, and home_country ARE source-owned and must be
# refreshed every sync (per confirmed evidence, see git history and the
# 2026-08-06 Jobvite data audit):
#   start_date    <- application.startDate (epoch-ms string), formula
#     verified against 5 rows where both source and legacy target values
#     existed: to_timestamp(ms::bigint / 1000)::date matched exactly in all 5.
#   workplace     <- application.job.customField['site'], verified against
#     816 existing target rows: 812 (99.5%) already matched this value
#     exactly.
#   home_country  <- application.customField['candidate_home_country']
#     (label "Candidate home country"). Confirmed as the correct,
#     purpose-built source (distinct from the candidate's top-level mailing
#     address country) during the 2026-08-06 audit; previously present in
#     raw_payload but never wired into this UPSERT -- benivo.candidates
#     .home_country stayed NULL despite the column already existing.
#   current_country <- raw_payload.countryName (top-level candidate-profile
#     field, sibling of firstName/lastName/email -- NOT parsed from the
#     'location' display string "city, state country", NOT
#     application.job.location which is the JOB's location). Confirmed
#     2026-08-10 while investigating why candidates with country data
#     visible in Jobvite were showing as missing home_country: neither
#     example candidate has a candidate_home_country customField entry, but
#     both -- and 100% of the 99 candidates missing home_country as of that
#     investigation -- have a populated countryName. This is the FALLBACK
#     source only; app/services/home_country_service.py resolves the
#     effective value candidates/postings actually use. home_country itself
#     is never overwritten by this fallback.
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
    home_country,
    current_country,
    is_vip,
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
    (
        SELECT app_cf->>'value'
        FROM jsonb_array_elements(j.raw_payload->'application'->'customField') app_cf
        WHERE app_cf->>'fieldCode' = 'candidate_home_country'
        LIMIT 1
    ) AS home_country,
    NULLIF(j.raw_payload->>'countryName', '') AS current_country,
    COALESCE(
        (
            SELECT app_cf->>'value'
            FROM jsonb_array_elements(j.raw_payload->'application'->'customField') app_cf
            WHERE app_cf->>'fieldCode' = 'mobility_vip'
            LIMIT 1
        ) = 'Yes',
        FALSE
    ) AS is_vip,
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
    home_country = EXCLUDED.home_country,
    current_country = EXCLUDED.current_country,
    is_vip = EXCLUDED.is_vip,
    source_payload = EXCLUDED.source_payload,
    updated_at = NOW();
"""

# Durable, append-only record of the first time an application_eid was ever
# detected inside this same scope predicate -- see migrations/0008 and
# app/config.go_live_at()/candidate_repository.get_ready_candidates() for
# how it gates automatic posting on go-live. ON CONFLICT DO NOTHING is the
# whole mechanism: an application_eid is inserted here exactly once, ever,
# so first_seen_in_scope_at survives _DELETE_OUT_OF_SCOPE_SQL below
# removing (and a later sync re-inserting) the SAME application_eid's
# benivo.candidates row any number of times -- a candidate leaving Mobility
# and re-entering later never looks "new" again. Uses the identical scope
# predicate as _UPSERT_SQL so the two never disagree about who's in scope.
_SCOPE_HISTORY_UPSERT_SQL = """
INSERT INTO benivo.scope_history (application_eid, candidate_eid, first_seen_in_scope_at)
SELECT DISTINCT j.application_eid, j.candidate_eid, NOW()
FROM jv_arrise_data_schema.jobvite_applications j
CROSS JOIN LATERAL jsonb_array_elements(
    j.raw_payload->'application'->'customField'
) cf
WHERE j.workflow_state = %(workflow_state)s
  AND cf->>'fieldCode' = %(relocation_field_code)s
  AND cf->>'value' IN %(relocation_values)s
ON CONFLICT (application_eid) DO NOTHING;
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

            # Must run BEFORE the delete below and in the same transaction:
            # this is what makes first_seen_in_scope_at durable across a
            # candidate leaving and later re-entering scope in-between syncs.
            cur.execute(_SCOPE_HISTORY_UPSERT_SQL, params)

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
