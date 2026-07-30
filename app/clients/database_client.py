"""PostgreSQL connection primitives. No business queries here -- see app/repositories."""

from contextlib import contextmanager

import psycopg2
import psycopg2.extras

from app import config


def get_connection(autocommit: bool = True):
    conn = psycopg2.connect(
        host=config.DB_HOST,
        port=config.DB_PORT,
        dbname=config.DB_NAME,
        user=config.DB_USER,
        password=config.DB_PASSWORD,
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
