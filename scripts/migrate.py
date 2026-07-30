#!/usr/bin/env python
"""
Safe, forward-only migration runner.

Tracks applied migrations in benivo._schema_migrations (created on first
use, idempotent CREATE TABLE IF NOT EXISTS). Every file in migrations/ is
itself written to be idempotent (IF NOT EXISTS throughout) as a second
safety net, but this runner still only ever applies a given filename once
it's recorded, in filename order, and never rewrites or re-executes a
migration already tracked.

NOTE: migrations 0001-0004 were already applied to the live database
directly (each explicitly confirmed at the time, before this runner
existed). The first time this runner is used, its tracking table will be
empty, so it will see 0001-0004 as "pending" and re-run them -- this is
safe and expected, because every one of those files uses IF NOT EXISTS /
DROP INDEX IF EXISTS, so re-running them is a no-op. From that point on,
the tracking table accurately reflects what's been applied.

Usage:
    python scripts/migrate.py --check    # list applied/pending migrations, apply nothing
    python scripts/migrate.py --apply    # apply every pending migration, in filename order
"""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import config  # noqa: E402
from app.clients.database_client import get_connection  # noqa: E402
from app.logging_config import configure_logging  # noqa: E402

MIGRATIONS_DIR = Path(__file__).resolve().parent.parent / "migrations"

_ENSURE_TRACKING_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS benivo._schema_migrations (
    filename TEXT PRIMARY KEY,
    applied_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
"""


def _migration_files():
    return sorted(MIGRATIONS_DIR.glob("*.sql"))


def _applied_filenames(cur):
    cur.execute("SELECT filename FROM benivo._schema_migrations")
    return {row[0] for row in cur.fetchall()}


def check() -> int:
    conn = get_connection(autocommit=True)
    try:
        cur = conn.cursor()
        cur.execute(_ENSURE_TRACKING_TABLE_SQL)
        applied = _applied_filenames(cur)
        cur.close()
    finally:
        conn.close()

    pending = [f.name for f in _migration_files() if f.name not in applied]

    print(f"Applied ({len(applied)}): {sorted(applied)}")
    print(f"Pending ({len(pending)}): {pending}")
    return 0


def apply_migrations() -> int:
    conn = get_connection(autocommit=False)
    try:
        cur = conn.cursor()
        cur.execute(_ENSURE_TRACKING_TABLE_SQL)
        conn.commit()
        applied = _applied_filenames(cur)

        for path in _migration_files():
            if path.name in applied:
                print(f"Skipping already-applied: {path.name}")
                continue

            print(f"Applying: {path.name}")
            sql = path.read_text(encoding="utf-8")

            try:
                cur.execute(sql)
                cur.execute("INSERT INTO benivo._schema_migrations (filename) VALUES (%s)", (path.name,))
                conn.commit()
                print(f"Applied: {path.name}")
            except Exception:
                conn.rollback()
                print(f"FAILED applying {path.name}; rolled back. Stopping -- fix the file before retrying.")
                raise

        cur.close()
    finally:
        conn.close()

    return 0


def main(argv=None) -> int:
    configure_logging()

    try:
        config.validate()
    except RuntimeError as exc:
        print(f"Configuration error: {exc}")
        return 1

    parser = argparse.ArgumentParser(description="Safe, forward-only migration runner.")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--check", action="store_true", help="List applied/pending migrations; apply nothing.")
    group.add_argument("--apply", action="store_true", help="Apply every pending migration, in filename order.")

    args = parser.parse_args(argv if argv is not None else sys.argv[1:])

    return check() if args.check else apply_migrations()


if __name__ == "__main__":
    sys.exit(main())
