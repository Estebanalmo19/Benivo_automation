"""Centralized Benivo Population resolution: dealer_shuffler (Jobvite controlled
selector) + mobility_vip (is_vip) -> population.

This module is the ONE place the Population business rule lives -- every
consumer (Create User payload, Payload Preview, Ready To Post, Executive
Summary) calls resolve_population_values()/resolve_population() rather than
re-deriving it from dealer_shuffler/is_vip itself.

SUPERSEDES the 2026-09-04 version of this module, which special-cased an
exact-match allowlist of only "Presenter"/"Game Presenter"/"Shuffler".
Confirmed with Mobility 2026-09-04 (second round): dealer_shuffler
(Jobvite's job.customField[fieldCode='dealer__shuffler'], the real
fieldCode confirmed against actual data -- see candidate_repository.
DEALER_SHUFFLER_SUBQUERY) is a CONTROLLED SELECTOR (a fixed dropdown on the
job posting form, not free text). The confirmed rule is deliberately
broader than any named allowlist: ANY real catalog value the field can
hold -- not just "Presenter"/"Shuffler" -- means the candidate is a Game
Presenter/Shuffler/Dealer-type role and Population is "Game Presenters and
Shufflers". The only case that does NOT qualify is the field genuinely
carrying no real selection (missing/null, or the field's own "not
applicable" placeholder value) -- see is_dealer_shuffler_catalog_value().

Real catalog inspected 2026-09-04 (job-level, ALL 372,747 Jobvite
applications, no LIMIT): exactly 6 distinct values --
  "Presenter"                 83,702
  "Dealer"                    64,218
  "Shuffler"                  14,709
  "Gameshow host"              1,515
  "n/a"                          105  <- the field's own "not applicable" placeholder
  "Prive Specialist Dealer"        70
"n/a" is deliberately excluded from the catalog match -- confirmed by
checking where it actually occurs: dealer_shuffler is asked on EVERY job
posting (164,319 total rows, far more than only Presenter/Shuffler/Dealer-
titled jobs), so "n/a" is how a non-Presenter/Shuffler/Dealer job (e.g. a
Software Engineer posting) answers this question -- it means "this role is
not any of those", the functional equivalent of the field being unset, not
a 7th real role. "Premium Game Host" (mentioned as a possible catalog value)
was investigated and is NOT a dealer_shuffler value in the real data -- it
is a job TITLE (382 rows); every one of those job postings' own
dealer_shuffler value is "Presenter", already covered by the catalog match.
Should a genuinely new catalog value appear in the future (e.g. "Premium
Game Host" is ever actually added as a dealer_shuffler selector option),
is_dealer_shuffler_catalog_value() already matches it automatically -- no
allowlist to update, since ANY non-blank, non-"n/a" value qualifies.

Confirmed business rule (2026-09-04, second round):
  1. dealer_shuffler holds any real catalog value (i.e. is present and is
     not the field's "n/a"/blank placeholder)
       -> "Game Presenters and Shufflers"
  2. Otherwise, mobility_vip (is_vip) is True
       -> "Tier 1"
  3. Otherwise
       -> "Tier 3"

Do NOT infer this from Jobvite job_title or HiBob hr_work_title -- both
were investigated and rejected as sources; dealer_shuffler alone is
authoritative.

These three Population strings are simultaneously the business label AND
the exact Benivo API value -- Mobility's rule already speaks in Benivo's
own refdata['policies'] vocabulary ("Tier 1", "Tier 3", "Game Presenters
and Shufflers" all appear there verbatim -- see migrations/0004's
comment), so there is no separate translation step to guess or misspell.

Which Benivo API field carries this value: confirmed 2026-09-04 -- UAT
evidence shows the Create User "policy" value is what appears as
Population in the Benivo UI. See posting_service.build_benivo_payload()
for the one place that value is wired into the outbound "policy" field.
"""

from typing import Optional, Tuple

from app.models.domain import (
    POPULATION_GAME_PRESENTERS_AND_SHUFFLERS,
    POPULATION_TIER_1,
    POPULATION_TIER_3,
)

# The field's own "not applicable" placeholder(s) -- confirmed real value
# is "n/a" (105 rows); the other spellings are defensive normalization,
# not separately confirmed in real data, in case of future data variance.
_NOT_A_REAL_CATALOG_VALUE = {"n/a", "na", "none", "not applicable"}

_VALID_POPULATION_VALUES = (
    POPULATION_GAME_PRESENTERS_AND_SHUFFLERS,
    POPULATION_TIER_1,
    POPULATION_TIER_3,
)


def is_dealer_shuffler_catalog_value(dealer_shuffler: Optional[str]) -> bool:
    """
    True for any real, non-blank dealer_shuffler value OTHER than the
    field's own "n/a" placeholder -- confirmed 2026-09-04: dealer_shuffler
    is a controlled selector (closed dropdown), so trusting the field's
    mere presence (rather than matching specific named strings) is the
    authoritative signal, per Mobility's explicit instruction not to
    special-case only Presenter/Shuffler.
    """
    if not dealer_shuffler:
        return False

    normalized = dealer_shuffler.strip().lower()

    if not normalized:
        return False

    return normalized not in _NOT_A_REAL_CATALOG_VALUE


def resolve_population(dealer_shuffler: Optional[str], is_vip: Optional[bool]) -> str:
    """The one centralized Population-resolution rule."""
    if is_dealer_shuffler_catalog_value(dealer_shuffler):
        return POPULATION_GAME_PRESENTERS_AND_SHUFFLERS

    if is_vip is True:
        return POPULATION_TIER_1

    return POPULATION_TIER_3


def resolve_population_api_value(population_name: str) -> Optional[str]:
    """
    Exact Benivo API value for a business population_name, or None if
    unrecognized (fails safe). In practice every value resolve_population()
    can return is already a confirmed API value; this stays a separate
    step only for symmetry and to guard against a future caller passing an
    arbitrary string.
    """
    return population_name if population_name in _VALID_POPULATION_VALUES else None


def resolve_population_values(dealer_shuffler: Optional[str], is_vip: Optional[bool]) -> Tuple[str, Optional[str]]:
    """(population_name, population_api_value) -- population_name is never overwritten by the API-specific label."""
    population_name = resolve_population(dealer_shuffler, is_vip)
    return population_name, resolve_population_api_value(population_name)
