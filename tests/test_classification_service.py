import datetime
from unittest.mock import MagicMock, patch

import pytest

from app.models.domain import (
    EXCLUDED_MOBILITY_SUPPORT,
    NEEDS_RECRUITER_REVIEW,
    PENDING_OFFICE_MAPPING,
    POSTED,
    READY_TO_POST,
)
from app.services import classification_service
from app.services.classification_service import classify, is_terminal

SOME_DATE = datetime.date(2026, 1, 1)
EXECUTION_TIMESTAMP = datetime.datetime(2026, 8, 6, tzinfo=datetime.timezone.utc)
MAPPED_WORKPLACE = "Serbia Live Casino"
UNMAPPED_WORKPLACE = "Nonexistent Site"

# A qualifying mobility_support value -- used as the "does not block
# readiness" default for every pre-existing relocation/start-date/office
# test case below, so those keep asserting exactly what they asserted
# before mobility_support existed as a gate.
QUALIFYING_SUPPORT = "Relocation"


@pytest.mark.parametrize(
    "relocation_value, start_date, workplace, expected",
    [
        ("Yes", SOME_DATE, MAPPED_WORKPLACE, READY_TO_POST),
        ("yes", SOME_DATE, MAPPED_WORKPLACE, READY_TO_POST),
        ("YES", SOME_DATE, MAPPED_WORKPLACE, READY_TO_POST),
        ("Yes", SOME_DATE, UNMAPPED_WORKPLACE, PENDING_OFFICE_MAPPING),
        ("Yes", SOME_DATE, None, PENDING_OFFICE_MAPPING),
        # No Jobvite start_date: no longer blocks readiness. An effective
        # (calculated) date is always resolvable under the current temporary
        # rule (see start_date_service), so these behave exactly like a
        # candidate whose Jobvite start_date IS present.
        ("Yes", None, MAPPED_WORKPLACE, READY_TO_POST),
        ("Yes", None, UNMAPPED_WORKPLACE, PENDING_OFFICE_MAPPING),
        ("No", None, MAPPED_WORKPLACE, NEEDS_RECRUITER_REVIEW),
        ("No", SOME_DATE, MAPPED_WORKPLACE, NEEDS_RECRUITER_REVIEW),
        (None, SOME_DATE, MAPPED_WORKPLACE, NEEDS_RECRUITER_REVIEW),
        (None, None, None, NEEDS_RECRUITER_REVIEW),
        ("", SOME_DATE, MAPPED_WORKPLACE, NEEDS_RECRUITER_REVIEW),
        ("Maybe", SOME_DATE, MAPPED_WORKPLACE, NEEDS_RECRUITER_REVIEW),
        ("TRUE", SOME_DATE, MAPPED_WORKPLACE, NEEDS_RECRUITER_REVIEW),
    ],
)
def test_classify(relocation_value, start_date, workplace, expected):
    assert classify(
        relocation_value, start_date, workplace, EXECUTION_TIMESTAMP, mobility_support=QUALIFYING_SUPPORT,
    ) == expected


def test_classify_missing_jobvite_start_date_becomes_ready_to_post_not_pending():
    # Explicit regression guard for the rule change: a candidate with NO
    # Jobvite start_date and a mapped workplace must become READY_TO_POST
    # (via the calculated third-month-after date), never
    # PENDING_MISSING_START_DATE.
    assert classify(
        "Yes", None, MAPPED_WORKPLACE, EXECUTION_TIMESTAMP, mobility_support=QUALIFYING_SUPPORT,
    ) == READY_TO_POST


def test_posted_is_terminal_and_excluded_from_reclassification():
    assert is_terminal(POSTED) is True


@pytest.mark.parametrize(
    "non_terminal_status",
    [
        "PENDING", "READY_TO_POST", "PENDING_MISSING_START_DATE", "PENDING_OFFICE_MAPPING",
        "NEEDS_RECRUITER_REVIEW", "EXCLUDED_MOBILITY_SUPPORT",
        "POST_FAILED", None,
    ],
)
def test_only_posted_is_terminal(non_terminal_status):
    assert is_terminal(non_terminal_status) is False


def test_pending_office_mapping_is_not_terminal_so_new_mappings_are_picked_up():
    # Confirms a candidate stuck at PENDING_OFFICE_MAPPING will be freely
    # reclassified (and can become READY_TO_POST) the moment its workplace
    # is added to office_resolution_service.WORKPLACE_TO_OFFICE_NAME -- no
    # special-casing needed beyond PENDING_OFFICE_MAPPING not being terminal.
    assert is_terminal(PENDING_OFFICE_MAPPING) is False
    assert classify(
        "Yes", SOME_DATE, UNMAPPED_WORKPLACE, EXECUTION_TIMESTAMP, mobility_support=QUALIFYING_SUPPORT,
    ) == PENDING_OFFICE_MAPPING


# ---------------------------------------------------------------------------
# mobility_support eligibility (confirmed 2026-09-04 business rule; the
# ONLY Benivo scope rule -- corrected 2026-09-05, domestic/local relocation
# is NOT a general exclusion gate for any country, UAE included).
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "mobility_support",
    ["Relocation", "Visa/work permit", "Accommodation", "Relocation\nVisa/work permit\nAccommodation", "Relocation\nAccommodation"],
)
def test_classify_mobility_support_qualifying_combinations_are_ready(mobility_support):
    assert classify(
        "Yes", SOME_DATE, MAPPED_WORKPLACE, EXECUTION_TIMESTAMP, mobility_support=mobility_support,
    ) == READY_TO_POST


def test_classify_mobility_support_na_only_excludes():
    assert classify(
        "Yes", SOME_DATE, MAPPED_WORKPLACE, EXECUTION_TIMESTAMP, mobility_support="N/A",
    ) == EXCLUDED_MOBILITY_SUPPORT


def test_classify_mobility_support_missing_excludes():
    assert classify(
        "Yes", SOME_DATE, MAPPED_WORKPLACE, EXECUTION_TIMESTAMP, mobility_support=None,
    ) == EXCLUDED_MOBILITY_SUPPORT


def test_classify_mobility_support_checked_before_start_date_and_office():
    # An EXCLUDED_MOBILITY_SUPPORT candidate reports that reason even when
    # it would otherwise also be PENDING_OFFICE_MAPPING -- mobility_support
    # is checked earlier.
    assert classify(
        "Yes", SOME_DATE, UNMAPPED_WORKPLACE, EXECUTION_TIMESTAMP, mobility_support="N/A",
    ) == EXCLUDED_MOBILITY_SUPPORT


def test_classify_relocation_gate_checked_before_mobility_support():
    # is_relocation_required != Yes still wins over mobility_support --
    # unchanged from the original rule's ordering.
    assert classify(
        "No", SOME_DATE, MAPPED_WORKPLACE, EXECUTION_TIMESTAMP, mobility_support="N/A",
    ) == NEEDS_RECRUITER_REVIEW


def test_classify_domestic_relocation_no_longer_excludes():
    # Corrected 2026-09-05: a domestic relocation (e.g. Serbia -> Serbia)
    # with a qualifying mobility_support selection is READY_TO_POST,
    # exactly like an international one -- classify() no longer takes a
    # home_country/current_country/domestic-relocation gate at all.
    assert classify(
        "Yes", SOME_DATE, MAPPED_WORKPLACE, EXECUTION_TIMESTAMP, mobility_support=QUALIFYING_SUPPORT,
    ) == READY_TO_POST


def test_classify_signature_has_no_domestic_relocation_parameters():
    # classify() must not accept home_country/current_country -- the
    # domestic-relocation gate they fed was removed entirely, not merely
    # disabled.
    import inspect

    params = inspect.signature(classify).parameters
    assert "home_country" not in params
    assert "current_country" not in params


# ---------------------------------------------------------------------------
# classify_candidates() -- DB orchestration
# ---------------------------------------------------------------------------

def test_classify_candidates_generates_execution_timestamp_once_per_run():
    # Three candidates, none with a Jobvite start_date -- if
    # classify_candidates() called datetime.now() per-candidate instead of
    # once for the whole run, this would still (almost always) pass by
    # coincidence; asserting the call count directly is what actually
    # proves "generated once per run, reused for every candidate."
    mock_cursor = MagicMock()
    mock_cursor.fetchall.return_value = [
        {"id": 1, "is_relocation_required": "Yes", "start_date": None, "workplace": MAPPED_WORKPLACE, "mobility_support": QUALIFYING_SUPPORT},
        {"id": 2, "is_relocation_required": "Yes", "start_date": None, "workplace": MAPPED_WORKPLACE, "mobility_support": QUALIFYING_SUPPORT},
        {"id": 3, "is_relocation_required": "Yes", "start_date": None, "workplace": MAPPED_WORKPLACE, "mobility_support": QUALIFYING_SUPPORT},
    ]
    mock_cursor.rowcount = 1

    class FakeTransaction:
        def __enter__(self):
            return mock_cursor

        def __exit__(self, *args):
            return False

    fixed_now = datetime.datetime(2026, 8, 6, tzinfo=datetime.timezone.utc)
    mock_datetime = MagicMock(wraps=datetime.datetime)
    mock_datetime.now.return_value = fixed_now

    with patch("app.services.classification_service.transaction", return_value=FakeTransaction()), \
         patch("app.services.classification_service.datetime", mock_datetime):
        counts = classification_service.classify_candidates()

    assert mock_datetime.now.call_count == 1
    assert counts[READY_TO_POST] == 3


def test_fetch_classifiable_candidates_query_includes_mobility_support_subquery():
    from app.repositories.candidate_repository import MOBILITY_SUPPORT_SUBQUERY

    mock_cursor = MagicMock()
    mock_cursor.fetchall.return_value = []

    classification_service._fetch_classifiable_candidates(mock_cursor)

    sql, params = mock_cursor.execute.call_args[0]
    assert MOBILITY_SUPPORT_SUBQUERY in sql
    assert params == (POSTED,)


def test_fetch_classifiable_candidates_query_no_longer_selects_home_or_current_country():
    # Corrected 2026-09-05: classify() no longer needs these -- the
    # domestic-relocation gate that used them was removed.
    mock_cursor = MagicMock()
    mock_cursor.fetchall.return_value = []

    classification_service._fetch_classifiable_candidates(mock_cursor)

    sql, _params = mock_cursor.execute.call_args[0]
    assert "home_country" not in sql
    assert "current_country" not in sql
