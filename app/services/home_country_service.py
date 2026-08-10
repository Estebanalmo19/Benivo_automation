"""Effective home-country resolution: candidate_home_country (primary), with a
current_country fallback when the primary is missing.

Investigated 2026-08-10 (Country Data Issues investigation, pdr4myw7/
p6Ejaywg): application.customField[fieldCode=candidate_home_country] --
the confirmed primary source for benivo.candidates.home_country -- does not
exist for every candidate. Jobvite's own candidate-profile country field
(raw_payload.countryName, synced into benivo.candidates.current_country --
see synchronization_service.py) covered 100% of the candidates missing
home_country at the time of that investigation. It is a structured field,
never parsed from the 'location' display string ("city, state country") or
from application.job.location (the JOB's location, not the candidate's).

This is a fallback only: home_country is never overwritten. Both raw values
stay available on the candidate row; only the derived effective value and
its source are computed here, on demand, exactly like start_date_service.
resolve_effective_start_date().
"""

from typing import Any, Dict, Optional, Tuple

SOURCE_CANDIDATE_HOME_COUNTRY = "CANDIDATE_HOME_COUNTRY"
SOURCE_CURRENT_LOCATION = "CURRENT_LOCATION"
SOURCE_MISSING = "MISSING"


def resolve_effective_home_country(candidate: Dict[str, Any]) -> Tuple[Optional[str], str]:
    """
    (effective_home_country, source):
      candidate_home_country present -> (that value, CANDIDATE_HOME_COUNTRY)
      else current_country present    -> (that value, CURRENT_LOCATION)
      else                             -> (None, MISSING)
    """
    candidate_home_country = candidate.get("home_country")

    if candidate_home_country:
        return candidate_home_country, SOURCE_CANDIDATE_HOME_COUNTRY

    current_country = candidate.get("current_country")

    if current_country:
        return current_country, SOURCE_CURRENT_LOCATION

    return None, SOURCE_MISSING
