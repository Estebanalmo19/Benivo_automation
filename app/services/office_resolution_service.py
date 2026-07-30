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
# hardcoded anywhere in this mapping or this module.
WORKPLACE_TO_OFFICE_NAME = {
    "rak live casino": "UAE (Live Casino)",
    "serbia live casino": "Serbia (Live Casino)",
    "colombia live casino": "Colombia (Live Casino)",
    "bulgaria live casino": "Bulgaria (Live Casino)",
    "malta": "Malta (Global)",
}


def normalize_for_lookup(value: Optional[str]) -> str:
    """Whitespace/casing normalization for lookup only -- never stored or sent to Benivo."""
    if not value:
        return ""
    return " ".join(value.strip().split()).lower()


def resolve_office_name(workplace: Optional[str]) -> Optional[str]:
    """Explicit mapping only, no fuzzy/partial matching. Returns the Benivo officeName to look up, or None."""
    return WORKPLACE_TO_OFFICE_NAME.get(normalize_for_lookup(workplace))


def resolve_office(candidate: Dict[str, Any], refdata: Optional[Dict[str, Any]]) -> Optional[Dict[str, str]]:
    """
    1. Read candidate.workplace.
    2/3. Normalize + translate via the explicit WORKPLACE_TO_OFFICE_NAME mapping.
    4/5. Find that exact officeName in live refdata, retrieve the real officeId.
    Never guesses, never generates or infers an officeId, no fuzzy matching.
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
            return {"officeId": office.get("id"), "officeName": office.get("officeName", "")}

    logger.info(
        "Unresolved office: workplace=%r translated to expected Benivo officeName=%r, "
        "but no matching office exists in the current environment's reference data.",
        workplace,
        office_name,
    )
    return None
