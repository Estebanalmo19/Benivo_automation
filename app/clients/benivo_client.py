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

TOKEN_URL = "https://externalapi.uat.benivo.com/idm/v1/Token/OAuth2"
REFDATA_URL = "https://hubapi.uat.benivo.com/v3/api/user/refdata"
CREATE_USER_URL = "https://hubapi.uat.benivo.com/v3/api/user/create"
USER_ASSIGNMENT_URL = "https://hubapi.uat.benivo.com/v3/api/user/userassignment"

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
