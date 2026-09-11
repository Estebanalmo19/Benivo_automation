"""Jobvite phone -> Benivo Create User `phoneNumber` normalization. Deterministic formatting cleanup only, no inference.

Confirmed 2026-09-11+ via official Benivo API documentation: Create User
accepts an optional `phoneNumber` (String, group "Primary Identification and
Contact Information"), documented example "+0000000000" -- an
international, "+"-prefixed value. Jobvite's own source
(candidate.mobile, synced as-is into benivo.candidates.phone_number -- see
synchronization_service.py) is inconsistently formatted: roughly 45% of the
current Mobility-in-process population already carry a leading "+" (an
international-style value), the rest are bare national/local numbers with
no country code at all.

This module only strips harmless, deterministic formatting noise (spaces,
hyphens, parentheses) from an already-international-looking number -- it
NEVER invents or infers a country code for a national/local number, since
there is no reliable source for one (home_country/current_country/
host_country/citizenship are all candidate location data, not proof of
which number format the phone digits themselves use). A number that does
not already start with "+" is omitted entirely rather than guessed into a
possibly-wrong country.
"""

import logging
import re
from typing import Optional

logger = logging.getLogger(__name__)

# Harmless formatting separators only -- spaces, hyphens, and parentheses.
# Nothing else is ever stripped (e.g. dots or letters would indicate a
# shape this function isn't confident about, so the value falls through to
# the final regex check and is omitted rather than partially "cleaned").
_SEPARATORS_TO_STRIP = re.compile(r"[ \-()]")

# The only accepted final shape: a leading "+" followed by one or more
# digits and nothing else.
_INTERNATIONAL_PHONE_PATTERN = re.compile(r"^\+\d+$")


def normalize_phone_number(raw_phone: Optional[str]) -> Optional[str]:
    """
    Returns a cleaned "+<digits>" string, or None (omit from payload) if the
    value is blank, or doesn't already look like an international number
    after removing harmless separators. Never derives or guesses a country
    code -- a bare national/local number always resolves to None.
    """
    if not raw_phone:
        return None

    trimmed = raw_phone.strip()

    if not trimmed:
        return None

    cleaned = _SEPARATORS_TO_STRIP.sub("", trimmed)

    if _INTERNATIONAL_PHONE_PATTERN.match(cleaned):
        return cleaned

    logger.info(
        "Jobvite phone value does not resolve to a safe international format -- omitting from Benivo Create User payload rather than guessing a country code."
    )
    return None
