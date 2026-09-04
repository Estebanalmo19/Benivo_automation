"""Centralized Benivo scope/eligibility resolution: mobility_support option parsing.

This module is the ONE place the scope business rule lives -- every
consumer (classification_service.classify(), reporting_service's scope/
reason columns) calls the functions here rather than re-deriving it.

Confirmed business rule (2026-09-04, corrected 2026-09-05 -- see below):

mobility_support eligibility. Jobvite's application.customField
[fieldCode='mobility_support'] is a MULTI-SELECT field: real values are
newline-separated option lists (confirmed against 21,325 real rows, e.g.
"Relocation\\nVisa/work permit\\nAccommodation"), never a single
delimiter-free string -- so this never compares the raw value with `==`.
A candidate qualifies for Benivo/Mobility support -- and is IN SCOPE,
full stop -- if the parsed option list contains ANY of "Relocation" /
"Visa/work permit" / "Accommodation" (any combination of the three
qualifies). "N/A" by itself, a missing field, or an unrecognized value
never qualifies on its own -- but "N/A" mixed with a real option
(confirmed present in real data, e.g. "Relocation\\nN/A") still qualifies,
since a real option is present. There is no other scope gate: mobility_support
alone determines Benivo scope.

CORRECTED 2026-09-05: an earlier version of this module additionally
excluded "domestic/local relocation" candidates (home country == the
Benivo office's host country) whose destination was not UAE, reasoning
that "Local/domestic relocations should be imported into Benivo ONLY for
UAE" meant domestic-non-UAE candidates should be excluded even when
mobility_support qualified. Mobility explicitly corrected this 2026-09-05:
domestic/local status is NOT a general exclusion gate for Serbia, Romania,
Bulgaria, or any other country -- a domestic candidate with a qualifying
mobility_support selection IS in scope, exactly like an international one.
The UAE mention was never a reason to exclude other countries; it was
guidance that UAE domestic candidates must NOT be excluded (they still
need immigration support locally) -- which requires no special-casing at
all now that domestic status isn't a gate for anyone. The domestic/host-
country comparison this module used to perform (is_domestic_relocation(),
UAE_ISO2, SCOPE_REASON_DOMESTIC_NON_UAE) has been removed entirely, not
merely disabled, per this session's convention of not leaving retired
logic sitting unused. Scope is now, and only, resolve_scope() ==
mobility_support_qualifies().
"""

from typing import List, Optional, Tuple

MOBILITY_SUPPORT_RELOCATION = "Relocation"
MOBILITY_SUPPORT_VISA_WORK_PERMIT = "Visa/work permit"
MOBILITY_SUPPORT_ACCOMMODATION = "Accommodation"

_QUALIFYING_OPTIONS = {
    MOBILITY_SUPPORT_RELOCATION.lower(),
    MOBILITY_SUPPORT_VISA_WORK_PERMIT.lower(),
    MOBILITY_SUPPORT_ACCOMMODATION.lower(),
}

SCOPE_REASON_MOBILITY_SUPPORT = "MOBILITY_SUPPORT_NOT_SELECTED"

SCOPE_REASON_TEXT = {
    SCOPE_REASON_MOBILITY_SUPPORT: (
        "mobility_support does not include Relocation, Visa/work permit, or Accommodation"
    ),
}


def parse_mobility_support_options(raw_value: Optional[str]) -> List[str]:
    """
    Splits Jobvite's multi-select mobility_support value into its individual
    options. Confirmed real-data separator is "\\n" (13 distinct
    combinations observed, all newline-joined, no comma/semicolon variant
    seen) -- \\r\\n and bare \\r are also normalized defensively. Each token
    is trimmed; empty tokens (blank lines, trailing newline) are dropped.
    Returns [] for None/empty input.
    """
    if not raw_value:
        return []

    normalized = raw_value.replace("\r\n", "\n").replace("\r", "\n")
    return [token.strip() for token in normalized.split("\n") if token.strip()]


def mobility_support_qualifies(raw_value: Optional[str]) -> bool:
    """
    True if the parsed option list contains Relocation, Visa/work permit,
    or Accommodation (any combination). False for a missing field, "N/A"
    alone, or any other unrecognized value(s) only.
    """
    options = parse_mobility_support_options(raw_value)
    return any(option.lower() in _QUALIFYING_OPTIONS for option in options)


def resolve_scope(mobility_support: Optional[str]) -> Tuple[bool, Optional[str]]:
    """
    (in_scope, reason_code). reason_code is None when in_scope is True,
    otherwise SCOPE_REASON_MOBILITY_SUPPORT (look up SCOPE_REASON_TEXT for
    a human-readable string). Deliberately independent of
    is_relocation_required/benivo_status -- this answers only "does
    mobility_support put this candidate in Benivo's scope", so callers
    (classify(), reporting) can combine it with their own other gates as
    needed. Currently a thin wrapper over mobility_support_qualifies() --
    kept as its own function (rather than inlined at call sites) so a
    future additional scope rule has one obvious place to be added, and so
    reporting can ask "is this candidate in scope" without needing to know
    it's just a mobility_support check today.
    """
    if not mobility_support_qualifies(mobility_support):
        return False, SCOPE_REASON_MOBILITY_SUPPORT

    return True, None
