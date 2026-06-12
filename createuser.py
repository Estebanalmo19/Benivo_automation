import json
import os
import sys
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import requests
from dotenv import load_dotenv


load_dotenv()


# ============================================================
# SOLO VIENEN DEL .env ESTAS 3 VARIABLES
# ============================================================
CLIENT_ID = os.getenv("BENIVO_CLIENT_ID")
CLIENT_SECRET = os.getenv("BENIVO_CLIENT_SECRET")
GRANT_TYPE = os.getenv("BENIVO_GRANT_TYPE")
# ============================================================


# URLs FIJAS PARA UAT
TOKEN_URL = "https://externalapi.uat.benivo.com/idm/v1/Token/OAuth2"
REFDATA_URL = "https://hubapi.uat.benivo.com/v3/api/user/refdata"
CREATE_USER_URL = "https://hubapi.uat.benivo.com/v3/api/user/create"
USER_ASSIGNMENT_URL = "https://hubapi.uat.benivo.com/v3/api/user/userassignment"

# Valores de prueba
PREFERRED_POLICY = "Tier 1"
PREFERRED_OFFICE_NAME = "Colombia (Live Casino)"
TEST_EMAIL_DOMAIN = "arrise.com"


def fail(message: str, response: Optional[requests.Response] = None) -> None:
    print(f"\n❌ ERROR: {message}")

    if response is not None:
        print(f"Status code: {response.status_code}")
        try:
            print(json.dumps(response.json(), indent=2, ensure_ascii=False))
        except Exception:
            print(response.text)

    sys.exit(1)


def pretty(title: str, data: Any) -> None:
    print(f"\n========== {title} ==========")
    print(json.dumps(data, indent=2, ensure_ascii=False))


def validate_env() -> None:
    missing = []

    if not CLIENT_ID:
        missing.append("BENIVO_CLIENT_ID")

    if not CLIENT_SECRET:
        missing.append("BENIVO_CLIENT_SECRET")

    if not GRANT_TYPE:
        missing.append("BENIVO_GRANT_TYPE")

    if missing:
        fail(f"Faltan variables en .env: {', '.join(missing)}")

    if GRANT_TYPE != "client_credentials":
        fail("BENIVO_GRANT_TYPE debe ser exactamente: client_credentials")


def get_access_token() -> str:
    print("1) Generando token...")

    headers = {
    "Accept": "application/json",
    "Content-Type": "application/x-www-form-urlencoded",
    "User-Agent": "PostmanRuntime/7.43.0",
    }

    data = {
        "grant_type": GRANT_TYPE,
        "client_id": CLIENT_ID,
        "client_secret": CLIENT_SECRET,
    }

    response = requests.post(
        TOKEN_URL,
        headers=headers,
        data=data,
        timeout=30,
    )

    if response.status_code != 200:
        fail("No se pudo generar el token.", response)

    payload = response.json()
    access_token = payload.get("access_token")

    if not access_token:
        fail("La respuesta del token no trajo access_token.", response)

    print("✅ Token generado correctamente.")
    return access_token


def get_headers(access_token: str, json_content: bool = True) -> Dict[str, str]:
    headers = {
        "Authorization": f"Bearer {access_token}",
        "Accept": "application/json",
        "User-Agent": "PostmanRuntime/7.43.0",
    }
    if json_content:
        headers["Content-Type"] = "application/json"
    return headers


def get_refdata(access_token: str) -> Dict[str, Any]:
    print("2) Consultando refdata...")

    response = requests.get(
        REFDATA_URL,
        headers=get_headers(access_token, json_content=False),
        timeout=30,
    )

    if response.status_code != 200:
        fail("No se pudo consultar refdata.", response)

    payload = response.json()

    if payload.get("hasError") is True:
        fail("Benivo respondió error en refdata.", response)

    refdata = payload.get("data")

    if not isinstance(refdata, dict):
        fail("La respuesta de refdata no trae data válida.", response)

    print("✅ Refdata OK.")
    return refdata


def select_policy(refdata: Dict[str, Any]) -> str:
    policies = refdata.get("policies")

    if not isinstance(policies, list) or not policies:
        fail("No llegaron policies en refdata.")

    policy_names = [
        item.get("policy")
        for item in policies
        if isinstance(item, dict) and item.get("policy")
    ]

    if not policy_names:
        fail("No encontré nombres de policy en refdata.")

    if PREFERRED_POLICY in policy_names:
        return PREFERRED_POLICY

    print(f"⚠️ No encontré '{PREFERRED_POLICY}'. Usaré '{policy_names[0]}'.")
    return policy_names[0]


def select_office_id(refdata: Dict[str, Any]) -> str:
    offices = refdata.get("offices")

    if not isinstance(offices, list) or not offices:
        fail("No llegaron offices en refdata.")

    for office in offices:
        if office.get("officeName") == PREFERRED_OFFICE_NAME:
            return office["id"]

    first_office = offices[0]

    print(
        f"⚠️ No encontré '{PREFERRED_OFFICE_NAME}'. "
        f"Usaré '{first_office.get('officeName')}'."
    )

    return first_office["id"]


def build_test_user(policy: str, office_id: str) -> Dict[str, Any]:
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")

    return {
        "firstName": "Esteban",
        "lastName": "test",
        "email": "esteban.alvarez@arrise.com",
        "policy": policy,
        "officeId": office_id,
        "startDateOfAssignment": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }


def create_user(access_token: str, user_payload: Dict[str, Any]) -> Dict[str, Any]:
    print("3) Creando usuario de prueba...")

    response = requests.post(
        CREATE_USER_URL,
        headers=get_headers(access_token),
        json=[user_payload],
        timeout=30,
    )

    if response.status_code != 200:
        fail("No se pudo crear el usuario.", response)

    payload = response.json()

    if payload.get("hasError") is True:
        fail("Benivo respondió error creando usuario.", response)

    created_rows = payload.get("data")

    if not isinstance(created_rows, list) or not created_rows:
        fail("La respuesta de creación no trajo data.", response)

    created = created_rows[0]

    for field in ["benivoId", "assignmentId", "email"]:
        if field not in created:
            fail(f"La respuesta de creación no trajo el campo: {field}", response)

    print("✅ Usuario creado correctamente.")
    return created


def get_user_assignment(
    access_token: str,
    benivo_id: int,
    assignment_id: int,
    email: str,
) -> Dict[str, Any]:
    print("4) Consultando userassignment...")

    payload = [
        {
            "benivoId": benivo_id,
            "assignmentId": assignment_id,
            "email": email,
        }
    ]

    response = requests.post(
        USER_ASSIGNMENT_URL,
        headers=get_headers(access_token),
        json=payload,
        timeout=30,
    )

    if response.status_code != 200:
        fail("No se pudo consultar userassignment.", response)

    result = response.json()

    if result.get("hasError") is True:
        fail("Benivo respondió error en userassignment.", response)

    print("✅ Userassignment OK.")
    return result


def extract_user_email(response_payload: Dict[str, Any]) -> Optional[str]:
    data = response_payload.get("data")

    if not isinstance(data, dict):
        return None

    user = data.get("user")

    if not isinstance(user, dict):
        return None

    return user.get("email")


def extract_assignment_ids(response_payload: Dict[str, Any]) -> List[int]:
    data = response_payload.get("data")

    if not isinstance(data, dict):
        return []

    assignments = data.get("assignments")

    if not isinstance(assignments, list):
        return []

    ids = []

    for assignment in assignments:
        if not isinstance(assignment, dict):
            continue

        assignment_id = assignment.get("assignmentId") or assignment.get("assignmentID")

        if assignment_id is None:
            continue

        try:
            ids.append(int(assignment_id))
        except Exception:
            pass

    return ids


def validate_created_user(
    original_payload: Dict[str, Any],
    created_response: Dict[str, Any],
    user_assignment_response: Dict[str, Any],
) -> None:
    print("5) Validando creación...")

    expected_email = original_payload["email"].lower()
    created_email = str(created_response["email"]).lower()
    created_benivo_id = int(created_response["benivoId"])
    created_assignment_id = int(created_response["assignmentId"])

    fetched_email = extract_user_email(user_assignment_response)
    fetched_assignment_ids = extract_assignment_ids(user_assignment_response)

    errors = []

    if created_email != expected_email:
        errors.append(
            f"Email creado no coincide. expected={expected_email}, created={created_email}"
        )

    if fetched_email and fetched_email.lower() != expected_email:
        errors.append(
            f"Email consultado no coincide. expected={expected_email}, fetched={fetched_email}"
        )

    if fetched_assignment_ids and created_assignment_id not in fetched_assignment_ids:
        errors.append(
            f"AssignmentId no aparece en consulta. "
            f"expected={created_assignment_id}, fetched={fetched_assignment_ids}"
        )

    if not fetched_email and not fetched_assignment_ids:
        errors.append(
            "La consulta userassignment respondió 200, pero no encontré user.email ni assignments[].assignmentId."
        )

    if errors:
        print("\n❌ VALIDACIÓN FALLÓ")
        for error in errors:
            print(f"- {error}")
        sys.exit(1)

    print("\n✅ VALIDACIÓN OK")
    print("Usuario creado y confirmado correctamente:")
    print(f"email: {original_payload['email']}")
    print(f"benivoId: {created_benivo_id}")
    print(f"assignmentId: {created_assignment_id}")


def main() -> None:
    validate_env()

    access_token = get_access_token()

    refdata = get_refdata(access_token)

    policy = select_policy(refdata)
    office_id = select_office_id(refdata)

    print(f"Policy seleccionada: {policy}")
    print(f"OfficeId seleccionado: {office_id}")

    user_payload = build_test_user(policy, office_id)

    pretty("PAYLOAD CREATE USER", [user_payload])

    created = create_user(access_token, user_payload)

    pretty("RESPONSE CREATE USER", created)

    user_assignment = get_user_assignment(
        access_token=access_token,
        benivo_id=int(created["benivoId"]),
        assignment_id=int(created["assignmentId"]),
        email=created["email"],
    )

    pretty("RESPONSE USER ASSIGNMENT", user_assignment)

    validate_created_user(
        original_payload=user_payload,
        created_response=created,
        user_assignment_response=user_assignment,
    )


if __name__ == "__main__":
    main()