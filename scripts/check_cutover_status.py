#!/usr/bin/env python
"""
Read-only, no-I/O status check of the current cutover-relevant configuration
-- Change 4 of the UAT cutover safeguards (confirmed 2026-09-08). Meant to
be run by an operator immediately before any cutover step (T0 capture,
GO_LIVE_AT activation, canary posting) to confirm the environment is in the
expected state without ever exposing a secret.

Performs ZERO HTTP calls and ZERO database operations -- it only reads
already-loaded environment variables (via `from app import config`, which
triggers the same load_dotenv() every other entry point relies on) and
prints a small, fixed set of non-secret facts about them.

Prints ONLY:
  - BENIVO_DRY_RUN / BENIVO_GO_LIVE_AT / BENIVO_APPROVED_BATCH_FILE /
    BENIVO_MAX_CANDIDATES / BENIVO_REPORT_DELIVERY_ENABLED presence/value
    (GO_LIVE_AT and APPROVED_BATCH_FILE as set/unset only -- never the
    actual timestamp or file path, even though neither is itself a secret,
    simply because this script's contract is presence-only for those two)
  - a UAT/PROD/UNKNOWN classification of each of the five Benivo endpoint
    hostnames

NEVER prints: the full webhook URL, any client secret, any password, any
token, any DB connection string, or any endpoint URL's full value (only
its hostname is ever inspected, and even that is never printed directly --
only the classification result is).

Endpoint classification is deliberately conservative: "UAT" is returned
only when the hostname contains the confirmed literal marker
"uat.benivo.com". This codebase has no confirmed production Benivo
hostname pattern anywhere (see docs/PHASE1_ARCHITECTURE.md: "Production
values are not yet confirmed") -- so this classifier NEVER returns "PROD".
Anything that isn't the confirmed UAT marker is reported "UNKNOWN" rather
than guessed.

Usage:
    python scripts/check_cutover_status.py
"""

import sys
from pathlib import Path
from typing import List, Optional
from urllib.parse import urlparse

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import os  # noqa: E402

from app import config  # noqa: E402  (import alone triggers load_dotenv())

CONFIRMED_UAT_HOST_MARKER = "uat.benivo.com"

TRI_STATE_TRUE_VALUES = {"true", "1", "yes"}
TRI_STATE_FALSE_VALUES = {"false", "0", "no"}


def tri_state(var_name: str) -> str:
    """'true' / 'false' / the raw value if unrecognized / 'unset'. Never guessed."""
    raw = os.getenv(var_name)

    if raw is None or not raw.strip():
        return "unset"

    normalized = raw.strip().lower()

    if normalized in TRI_STATE_TRUE_VALUES:
        return "true"

    if normalized in TRI_STATE_FALSE_VALUES:
        return "false"

    return raw.strip()


def presence(var_name: str) -> str:
    """'set' / 'unset' only -- never the value."""
    raw = os.getenv(var_name)
    return "set" if raw and raw.strip() else "unset"


def value_or_unset(var_name: str) -> str:
    raw = os.getenv(var_name)
    return raw.strip() if raw and raw.strip() else "unset"


def classify_endpoint(url: Optional[str]) -> str:
    if not url:
        return "UNKNOWN"

    try:
        hostname = urlparse(url).hostname or ""
    except ValueError:
        return "UNKNOWN"

    return "UAT" if CONFIRMED_UAT_HOST_MARKER in hostname.lower() else "UNKNOWN"


def main(argv: Optional[List[str]] = None) -> int:
    print(f"BENIVO_DRY_RUN = {tri_state('BENIVO_DRY_RUN')}")
    print(f"BENIVO_GO_LIVE_AT = {presence('BENIVO_GO_LIVE_AT')}")
    print(f"BENIVO_APPROVED_BATCH_FILE = {presence('BENIVO_APPROVED_BATCH_FILE')}")
    print(f"BENIVO_MAX_CANDIDATES = {value_or_unset('BENIVO_MAX_CANDIDATES')}")
    print(f"BENIVO_REPORT_DELIVERY_ENABLED = {tri_state('BENIVO_REPORT_DELIVERY_ENABLED')}")

    print(f"TOKEN_ENDPOINT_ENV = {classify_endpoint(config.BENIVO_TOKEN_URL)}")
    print(f"REFDATA_ENDPOINT_ENV = {classify_endpoint(config.BENIVO_REFDATA_URL)}")
    print(f"CREATE_USER_ENDPOINT_ENV = {classify_endpoint(config.BENIVO_CREATE_USER_URL)}")
    print(f"USER_LOOKUP_ENDPOINT_ENV = {classify_endpoint(config.BENIVO_USER_LOOKUP_URL)}")
    print(f"CASE_ENDPOINT_ENV = {classify_endpoint(config.BENIVO_CASE_URL)}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
