"""Jobvite gender -> Benivo Create User `gender` resolution. Explicit mapping only, no fuzzy matching.

Confirmed 2026-09-11+ via official Benivo API documentation: Create User
accepts an optional `gender` (String, group "Demographics") with documented
accepted values Female / Male / Other. Jobvite's own source
(application.gender, synced as-is into benivo.candidates.gender -- see
synchronization_service.py) produces exactly Male / Female / Undefined.

"Undefined" is Jobvite's own value for an unanswered EEO-style field, not a
value Benivo has ever seen or confirmed -- it is deliberately NEVER mapped
to Benivo's "Other" (a real, distinct documented value business explicitly
said must not be used as a substitute for missing data). It resolves to
None, the same as a NULL/blank value, and the payload key is omitted
entirely rather than sent as an empty/guessed value -- gender is documented
Optional, so omission is valid per the confirmed contract.

Any Jobvite value outside the explicit Male/Female mapping (including a
genuinely new, unexpected value -- not just "Undefined") fails safely to
None and is logged, never guessed into "Other" or anything else. A new
confirmed Jobvite value should be added here explicitly, the same way
country_code_service.COUNTRY_NAME_TO_ISO2 and
office_resolution_service.WORKPLACE_TO_OFFICE_NAME are grown.
"""

import logging
from typing import Optional

logger = logging.getLogger(__name__)

GENDER_JOBVITE_TO_BENIVO = {
    "male": "Male",
    "female": "Female",
}


def resolve_gender(raw_gender: Optional[str]) -> Optional[str]:
    """
    Explicit GENDER_JOBVITE_TO_BENIVO mapping only. Returns None (omit from
    payload) for NULL/blank, "Undefined", or any unrecognized value --
    logs unrecognized non-"Undefined" values so a real new Jobvite value
    gets a confirmed mapping added rather than silently dropped forever.
    """
    if not raw_gender:
        return None

    normalized = raw_gender.strip().lower()

    if not normalized:
        return None

    mapped = GENDER_JOBVITE_TO_BENIVO.get(normalized)

    if mapped is None and normalized != "undefined":
        logger.warning(
            "Unmapped Jobvite gender value %r -- omitting from Benivo Create User payload rather than guessing a mapping.",
            raw_gender,
        )

    return mapped
