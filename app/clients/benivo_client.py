"""Raw Benivo API calls: authentication, reference data, user lookup, user creation.

No business logic here (no office/policy resolution, no candidate selection)
-- see app/services for that. Generalized from the original
create_user_benivo.py, which remains untouched in legacy/ as a standalone
single-candidate test script.
"""

import json
from typing import Any, Dict, Optional

import requests

from app import config

# No Benivo URL is ever hardcoded here -- every endpoint is environment-driven
# (see app.config, which fails fast at startup via config.validate() if any
# is missing) so switching the whole app from UAT to Production is only a
# .env change, never a code change.
TOKEN_URL = config.BENIVO_TOKEN_URL
REFDATA_URL = config.BENIVO_REFDATA_URL
CREATE_USER_URL = config.BENIVO_CREATE_USER_URL
USER_ASSIGNMENT_URL = config.BENIVO_USER_LOOKUP_URL
CASE_URL = config.BENIVO_CASE_URL

REQUEST_TIMEOUT_SECONDS = 30


def _get_headers(access_token: str) -> Dict[str, str]:
    return {
        "Authorization": f"Bearer {access_token}",
        "Accept": "application/json",
        "Content-Type": "application/json",
        "User-Agent": "PostmanRuntime/7.43.0",
    }


def get_access_token() -> str:
    headers = {
        "Accept": "application/json",
        "Content-Type": "application/x-www-form-urlencoded",
        "User-Agent": "PostmanRuntime/7.43.0",
    }
    data = {
        "grant_type": config.BENIVO_GRANT_TYPE,
        "client_id": config.BENIVO_CLIENT_ID,
        "client_secret": config.BENIVO_CLIENT_SECRET,
    }

    response = requests.post(TOKEN_URL, headers=headers, data=data, timeout=REQUEST_TIMEOUT_SECONDS)

    if response.status_code != 200:
        raise RuntimeError(f"Failed to obtain Benivo token: {response.status_code} - {response.text[:500]}")

    access_token = response.json().get("access_token")

    if not access_token:
        raise RuntimeError("Benivo token response did not include access_token.")

    return access_token


def get_refdata(access_token: str) -> Dict[str, Any]:
    response = requests.get(REFDATA_URL, headers=_get_headers(access_token), timeout=REQUEST_TIMEOUT_SECONDS)

    if response.status_code != 200:
        raise RuntimeError(f"Failed to fetch Benivo refdata: {response.status_code} - {response.text[:500]}")

    payload = response.json()

    if payload.get("hasError") is True:
        raise RuntimeError(f"Benivo refdata returned an error: {payload}")

    return payload.get("data", {})


def find_user_by_email(access_token: str, email: str) -> Dict[str, Any]:
    response = requests.post(
        USER_ASSIGNMENT_URL,
        headers=_get_headers(access_token),
        json={"email": email},
        timeout=REQUEST_TIMEOUT_SECONDS,
    )

    body = response.json() if response.content else {}
    found = False
    benivo_user_id = None
    benivo_assignment_id = None

    if response.status_code == 200 and body.get("hasError") is not True:
        data = body.get("data") or {}
        user = data.get("user") if isinstance(data, dict) else None

        if isinstance(user, dict) and (user.get("email") or "").lower() == email.lower():
            found = True
            benivo_user_id = user.get("benivoId") or user.get("benivoID") or user.get("BenivoID")

        assignments = data.get("assignments") if isinstance(data, dict) else None

        if isinstance(assignments, list) and assignments:
            first_assignment = assignments[0]

            if isinstance(first_assignment, dict):
                benivo_assignment_id = first_assignment.get("assignmentId") or first_assignment.get("assignmentID")

    return {
        "found": found,
        "status_code": response.status_code,
        "benivo_user_id": benivo_user_id,
        "benivo_assignment_id": benivo_assignment_id,
        "raw_response": body,
    }


def create_user(access_token: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    response = requests.post(
        CREATE_USER_URL,
        headers=_get_headers(access_token),
        json=[payload],
        timeout=REQUEST_TIMEOUT_SECONDS,
    )

    body = response.json() if response.content else {}

    result = {"success": False, "status_code": response.status_code, "created": None, "raw_response": body, "error": None}

    if response.status_code != 200 or body.get("hasError") is True:
        result["error"] = json.dumps(body, ensure_ascii=False)
        return result

    created_rows = body.get("data")

    if not isinstance(created_rows, list) or not created_rows:
        result["error"] = "Create response did not include data."
        return result

    created = created_rows[0]
    missing = [field for field in ("benivoId", "assignmentId", "email") if field not in created]

    if missing:
        result["error"] = f"Create response missing fields: {missing}"
        return result

    result["success"] = True
    result["created"] = created
    return result


def update_case(access_token: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    """
    PATCH /clients/v1/Case. Confirmed 2026-08-10 by Gina (Benivo): the
    assignmentId from create-user IS the caseId this endpoint requires.
    Confirmed 2026-08-19 by Gina: the body must be a findBy/data envelope
    ({"findBy": {"caseId": ...}, "data": {"hostJobRole": ..., "homeLocation":
    {"country": ...}}}), not a flat body -- see
    posting_service.build_case_update_payload(), which builds the exact
    shape. This function sends payload as-is with no reshaping.

    Fixed 2026-08-21 after a real UAT PATCH was misreported as FAILED with
    an empty response body: this previously required response.status_code
    to be exactly 200, rejecting every other 2xx (201/202/204/...) as a
    failure even when transport succeeded and Benivo returned no error --
    e.g. 204 No Content, a normal, bodyless PATCH-success convention, was
    treated identically to a real server error. Any 2xx is now transport
    success unless the response body explicitly says hasError=true.
    status_code is always the real HTTP status Benivo returned, so a
    genuine failure (4xx/5xx) is never confused with the 204-no-body case
    -- both share raw_response={} but are distinguished by status_code and
    success.
    """
    response = requests.patch(
        CASE_URL,
        headers=_get_headers(access_token),
        json=payload,
        timeout=REQUEST_TIMEOUT_SECONDS,
    )

    status_code = response.status_code
    body = response.json() if response.content else {}

    result = {"success": False, "status_code": status_code, "raw_response": body, "error": None}

    if not (200 <= status_code < 300) or body.get("hasError") is True:
        result["error"] = json.dumps(body, ensure_ascii=False)
        return result

    result["success"] = True
    return result
