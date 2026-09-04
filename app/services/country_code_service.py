"""Country name -> ISO 3166-1 alpha-2 code resolution. Explicit mapping only, no fuzzy matching.

Confirmed 2026-08-21 by a real Benivo UAT PATCH: the Case PATCH
(build_case_update_payload() in posting_service.py) requires
homeLocation.country as a 2-character ISO 3166-1 alpha-2 code -- Benivo
rejects a full country name with error 4422
("HomeLocation.Country must be a valid 2-character country code"). This is
used ONLY for that field. create-user's homeCountry has no evidence of the
same requirement and is deliberately left sending the full country name
as-is -- see build_benivo_payload(), which does not use this module.

Two distinct sources feed a candidate's country value (see
home_country_service.py) and neither is a clean ISO 3166 short name in
every case:
  - candidates.home_country: a Jobvite custom-field dropdown label. Most
    entries already match the ISO 3166 English short name, but some don't
    (e.g. "Korea (South)", "Myanmar (Burma)", "Russia",
    "North Macedonia (formerly Macedonia)").
  - candidates.current_country: Jobvite's own countryName field, which
    mostly already follows the formal ISO 3166 English short name (e.g.
    "Russian Federation", "Viet Nam", "Korea (the Republic of)").

COUNTRY_NAME_TO_ISO2 covers the exact union of both fields' values observed
in the candidate population as of the 2026-08-21 audit -- same
confirmed-only philosophy as office_resolution_service.WORKPLACE_TO_OFFICE_NAME:
no guessed code is ever sent. A country not yet seen resolves to None
(fails safely -- see resolve_iso2()) rather than a guessed 2-letter code;
add it here explicitly once confirmed, the same way a new workplace is
added to WORKPLACE_TO_OFFICE_NAME.
"""

import logging
from typing import Optional

logger = logging.getLogger(__name__)

COUNTRY_NAME_TO_ISO2 = {
    "argentina": "AR",
    "armenia": "AM",
    "australia": "AU",
    "austria": "AT",
    "azerbaijan": "AZ",
    "belarus": "BY",
    "brazil": "BR",
    "bulgaria": "BG",
    "cambodia": "KH",
    "china": "CN",
    "colombia": "CO",
    "cyprus": "CY",
    "estonia": "EE",
    "georgia": "GE",
    "germany": "DE",
    "ghana": "GH",
    "greece": "GR",
    "india": "IN",
    "indonesia": "ID",
    "italy": "IT",
    "kazakhstan": "KZ",
    "korea (south)": "KR",
    "korea (the republic of)": "KR",
    "korea (the democratic people's republic of)": "KP",
    "kyrgyzstan": "KG",
    "latvia": "LV",
    "lithuania": "LT",
    "malaysia": "MY",
    "mali": "ML",
    "malta": "MT",
    "moldova (the republic of)": "MD",
    "montenegro": "ME",
    "myanmar": "MM",
    "myanmar (burma)": "MM",
    "nepal": "NP",
    "north macedonia": "MK",
    "north macedonia (formerly macedonia)": "MK",
    "philippines": "PH",
    "poland": "PL",
    "romania": "RO",
    "russia": "RU",
    "russian federation": "RU",
    "serbia": "RS",
    "seychelles": "SC",
    "south africa": "ZA",
    "sri lanka": "LK",
    "tanzania": "TZ",
    "thailand": "TH",
    "turkey": "TR",
    "turkmenistan": "TM",
    "ukraine": "UA",
    "united arab emirates": "AE",
    # Added 2026-09-04 for mobility_scope_service.py's UAE-only domestic
    # relocation rule: office_resolution_service.OFFICE_NAME_TO_HOST_COUNTRY
    # reads the literal string "UAE" (not the ISO short name), while a
    # candidate's own home_country/current_country reads "United Arab
    # Emirates" -- both must resolve to the same ISO2 code to be compared.
    "uae": "AE",
    "united kingdom": "GB",
    "united states": "US",
    "uzbekistan": "UZ",
    "viet nam": "VN",
    "vietnam": "VN",
}


def normalize_for_lookup(value: Optional[str]) -> str:
    """Whitespace/casing normalization for lookup only -- never stored or sent to Benivo."""
    if not value:
        return ""
    return " ".join(value.strip().split()).lower()


def resolve_iso2(country_name: Optional[str]) -> Optional[str]:
    """
    Explicit COUNTRY_NAME_TO_ISO2 mapping only. Fails safely: returns None
    (never a guessed code) for a missing or unrecognized country name, and
    logs which value had no mapping so a real gap gets a confirmed entry
    added rather than silently sending an invalid code to Benivo.
    """
    if not country_name:
        return None

    iso2 = COUNTRY_NAME_TO_ISO2.get(normalize_for_lookup(country_name))

    if iso2 is None:
        logger.warning(
            "Unresolved country for Case PATCH ISO code: %r has no entry in COUNTRY_NAME_TO_ISO2.",
            country_name,
        )

    return iso2
