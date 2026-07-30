import datetime

import pytest

from classify_candidates import (
    NEEDS_RECRUITER_REVIEW,
    PENDING_MISSING_START_DATE,
    PENDING_OFFICE_MAPPING,
    POSTED,
    READY_TO_POST,
    classify,
    is_terminal,
)

SOME_DATE = datetime.date(2026, 1, 1)
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
        ("Yes", None, MAPPED_WORKPLACE, PENDING_MISSING_START_DATE),
        ("Yes", None, UNMAPPED_WORKPLACE, PENDING_MISSING_START_DATE),  # missing start_date checked before office
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
    assert classify(relocation_value, start_date, workplace) == expected


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
    # is added to posting.WORKPLACE_TO_OFFICE_NAME -- no special-casing
    # needed beyond PENDING_OFFICE_MAPPING not being terminal.
    assert is_terminal(PENDING_OFFICE_MAPPING) is False
    assert classify("Yes", SOME_DATE, UNMAPPED_WORKPLACE) == PENDING_OFFICE_MAPPING
