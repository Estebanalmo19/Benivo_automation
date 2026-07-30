"""Read/write access to benivo.post_log -- the permanent, append-only audit trail."""

from typing import Any, Dict, Optional, Set

import psycopg2.extras

from app.clients.database_client import db_cursor
from app.models.domain import TERMINAL_POST_LOG_STATUSES


def get_terminal_post_log_application_eids() -> Set[str]:
    """application_eids with a SUCCESS or ALREADY_EXISTS row -- never posted again."""
    query = """
        SELECT DISTINCT application_eid
        FROM benivo.post_log
        WHERE status = ANY(%(terminal_statuses)s)
    """

    with db_cursor() as cur:
        cur.execute(query, {"terminal_statuses": list(TERMINAL_POST_LOG_STATUSES)})
        return {row["application_eid"] for row in cur.fetchall() if row["application_eid"]}


def insert_post_log_row(cur, row: Dict[str, Any]) -> None:
    """
    Runs on a cursor already inside a transaction -- caller owns the
    commit/rollback boundary (see posting_service.record_post_result, which
    calls this and candidate_repository.update_candidate_after_posting
    together in one transaction, per the confirmed rule that a post_log
    write and its candidate status update must never partially succeed).
    """
    cur.execute(
        """
        INSERT INTO benivo.post_log (
            run_id, application_eid, candidate_eid, email, action, status,
            is_vip, policy_name, policy_api_value,
            benivo_user_id, benivo_assignment_id, benivo_profile_url,
            request_payload, response_payload, error_message, posted_at
        )
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, NOW())
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
        ),
    )
