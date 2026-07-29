import os
from contextlib import contextmanager
from typing import Any, Dict, List, Optional

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

READY_CANDIDATE_FIELDS = """
    c.id, c.application_eid, c.candidate_eid, c.email, c.first_name, c.last_name,
    c.job_title, c.requisition_id, c.workplace, c.host_country, c.host_city,
    c.start_date, c.benivo_status, c.created_at, c.updated_at,
    c.phone_number, c.location, c.population, c.vip, c.gender,
    c.home_country, c.home_state_province, c.home_city, c.country_of_birth,
    c.citizenship, c.employee_id, c.billing_entity, c.host_legal_entity,
    c.host_business_unit
"""

MISSING_START_DATE_FIELDS = """
    c.id, c.application_eid, c.candidate_eid, c.email, c.first_name, c.last_name,
    c.job_title, c.requisition_id, c.workplace, c.host_country, c.host_city,
    c.start_date, c.benivo_status, c.created_at, c.updated_at
"""


def get_connection():
    conn = psycopg2.connect(
        host=DB_HOST,
        port=DB_PORT,
        dbname=DB_NAME,
        user=DB_USER,
        password=DB_PASSWORD,
    )
    conn.autocommit = True
    return conn


@contextmanager
def db_cursor():
    conn = get_connection()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            yield cur
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
                AND pl.status = 'SUCCESS'
          )
        ORDER BY c.created_at, c.id
        LIMIT %s
    """

    with db_cursor() as cur:
        cur.execute(query, (max_candidates,))
        return [dict(row) for row in cur.fetchall()]


def get_candidates_missing_start_date() -> List[Dict[str, Any]]:
    query = f"""
        SELECT {MISSING_START_DATE_FIELDS}
        FROM benivo.candidates c
        WHERE c.benivo_status = 'PENDING_MISSING_START_DATE'
          AND c.is_relocation_required = 'Yes'
        ORDER BY c.created_at, c.id
    """

    with db_cursor() as cur:
        cur.execute(query)
        return [dict(row) for row in cur.fetchall()]
