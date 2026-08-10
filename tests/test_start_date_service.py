import datetime

import pytest

from app.services.start_date_service import (
    SOURCE_CALCULATED,
    SOURCE_JOBVITE,
    resolve_effective_start_date,
)


def test_jobvite_start_date_is_preserved_when_present():
    jobvite_date = datetime.date(2026, 5, 15)
    execution_timestamp = datetime.datetime(2026, 8, 6, tzinfo=datetime.timezone.utc)

    effective_start_date, source = resolve_effective_start_date(jobvite_date, execution_timestamp)

    assert effective_start_date == jobvite_date
    assert source == SOURCE_JOBVITE


def test_jobvite_datetime_is_normalized_to_date():
    jobvite_datetime = datetime.datetime(2026, 5, 15, 9, 30, tzinfo=datetime.timezone.utc)
    execution_timestamp = datetime.datetime(2026, 8, 6, tzinfo=datetime.timezone.utc)

    effective_start_date, source = resolve_effective_start_date(jobvite_datetime, execution_timestamp)

    assert effective_start_date == datetime.date(2026, 5, 15)
    assert source == SOURCE_JOBVITE


def test_falsy_start_date_values_trigger_calculation():
    execution_timestamp = datetime.datetime(2026, 8, 6, tzinfo=datetime.timezone.utc)

    for falsy_value in (None, "", 0):
        effective_start_date, source = resolve_effective_start_date(falsy_value, execution_timestamp)
        assert source == SOURCE_CALCULATED
        assert effective_start_date == datetime.date(2026, 11, 1)


def test_calculated_first_day_of_third_month_after_execution():
    # Business example: execution 2026-08-06 -> effective 2026-11-01.
    execution_timestamp = datetime.datetime(2026, 8, 6, tzinfo=datetime.timezone.utc)

    effective_start_date, source = resolve_effective_start_date(None, execution_timestamp)

    assert effective_start_date == datetime.date(2026, 11, 1)
    assert source == SOURCE_CALCULATED


def test_calculated_third_month_rolls_over_into_next_year():
    # Business example: execution 2026-12-18 -> effective 2027-03-01.
    execution_timestamp = datetime.datetime(2026, 12, 18, tzinfo=datetime.timezone.utc)

    effective_start_date, source = resolve_effective_start_date(None, execution_timestamp)

    assert effective_start_date == datetime.date(2027, 3, 1)
    assert source == SOURCE_CALCULATED


@pytest.mark.parametrize(
    "execution_date, expected",
    [
        (datetime.date(2026, 1, 1), datetime.date(2026, 4, 1)),
        (datetime.date(2026, 9, 30), datetime.date(2026, 12, 1)),
        (datetime.date(2026, 10, 1), datetime.date(2027, 1, 1)),   # rollover
        (datetime.date(2026, 11, 1), datetime.date(2027, 2, 1)),   # rollover
        (datetime.date(2026, 2, 28), datetime.date(2026, 5, 1)),
    ],
)
def test_calculated_third_month_across_various_months(execution_date, expected):
    execution_timestamp = datetime.datetime(
        execution_date.year, execution_date.month, execution_date.day, tzinfo=datetime.timezone.utc
    )

    effective_start_date, source = resolve_effective_start_date(None, execution_timestamp)

    assert effective_start_date == expected
    assert source == SOURCE_CALCULATED


def test_same_execution_timestamp_reused_gives_identical_result_for_every_candidate():
    # Simulates a batch run: one execution_timestamp generated once, reused
    # across many candidates in the same run -- every candidate without a
    # Jobvite date must land on the exact same calculated date, regardless
    # of how many times resolve_effective_start_date() is called with it.
    execution_timestamp = datetime.datetime(2026, 8, 6, 23, 59, 59, tzinfo=datetime.timezone.utc)

    results = [resolve_effective_start_date(None, execution_timestamp) for _ in range(5)]

    assert len({r[0] for r in results}) == 1
    assert all(r[0] == datetime.date(2026, 11, 1) for r in results)
    assert all(r[1] == SOURCE_CALCULATED for r in results)
