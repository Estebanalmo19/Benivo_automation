"""Centralized effective-start-date resolution for Benivo posting.

Temporary business rule, approved by the business owner (2026-08-06),
replacing the old behavior where a missing Jobvite start_date blocked a
candidate at PENDING_MISSING_START_DATE indefinitely:

    Jobvite start_date present  -> use it                          (JOBVITE)
    Jobvite start_date missing  -> first calendar day of the THIRD
                                    month after the execution date   (CALCULATED)

Example: execution 2026-08-06 -> effective 2026-11-01.
Example: execution 2026-12-18 -> effective 2027-03-01 (year rollover).

start_date_source records the ORIGIN of the date (JOBVITE vs CALCULATED),
not the specific rule that produced a calculated value -- the offset itself
is a separate, changeable configuration value (START_DATE_OFFSET_MONTHS),
so the audit trail stays meaningful even after the offset changes.

Deliberately isolated in its own module -- not inlined into
classification_service or posting_service -- so this stays the single
place the rule is expressed. Change START_DATE_OFFSET_MONTHS, or replace
_first_day_of_nth_month_after entirely, when the business rule changes;
no caller needs to change.

Every caller must generate ONE execution_timestamp per run (classify/post/
report) and pass that same value into every resolve_effective_start_date()
call for that run -- never call datetime.now() per-candidate, or two
candidates processed moments apart could land in different calendar
months for no business reason.
"""

from datetime import date, datetime
from typing import Any, Optional, Tuple

SOURCE_JOBVITE = "JOBVITE"
SOURCE_CALCULATED = "CALCULATED"

# The "temporary" knob: how many months ahead of the execution date the
# calculated fallback lands, when no Jobvite start_date exists. Change this
# single constant when the business rule's offset changes.
START_DATE_OFFSET_MONTHS = 3


def _first_day_of_nth_month_after(execution_timestamp: datetime, months_ahead: int) -> date:
    total_months = execution_timestamp.month - 1 + months_ahead
    year = execution_timestamp.year + total_months // 12
    month = total_months % 12 + 1
    return date(year, month, 1)


def resolve_effective_start_date(
    jobvite_start_date: Optional[Any],
    execution_timestamp: datetime,
) -> Tuple[Optional[date], str]:
    """
    Returns (effective_start_date, start_date_source).

    jobvite_start_date is whatever benivo.candidates.start_date currently
    holds (a date, a datetime, or None/falsy) -- Jobvite wins whenever it's
    present, regardless of execution_timestamp.
    """
    if jobvite_start_date:
        if isinstance(jobvite_start_date, datetime):
            return jobvite_start_date.date(), SOURCE_JOBVITE
        return jobvite_start_date, SOURCE_JOBVITE

    calculated = _first_day_of_nth_month_after(execution_timestamp, START_DATE_OFFSET_MONTHS)
    return calculated, SOURCE_CALCULATED
