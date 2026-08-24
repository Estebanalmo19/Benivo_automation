"""Read/write access to benivo.post_log -- the permanent, append-only audit trail."""

from typing import Any, Dict, List, Optional, Set

import psycopg2.extras

from app.clients.database_client import db_cursor
from app.models.domain import ACTION_CREATE_USER, TERMINAL_POST_LOG_STATUSES


def get_terminal_post_log_application_eids() -> Set[str]:
    """
    application_eids with a SUCCESS or ALREADY_EXISTS CREATE_USER row --
    never posted again. Scoped to action=CREATE_USER (not just status) so
    a Case-update audit row (action=UPDATE_CASE, see migrations/0006) is
    never mistaken for "already posted" -- postability is decided by the
    create-user outcome only.
    """
    query = """
        SELECT DISTINCT application_eid
        FROM benivo.post_log
        WHERE action = %(action)s
          AND status = ANY(%(terminal_statuses)s)
    """

    with db_cursor() as cur:
        cur.execute(query, {"action": ACTION_CREATE_USER, "terminal_statuses": list(TERMINAL_POST_LOG_STATUSES)})
        return {row["application_eid"] for row in cur.fetchall() if row["application_eid"]}


def get_post_log_rows_for_run(run_id: str) -> List[Dict[str, Any]]:
    """
    Every post_log row (both CREATE_USER and UPDATE_CASE) written during
    one specific run -- the direct, durable source for the reporting
    "Posting Results" sheet. Built straight from the audit trail, not from
    any in-memory run-result list, so it can never diverge from what was
    actually persisted. A dry run never writes rows under any run_id, so
    this naturally returns [] for a dry-run report.
    """
    query = """
        SELECT application_eid, candidate_eid, email, action, status,
               benivo_user_id, benivo_assignment_id, error_message, posted_at
        FROM benivo.post_log
        WHERE run_id = %(run_id)s
        ORDER BY application_eid, posted_at
    """

    with db_cursor() as cur:
        cur.execute(query, {"run_id": run_id})
        return [dict(row) for row in cur.fetchall()]


def insert_post_log_row(cur, row: Dict[str, Any]) -> None:
    """
    Runs on a cursor already inside a transaction -- caller owns the
    commit/rollback boundary (see posting_service.record_post_result, which
    calls this and candidate_repository.update_candidate_after_posting
    together in one transaction, per the confirmed rule that a post_log
    write and its candidate status update must never partially succeed).

    REQUIRES migrations/0009_add_http_status_code_to_post_log.sql to be
    applied first -- this INSERT references benivo.post_log.http_status_code,
    which does not exist until that migration runs. Do not deploy this
    version of the function before that migration is applied, or every
    post_log write (CREATE_USER and UPDATE_CASE alike) will fail.
    """
    cur.execute(
        """
        INSERT INTO benivo.post_log (
            run_id, application_eid, candidate_eid, email, action, status,
            is_vip, policy_name, policy_api_value,
            benivo_user_id, benivo_assignment_id, benivo_profile_url,
            request_payload, response_payload, error_message,
            execution_date, effective_start_date, start_date_source,
            http_status_code,
            posted_at
        )
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, NOW())
        """,
        (
            row["run_id"],
            row["application_eid"],
            row["candidate_eid"],
            row["email"],
            row["action"],
            row["status"],
            row["is_vip"],
            row["policy_name"],
            row["policy_api_value"],
            row["benivo_user_id"],
            row["benivo_assignment_id"],
            row["benivo_profile_url"],
            psycopg2.extras.Json(row["request_payload"]) if row["request_payload"] is not None else None,
            psycopg2.extras.Json(row["response_payload"]) if row["response_payload"] is not None else None,
            row["error_message"],
            row.get("execution_date"),
            row.get("effective_start_date"),
            row.get("start_date_source"),
            row.get("http_status_code"),
        ),
    )
