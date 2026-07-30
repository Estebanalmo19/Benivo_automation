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

REQUIRED_SETTINGS = {
    "DB_HOST": DB_HOST,
    "DB_PORT": DB_PORT,
    "DB_NAME": DB_NAME,
    "DB_USER": DB_USER,
    "DB_PASSWORD": DB_PASSWORD,
    "BENIVO_CLIENT_ID": BENIVO_CLIENT_ID,
    "BENIVO_CLIENT_SECRET": BENIVO_CLIENT_SECRET,
    "BENIVO_GRANT_TYPE": BENIVO_GRANT_TYPE,
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


def legacy_max_candidates(default: int = 1) -> int:
    """Fallback default for candidate_repository.get_ready_candidates() when no explicit limit is passed."""
    return env_positive_int("MAX_CANDIDATES", default=default)


def report_folder() -> Optional[str]:
    return os.getenv("REPORT_FOLDER") or None


def log_level() -> str:
    return os.getenv("LOG_LEVEL", "INFO").strip().upper() or "INFO"
