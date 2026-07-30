import os
from contextlib import contextmanager
from typing import Any, Dict, List, Optional, Set

import psycopg2
import psycopg2.extras
from dotenv import load_dotenv

load_dotenv()

DB_HOST = os.getenv("DB_HOST")
DB_PORT = os.getenv("DB_PORT")
DB_NAME = os.getenv("DB_NAME")
DB_USER = os.getenv("DB_USER")
DB_PASSWORD = os.getenv("DB_PASSWORD")

DEFAULT_MAX_CANDIDATES = 1

# post_log statuses that block a candidate from being selected again.
# SUCCESS and ALREADY_EXISTS are terminal; FAILED remains retryable.
TERMINAL_POST_LOG_STATUSES = ("SUCCESS", "ALREADY_EXISTS")

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


def get_connection(autocommit: bool = True):
    conn = psycopg2.connect(
        host=DB_HOST,
        port=DB_PORT,
        dbname=DB_NAME,
        user=DB_USER,
        password=DB_PASSWORD,
    )
    conn.autocommit = autocommit
    return conn


@contextmanager
def db_cursor():
    """Single-statement, autocommit access. Not for multi-statement writes needing atomicity -- use transaction()."""
    conn = get_connection()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            yield cur
    finally:
        conn.close()


@contextmanager
def transaction():
    """Multi-statement write access: commits on success, rolls back the whole batch on any exception."""
    conn = get_connection(autocommit=False)
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            yield cur
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def get_max_candidates(default: int = DEFAULT_MAX_CANDIDATES) -> int:
    raw = os.getenv("MAX_CANDIDATES")

    if raw is None or raw.strip() == "":
        return default

    try:
        value = int(raw)
    except ValueError:
        return default

    return value if value > 0 else default


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


def get_terminal_post_log_application_eids() -> Set[str]:
    """application_eids with a SUCCESS or ALREADY_EXISTS row in benivo.post_log -- never posted again."""
    query = """
        SELECT DISTINCT application_eid
        FROM benivo.post_log
        WHERE status = ANY(%(terminal_statuses)s)
    """

    with db_cursor() as cur:
        cur.execute(query, {"terminal_statuses": list(TERMINAL_POST_LOG_STATUSES)})
        return {row["application_eid"] for row in cur.fetchall() if row["application_eid"]}


# ---------------------------------------------------------------------------
# Write layer: Jobvite -> benivo.candidates synchronization
# ---------------------------------------------------------------------------
#
# application_eid has no confirmed UNIQUE/PRIMARY KEY constraint on
# benivo.candidates (the only confirmed unique index is the partial one on
# benivo.post_log: UNIQUE (application_eid) WHERE status = 'SUCCESS').
# Because of that, upsert_candidates() below intentionally does NOT use
# INSERT ... ON CONFLICT (application_eid), since Postgres requires a
# matching unique/exclusion constraint for that clause to work at all.
# Instead it uses an explicit UPDATE-then-INSERT strategy inside a single
# transaction. To make this fully race-safe and to enable a simpler
# ON CONFLICT-based upsert in the future, add:
#
#   ALTER TABLE benivo.candidates
#     ADD CONSTRAINT candidates_application_eid_key UNIQUE (application_eid);

CANDIDATE_WRITE_COLUMNS = [
    "candidate_eid",
    "email",
    "first_name",
    "last_name",
    "phone_number",
    "workflow_state",
    "is_relocation_required",
    "job_title",
    "requisition_id",
    "department",
    "location",
    "population",
    "workplace",
    "host_country",
    "host_city",
    "vip",
    "gender",
    "start_date",
    "home_country",
    "home_state_province",
    "home_city",
    "country_of_birth",
    "citizenship",
    "employee_id",
    "billing_entity",
    "host_legal_entity",
    "host_business_unit",
    "source_payload",
    "benivo_status",
]


def get_success_application_eids() -> Set[str]:
    """application_eids with a SUCCESS row in benivo.post_log; their benivo_status must not be overwritten by a sync."""
    query = """
        SELECT DISTINCT application_eid
        FROM benivo.post_log
        WHERE status = 'SUCCESS'
    """

    with db_cursor() as cur:
        cur.execute(query)
        return {row["application_eid"] for row in cur.fetchall() if row["application_eid"]}


def _prepare_value(column: str, value: Any) -> Any:
    if column == "source_payload" and value is not None:
        return psycopg2.extras.Json(value)
    return value


def upsert_candidates(rows: List[Dict[str, Any]]) -> Dict[str, int]:
    """
    Idempotent UPSERT into benivo.candidates, keyed on application_eid.

    Update-then-insert strategy (see module note above on why ON CONFLICT
    is not used). Runs as a single transaction: any database error rolls
    back the whole batch, so partial writes never persist. Rows already
    marked SUCCESS in benivo.post_log keep their existing benivo_status
    (only their other fields are refreshed).
    """
    result = {"inserted": 0, "updated": 0, "skipped": 0, "failed": 0}

    valid_rows = []
    for row in rows:
        application_eid = row.get("application_eid")
        if application_eid is None or not str(application_eid).strip():
            result["skipped"] += 1
            continue
        valid_rows.append(row)

    if not valid_rows:
        return result

    protected_eids = get_success_application_eids()

    conn = get_connection(autocommit=False)

    try:
        with conn.cursor() as cur:
            for row in valid_rows:
                application_eid = str(row["application_eid"]).strip()
                protect_status = application_eid in protected_eids

                update_columns = [
                    column
                    for column in CANDIDATE_WRITE_COLUMNS
                    if not (protect_status and column == "benivo_status")
                ]

                update_sql = "UPDATE benivo.candidates SET " + ", ".join(
                    f"{column} = %s" for column in update_columns
                ) + ", updated_at = NOW() WHERE application_eid = %s"

                update_values = [_prepare_value(column, row.get(column)) for column in update_columns]
                update_values.append(application_eid)

                cur.execute(update_sql, update_values)

                if cur.rowcount > 0:
                    result["updated"] += 1
                    continue

                insert_columns = ["application_eid"] + CANDIDATE_WRITE_COLUMNS
                insert_sql = (
                    "INSERT INTO benivo.candidates ("
                    + ", ".join(insert_columns)
                    + ", created_at, updated_at) VALUES ("
                    + ", ".join(["%s"] * len(insert_columns))
                    + ", NOW(), NOW())"
                )
                insert_values = [application_eid] + [
                    _prepare_value(column, row.get(column)) for column in CANDIDATE_WRITE_COLUMNS
                ]

                cur.execute(insert_sql, insert_values)
                result["inserted"] += 1

        conn.commit()
    except psycopg2.Error as exc:
        conn.rollback()
        raise RuntimeError(f"benivo.candidates upsert failed, batch rolled back: {exc}") from exc
    finally:
        conn.close()

    return result
