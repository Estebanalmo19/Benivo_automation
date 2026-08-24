"""Centralized environment-variable parsing and validation.

Reading env vars here is safe at import time (never raises) so unit tests
can import service modules freely without a configured environment. Actual
validation only happens when validate() is called explicitly -- main.py
calls it once at startup and exits clearly if required configuration is
missing. Never log the values of secret settings.

Required settings (DB_*, BENIVO_CLIENT_*) are read once as module-level
constants -- they should never change during a run. Execution-behavior
settings (BENIVO_DRY_RUN, BENIVO_MAX_CANDIDATES, etc.) are exposed as
functions that re-read the environment on every call, matching how they
always worked: this is what lets ops flip BENIVO_DRY_RUN for a single
invocation without restarting anything, and it's also what tests rely on
via monkeypatch.setenv() + calling the function again.
"""

import os
from datetime import datetime
from typing import List, Optional

from dotenv import load_dotenv

from app.utils.helpers import env_flag, env_positive_int

load_dotenv()

# --- Database (required, static) ---------------------------------------------
DB_HOST = os.getenv("DB_HOST")
DB_PORT = os.getenv("DB_PORT")
DB_NAME = os.getenv("DB_NAME")
DB_USER = os.getenv("DB_USER")
DB_PASSWORD = os.getenv("DB_PASSWORD")

# --- Benivo API (required, static) --------------------------------------------
BENIVO_CLIENT_ID = os.getenv("BENIVO_CLIENT_ID")
BENIVO_CLIENT_SECRET = os.getenv("BENIVO_CLIENT_SECRET")
BENIVO_GRANT_TYPE = os.getenv("BENIVO_GRANT_TYPE")

# --- Benivo API endpoints (required, static) -----------------------------------
# Every Benivo HTTP endpoint the app calls is environment-driven -- see
# app/clients/benivo_client.py, which references these exclusively and never
# hardcodes a URL. Switching the whole app from UAT to Production is
# intended to be ONLY a matter of setting these five values (plus
# BENIVO_CLIENT_ID/SECRET) in .env; no Python source code should need to
# change. Production values are not yet confirmed as of 2026-08-21 -- see
# .env.example, which documents the current confirmed UAT values only.
BENIVO_TOKEN_URL = os.getenv("BENIVO_TOKEN_URL")
BENIVO_REFDATA_URL = os.getenv("BENIVO_REFDATA_URL")
BENIVO_CREATE_USER_URL = os.getenv("BENIVO_CREATE_USER_URL")
BENIVO_USER_LOOKUP_URL = os.getenv("BENIVO_USER_LOOKUP_URL")
BENIVO_CASE_URL = os.getenv("BENIVO_CASE_URL")

REQUIRED_SETTINGS = {
    "DB_HOST": DB_HOST,
    "DB_PORT": DB_PORT,
    "DB_NAME": DB_NAME,
    "DB_USER": DB_USER,
    "DB_PASSWORD": DB_PASSWORD,
    "BENIVO_CLIENT_ID": BENIVO_CLIENT_ID,
    "BENIVO_CLIENT_SECRET": BENIVO_CLIENT_SECRET,
    "BENIVO_GRANT_TYPE": BENIVO_GRANT_TYPE,
    "BENIVO_TOKEN_URL": BENIVO_TOKEN_URL,
    "BENIVO_REFDATA_URL": BENIVO_REFDATA_URL,
    "BENIVO_CREATE_USER_URL": BENIVO_CREATE_USER_URL,
    "BENIVO_USER_LOOKUP_URL": BENIVO_USER_LOOKUP_URL,
    "BENIVO_CASE_URL": BENIVO_CASE_URL,
}


def missing_required_settings() -> List[str]:
    return [name for name, value in REQUIRED_SETTINGS.items() if not value]


def validate() -> None:
    """Call once at process startup. Raises RuntimeError naming exactly which settings are missing."""
    missing = missing_required_settings()

    if missing:
        raise RuntimeError(
            "Missing required configuration: " + ", ".join(missing) + ". "
            "Set these in your .env file (see .env.example) before running."
        )

    if BENIVO_GRANT_TYPE != "client_credentials":
        raise RuntimeError("BENIVO_GRANT_TYPE must be exactly 'client_credentials'.")


# --- Execution behavior (optional, dynamic -- read fresh every call) ---------

def is_dry_run() -> bool:
    return env_flag("BENIVO_DRY_RUN", default=True)


def max_candidates(default: int = 1) -> int:
    return env_positive_int("BENIVO_MAX_CANDIDATES", default=default)


def allow_reference_data_calls() -> bool:
    return env_flag("BENIVO_ALLOW_REFERENCE_DATA_CALLS", default=False)


def uat_application_eid() -> Optional[str]:
    raw = os.getenv("BENIVO_UAT_APPLICATION_EID")
    return raw.strip() if raw and raw.strip() else None


def go_live_at() -> Optional[datetime]:
    """
    Production go-live cutover, ISO 8601 (e.g. "2026-09-01T00:00:00Z").
    Unset by default -- while unset, candidate_repository.get_ready_candidates()
    applies no go-live filtering at all (today's exact behavior, nothing
    changes until this is deliberately set).

    Set this ONCE, at the actual go-live moment, and only AFTER the
    benivo.scope_history baseline backfill (see
    scripts/backfill_scope_history.py) has already been run -- otherwise
    every pre-existing backlog candidate would have no scope_history row
    yet and would be excluded from auto-posting forever, never becoming
    "Automatically Eligible" even after go-live, since a candidate is only
    ever inserted into scope_history once (see synchronization_service.py).
    A malformed value raises immediately rather than silently disabling the
    gate -- see app.config's own "fail clearly, never guess" convention.
    """
    raw = os.getenv("BENIVO_GO_LIVE_AT")

    if not raw or not raw.strip():
        return None

    return datetime.fromisoformat(raw.strip().replace("Z", "+00:00"))


def legacy_max_candidates(default: int = 1) -> int:
    """Fallback default for candidate_repository.get_ready_candidates() when no explicit limit is passed."""
    return env_positive_int("MAX_CANDIDATES", default=default)


def report_folder() -> Optional[str]:
    return os.getenv("REPORT_FOLDER") or None


def report_delivery_enabled() -> bool:
    return env_flag("BENIVO_REPORT_DELIVERY_ENABLED", default=False)


def report_webhook_url() -> Optional[str]:
    """The signed Power Automate trigger URL. Never log or return this value -- see report_delivery_service.py."""
    raw = os.getenv("BENIVO_REPORT_WEBHOOK_URL")
    return raw.strip() if raw and raw.strip() else None


def log_level() -> str:
    return os.getenv("LOG_LEVEL", "INFO").strip().upper() or "INFO"
