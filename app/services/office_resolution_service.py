"""Jobvite workplace -> Benivo office resolution: explicit mapping + live refdata lookup only. No fuzzy matching."""

import logging
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

# Explicit, centrally maintained Jobvite workplace -> Benivo officeName
# mapping. Keys are pre-normalized (see normalize_for_lookup): lowercased,
# single-spaced. Values are exact Benivo officeName strings, confirmed live
# against Benivo UAT refdata on 2026-07-29 (13 offices returned).
#
# officeId is intentionally NEVER stored here. It must always be looked up
# from the current environment's live refdata response at resolution time --
# UAT and production office UUIDs are expected to differ, so no UUID is
# hardcoded anywhere in this mapping or this module. Confirmed 2026-08-06 by
# official email from Benivo: the id refdata returns for each office is
# Benivo's own "PublicId" -- i.e. what this codebase has been sending as
# officeId in the create-user payload is exactly that PublicId, not a
# guessed or internal-only value.
WORKPLACE_TO_OFFICE_NAME = {
    "rak live casino": "UAE (Live Casino)",
    "serbia live casino": "Serbia (Live Casino)",
    "colombia live casino": "Colombia (Live Casino)",
    "bulgaria live casino": "Bulgaria (Live Casino)",
    "malta": "Malta (Global)",
    # Added 2026-08-10: traced why Romania/Latvia candidates were stuck in
    # PENDING_OFFICE_MAPPING -- not a spelling mismatch or missing source
    # data, these two workplace values simply had no entry here yet. Both
    # target officeNames were already present in OFFICE_NAME_TO_HOST_COUNTRY
    # (confirmed catalogue) and confirmed live in current UAT refdata:
    # "Romania (Live Casino)" -> b732127f-8a3a-4d99-bf0e-95c24fa69f55,
    # "Latvia (Global)" -> e908ab8d-0046-43f4-9ead-6a8dfb4b0876.
    "romania live casino": "Romania (Live Casino)",
    "latvia": "Latvia (Global)",
    # Added 2026-08-24: traced application_eid=penv7zwh stuck in
    # PENDING_OFFICE_MAPPING. Confirmed end-to-end -- not an inference from
    # candidate address/country: application.job.customField[fieldCode=
    # 'site'] is the literal, bare string "UAE" (not "RAK Live Casino"),
    # so it never matched the existing "rak live casino" key. Confirmed
    # live in current UAT refdata that "UAE (Live Casino)" exists
    # (ca081aff-d1f3-407a-afdd-adb836563d31), distinct from "UAE (Global)"
    # (1f607452-4bf2-4783-a3b5-40a923aced07) -- this maps the bare "UAE"
    # workplace value to the Live Casino office specifically, per confirmed
    # business intent, not the Global one.
    "uae": "UAE (Live Casino)",
}

# Explicit, reviewed Benivo officeName -> hostCountry mapping. Confirmed
# 2026-08-06 by official email from Benivo listing the full office
# catalogue (display name + PublicId) -- this is a business mapping over
# that confirmed catalogue, not inferred from any candidate data. Covers
# every office in the catalogue, including ones WORKPLACE_TO_OFFICE_NAME
# doesn't route to yet, since it's a property of the office itself.
#
# Deliberately NOT derived from the candidate's address/location fields
# (jv_arrise_data_schema country/city, benivo.candidates.host_country/
# host_city) -- those were investigated and found to have no confirmed
# Jobvite source (see synchronization_service.py comments). hostCountry
# here comes only from which Benivo office the candidate's workplace
# resolves to.
OFFICE_NAME_TO_HOST_COUNTRY = {
    "Serbia (Live Casino)": "Serbia",
    "Serbia (Global)": "Serbia",
    "UAE (Live Casino)": "UAE",
    "UAE (Global)": "UAE",
    "Bulgaria (Live Casino)": "Bulgaria",
    "Malta (Global)": "Malta",
    "Romania (Live Casino)": "Romania",
    "Romania (Global)": "Romania",
    "Colombia (Live Casino)": "Colombia",
    "Canada (Live Casino)": "Canada",
    "Brazil (Live Casino)": "Brazil",
    "Latvia (Global)": "Latvia",
    "Gibraltar (Head office)": "Gibraltar",
}


def normalize_for_lookup(value: Optional[str]) -> str:
    """Whitespace/casing normalization for lookup only -- never stored or sent to Benivo."""
    if not value:
        return ""
    return " ".join(value.strip().split()).lower()


def resolve_office_name(workplace: Optional[str]) -> Optional[str]:
    """Explicit mapping only, no fuzzy/partial matching. Returns the Benivo officeName to look up, or None."""
    return WORKPLACE_TO_OFFICE_NAME.get(normalize_for_lookup(workplace))


def resolve_host_country(office_name: Optional[str]) -> Optional[str]:
    """Explicit OFFICE_NAME_TO_HOST_COUNTRY mapping only. office_name must be an exact Benivo officeName, not a workplace."""
    if office_name is None:
        return None
    return OFFICE_NAME_TO_HOST_COUNTRY.get(office_name)


def resolve_office(candidate: Dict[str, Any], refdata: Optional[Dict[str, Any]]) -> Optional[Dict[str, str]]:
    """
    1. Read candidate.workplace.
    2/3. Normalize + translate via the explicit WORKPLACE_TO_OFFICE_NAME mapping.
    4/5. Find that exact officeName in live refdata, retrieve the real officeId (Benivo's PublicId).
    6. Attach hostCountry via the explicit OFFICE_NAME_TO_HOST_COUNTRY mapping.
    Never guesses, never generates or infers an officeId or hostCountry, no
    fuzzy matching, and hostCountry is never derived from the candidate's
    own address/location data -- only from the resolved Benivo office.
    """
    if refdata is None:
        return None

    workplace = candidate.get("workplace")
    office_name = resolve_office_name(workplace)

    if office_name is None:
        logger.info("Unresolved workplace: %r has no entry in WORKPLACE_TO_OFFICE_NAME.", workplace)
        return None

    offices = refdata.get("offices")

    if not isinstance(offices, list):
        return None

    for office in offices:
        if isinstance(office, dict) and office.get("officeName") == office_name:
            resolved_office_name = office.get("officeName", "")
            return {
                "officeId": office.get("id"),
                "officeName": resolved_office_name,
                "hostCountry": resolve_host_country(resolved_office_name),
            }

    logger.info(
        "Unresolved office: workplace=%r translated to expected Benivo officeName=%r, "
        "but no matching office exists in the current environment's reference data.",
        workplace,
        office_name,
    )
    return None
