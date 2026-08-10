import datetime
from unittest.mock import MagicMock, patch

import pytest

from app.models.domain import (
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
    assert classify(relocation_value, start_date, workplace, EXECUTION_TIMESTAMP) == expected


def test_classify_missing_jobvite_start_date_becomes_ready_to_post_not_pending():
    # Explicit regression guard for the rule change: a candidate with NO
    # Jobvite start_date and a mapped workplace must become READY_TO_POST
    # (via the calculated third-month-after date), never
    # PENDING_MISSING_START_DATE.
    assert classify("Yes", None, MAPPED_WORKPLACE, EXECUTION_TIMESTAMP) == READY_TO_POST


def test_posted_is_terminal_and_excluded_from_reclassification():
    assert is_terminal(POSTED) is True


@pytest.mark.parametrize(
    "non_terminal_status",
    ["PENDING", "READY_TO_POST", "PENDING_MISSING_START_DATE", "PENDING_OFFICE_MAPPING", "NEEDS_RECRUITER_REVIEW", "POST_FAILED", None],
)
def test_only_posted_is_terminal(non_terminal_status):
    assert is_terminal(non_terminal_status) is False


def test_pending_office_mapping_is_not_terminal_so_new_mappings_are_picked_up():
    # Confirms a candidate stuck at PENDING_OFFICE_MAPPING will be freely
    # reclassified (and can become READY_TO_POST) the moment its workplace
    # is added to office_resolution_service.WORKPLACE_TO_OFFICE_NAME -- no
    # special-casing needed beyond PENDING_OFFICE_MAPPING not being terminal.
    assert is_terminal(PENDING_OFFICE_MAPPING) is False
    assert classify("Yes", SOME_DATE, UNMAPPED_WORKPLACE, EXECUTION_TIMESTAMP) == PENDING_OFFICE_MAPPING


def test_classify_candidates_generates_execution_timestamp_once_per_run():
    # Three candidates, none with a Jobvite start_date -- if
    # classify_candidates() called datetime.now() per-candidate instead of
    # once for the whole run, this would still (almost always) pass by
    # coincidence; asserting the call count directly is what actually
    # proves "generated once per run, reused for every candidate."
    mock_cursor = MagicMock()
    mock_cursor.fetchall.return_value = [
        {"id": 1, "is_relocation_required": "Yes", "start_date": None, "workplace": MAPPED_WORKPLACE},
        {"id": 2, "is_relocation_required": "Yes", "start_date": None, "workplace": MAPPED_WORKPLACE},
        {"id": 3, "is_relocation_required": "Yes", "start_date": None, "workplace": MAPPED_WORKPLACE},
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
