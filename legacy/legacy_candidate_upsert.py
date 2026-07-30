"""LEGACY, UNUSED by the active pipeline.

This is the original Jobvite-REST-API-based upsert path (used only by
legacy/pull_candidates_jv.py), superseded by
app.services.synchronization_service, which syncs directly from
jv_arrise_data_schema.jobvite_applications in Postgres instead of calling
the Jobvite API. Kept, not deleted, per explicit instruction. Do not wire
this into the active app/ pipeline.
"""

import sys
from pathlib import Path
from typing import Any, Dict, List, Set

import psycopg2
import psycopg2.extras

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.clients.database_client import db_cursor, get_connection

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

    Update-then-insert strategy: application_eid was not confirmed to have a
    UNIQUE constraint when this was written (it since has been, see
    migrations, but this legacy path was never updated to use ON CONFLICT
    since it's unused). Runs as a single transaction: any database error
    rolls back the whole batch. Rows already marked SUCCESS in
    benivo.post_log keep their existing benivo_status.
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
