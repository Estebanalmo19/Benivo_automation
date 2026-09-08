import datetime
from unittest.mock import MagicMock, patch

import psycopg2
import pytest

from app.models.domain import TERMINAL_POST_LOG_STATUSES
from app.services import posting_service as posting

BENIVO_POST = "app.clients.benivo_client.requests.post"
BENIVO_GET = "app.clients.benivo_client.requests.get"
BENIVO_PATCH = "app.clients.benivo_client.requests.patch"

EXECUTION_TIMESTAMP = datetime.datetime(2026, 8, 6, tzinfo=datetime.timezone.utc)


# ---------------------------------------------------------------------------
# Terminal log exclusion / retry logic (pure mapping functions, no DB/HTTP)
# ---------------------------------------------------------------------------

def test_success_and_already_exists_are_terminal_and_block_retry():
    assert "SUCCESS" in TERMINAL_POST_LOG_STATUSES
    assert "ALREADY_EXISTS" in TERMINAL_POST_LOG_STATUSES


def test_failed_is_not_terminal_and_remains_retryable():
    assert "FAILED" not in TERMINAL_POST_LOG_STATUSES


@pytest.mark.parametrize(
    "outcome, expected_post_log_status, expected_candidate_status",
    [
        ("success", "SUCCESS", "POSTED"),
        ("already_exists", "ALREADY_EXISTS", "POSTED"),
        ("failed", "FAILED", "POST_FAILED"),
    ],
)
def test_outcome_to_status_mapping(outcome, expected_post_log_status, expected_candidate_status):
    post_log_status = posting._post_log_status_from_outcome(outcome)
    assert post_log_status == expected_post_log_status
    assert posting._candidate_status_from_post_log_status(post_log_status) == expected_candidate_status


def test_post_failed_is_not_terminal_so_it_stays_reclassifiable():
    # POST_FAILED must NOT be in classification_service.TERMINAL_STATUSES,
    # or a failed candidate would never be retried.
    from app.services.classification_service import is_terminal

    assert is_terminal("POST_FAILED") is False
    assert is_terminal("POSTED") is True


# ---------------------------------------------------------------------------
# BENIVO_MAX_CANDIDATES validation
# ---------------------------------------------------------------------------

def test_max_candidates_defaults_when_unset(monkeypatch):
    monkeypatch.delenv("BENIVO_MAX_CANDIDATES", raising=False)
    assert posting._get_max_candidates() == 1


@pytest.mark.parametrize("raw_value", ["0", "-5", "not-a-number", ""])
def test_max_candidates_falls_back_to_default_on_invalid_value(monkeypatch, raw_value):
    monkeypatch.setenv("BENIVO_MAX_CANDIDATES", raw_value)
    assert posting._get_max_candidates() == 1


def test_max_candidates_accepts_positive_integer(monkeypatch):
    monkeypatch.setenv("BENIVO_MAX_CANDIDATES", "5")
    assert posting._get_max_candidates() == 5


# ---------------------------------------------------------------------------
# BENIVO_DRY_RUN default
# ---------------------------------------------------------------------------

def test_dry_run_defaults_true_when_unset(monkeypatch):
    monkeypatch.delenv("BENIVO_DRY_RUN", raising=False)
    assert posting.is_dry_run() is True


def test_dry_run_false_only_when_explicit(monkeypatch):
    monkeypatch.setenv("BENIVO_DRY_RUN", "false")
    assert posting.is_dry_run() is False


# ---------------------------------------------------------------------------
# post_single_candidate() -- mocked Benivo API, no real HTTP requests
# ---------------------------------------------------------------------------

def test_post_single_candidate_reports_already_exists_without_calling_create():
    lookup_response = MagicMock(status_code=200, content=b"{}")
    lookup_response.json.return_value = {
        "hasError": False,
        "data": {"user": {"email": "jane@example.com", "benivoId": 1}, "assignments": []},
    }

    refdata = {"offices": [{"id": "office-1", "officeName": "Colombia (Live Casino)"}]}
    candidate = {
        "application_eid": "APP-1",
        "email": "jane@example.com",
        "first_name": "Jane",
        "last_name": "Doe",
        "workplace": "Colombia Live Casino",
        "start_date": datetime.date(2026, 1, 1),
        "is_vip": False,
    }

    with patch(BENIVO_POST, return_value=lookup_response) as mock_post:
        result = posting.post_single_candidate("fake-token", candidate, refdata, EXECUTION_TIMESTAMP)

    assert result["outcome"] == "already_exists"
    mock_post.assert_called_once()  # only the lookup call, create_user never invoked


def test_post_single_candidate_fails_when_office_cannot_be_resolved():
    candidate = {"application_eid": "APP-2", "email": "x@example.com", "workplace": "Unknown Office"}

    with patch(BENIVO_POST) as mock_post:
        result = posting.post_single_candidate("fake-token", candidate, refdata={"offices": []}, execution_timestamp=EXECUTION_TIMESTAMP)

    assert result["outcome"] == "failed"
    mock_post.assert_not_called()  # never even attempts an API call without a resolved office


def test_unresolved_office_prevents_posting():
    candidate = {"application_eid": "APP-3", "email": "x@example.com", "workplace": "Nonexistent Office"}

    with patch(BENIVO_POST) as mock_post:
        result = posting.post_single_candidate("fake-token", candidate, refdata={"offices": []}, execution_timestamp=EXECUTION_TIMESTAMP)

    assert result["outcome"] == "failed"
    assert result["benivo_user_id"] is None
    mock_post.assert_not_called()  # never attempts create-user or lookup without a resolved office


def test_post_single_candidate_includes_start_date_audit_fields():
    candidate = {
        "application_eid": "APP-4",
        "email": "x@example.com",
        "workplace": "Unknown Office",
        "start_date": None,
    }

    with patch(BENIVO_POST):
        result = posting.post_single_candidate("fake-token", candidate, refdata={"offices": []}, execution_timestamp=EXECUTION_TIMESTAMP)

    assert result["execution_date"] == EXECUTION_TIMESTAMP
    assert result["effective_start_date"] == datetime.date(2026, 11, 1)
    assert result["start_date_source"] == "CALCULATED"


# ---------------------------------------------------------------------------
# Dry run never calls create-user or user-lookup, under any configuration
# ---------------------------------------------------------------------------

def test_dry_run_never_calls_create_user_or_lookup_with_reference_data_disabled(monkeypatch):
    monkeypatch.delenv("BENIVO_ALLOW_REFERENCE_DATA_CALLS", raising=False)
    candidates = [{"application_eid": "APP-1", "email": "jane@example.com", "first_name": "Jane", "last_name": "Doe", "workplace": "Serbia Live Casino", "start_date": None, "is_vip": False}]

    with patch(BENIVO_POST) as mock_post, patch(BENIVO_GET) as mock_get:
        results = posting.post_candidates(candidates, dry_run=True)

    mock_post.assert_not_called()
    mock_get.assert_not_called()
    assert results[0]["office_resolved"] is False


def test_dry_run_with_reference_data_enabled_fetches_refdata_but_never_creates_user(monkeypatch):
    monkeypatch.setenv("BENIVO_ALLOW_REFERENCE_DATA_CALLS", "true")

    token_response = MagicMock(status_code=200)
    token_response.json.return_value = {"access_token": "fake-token"}

    refdata_response = MagicMock(status_code=200)
    refdata_response.json.return_value = {"hasError": False, "data": {"offices": [{"id": "off-1", "officeName": "Serbia (Live Casino)"}]}}

    candidates = [{"application_eid": "APP-1", "email": "jane@example.com", "first_name": "Jane", "last_name": "Doe", "workplace": "Serbia Live Casino", "start_date": None, "is_vip": False}]

    with patch(BENIVO_POST, return_value=token_response) as mock_post, patch(BENIVO_GET, return_value=refdata_response) as mock_get:
        results = posting.post_candidates(candidates, dry_run=True)

    mock_get.assert_called_once()  # refdata GET happened
    mock_post.assert_called_once()  # only the token POST -- never create_user/lookup
    assert results[0]["office_resolved"] is True
    assert results[0]["payload"]["officeId"] == "off-1"
    assert results[0]["payload"]["officeName"] == "Serbia (Live Casino)"


def test_dry_run_preview_includes_population_name(monkeypatch):
    monkeypatch.delenv("BENIVO_ALLOW_REFERENCE_DATA_CALLS", raising=False)
    candidates = [{"application_eid": "APP-1", "email": "jane@example.com", "first_name": "Jane", "last_name": "Doe", "workplace": "Serbia Live Casino", "start_date": None, "is_vip": False}]

    with patch(BENIVO_POST) as mock_post, patch(BENIVO_GET) as mock_get:
        results = posting.post_candidates(candidates, dry_run=True)

    mock_post.assert_not_called()
    mock_get.assert_not_called()
    # No dealer_shuffler -> not a Game Presenter/Shuffler; is_vip False -> Tier 3.
    assert results[0]["population_name"] == "Tier 3"
    assert results[0]["population_api_value"] == "Tier 3"
    assert results[0]["is_vip"] is False
    assert results[0]["payload"]["policy"] == "Tier 3"


def test_dry_run_preview_includes_home_country_and_case_update_payload(monkeypatch):
    # Controlled dry-run validation for the Case API extension: no real
    # POST/PATCH is ever made in dry run (BENIVO_PATCH not even patched
    # here -- calling it would blow up the test if it were somehow
    # invoked), but the preview must already show homeCountry on the
    # create-user payload and the exact Case PATCH shape that would be
    # sent (caseId=None, since it's only known after a real create-user).
    monkeypatch.delenv("BENIVO_ALLOW_REFERENCE_DATA_CALLS", raising=False)
    candidates = [
        {
            "application_eid": "APP-1",
            "email": "jane@example.com",
            "first_name": "Jane",
            "last_name": "Doe",
            "workplace": "Serbia Live Casino",
            "start_date": None,
            "is_vip": False,
            "job_title": "Game Presenter",
            "home_country": "Serbia",
        }
    ]

    with patch(BENIVO_POST) as mock_post, patch(BENIVO_GET) as mock_get:
        results = posting.post_candidates(candidates, dry_run=True)

    mock_post.assert_not_called()
    mock_get.assert_not_called()
    assert results[0]["payload"]["homeCountry"] == "Serbia"
    assert results[0]["case_update_payload_preview"] == {
        "findBy": {"caseId": None},
        "data": {
            "hostJobRole": "Game Presenter",
            "homeLocation": {"country": "RS"},
        },
    }


# ---------------------------------------------------------------------------
# Effective start date resolution surfaced through the dry-run preview
# ---------------------------------------------------------------------------

def test_dry_run_preview_reports_calculated_source_when_jobvite_date_missing(monkeypatch):
    monkeypatch.delenv("BENIVO_ALLOW_REFERENCE_DATA_CALLS", raising=False)
    candidates = [{"application_eid": "APP-1", "email": "jane@example.com", "first_name": "Jane", "last_name": "Doe", "workplace": "Serbia Live Casino", "start_date": None, "is_vip": False}]

    with patch(BENIVO_POST) as mock_post, patch(BENIVO_GET) as mock_get:
        results = posting.post_candidates(candidates, dry_run=True)

    mock_post.assert_not_called()
    mock_get.assert_not_called()
    assert results[0]["start_date_source"] == "CALCULATED"
    assert results[0]["effective_start_date"] is not None
    assert results[0]["payload"]["startDateOfAssignment"] is not None  # never left blank -- see resolve_effective_start_date()


def test_dry_run_preview_reports_jobvite_source_when_start_date_present(monkeypatch):
    monkeypatch.delenv("BENIVO_ALLOW_REFERENCE_DATA_CALLS", raising=False)
    candidates = [{"application_eid": "APP-1", "email": "jane@example.com", "first_name": "Jane", "last_name": "Doe", "workplace": "Serbia Live Casino", "start_date": datetime.date(2026, 3, 1), "is_vip": False}]

    with patch(BENIVO_POST) as mock_post, patch(BENIVO_GET) as mock_get:
        results = posting.post_candidates(candidates, dry_run=True)

    assert results[0]["start_date_source"] == "JOBVITE"
    assert results[0]["effective_start_date"] == "2026-03-01"
    assert results[0]["payload"]["startDateOfAssignment"] == "2026-03-01"


def test_post_candidates_generates_execution_timestamp_once_per_run(monkeypatch):
    # Multiple candidates, none with a Jobvite start_date -- every one must
    # land on the exact same calculated date because post_candidates()
    # generates execution_timestamp exactly once for the whole run.
    #
    # A real datetime subclass (not a plain MagicMock) is used here so
    # posting_service._format_start_date()'s isinstance(x, datetime) check
    # keeps working correctly while datetime.now() is frozen.
    monkeypatch.delenv("BENIVO_ALLOW_REFERENCE_DATA_CALLS", raising=False)
    candidates = [
        {"application_eid": f"APP-{i}", "email": f"a{i}@example.com", "first_name": "A", "last_name": "B", "workplace": "Serbia Live Casino", "start_date": None, "is_vip": False}
        for i in range(4)
    ]

    call_count = {"n": 0}
    fixed_now = datetime.datetime(2026, 8, 6, tzinfo=datetime.timezone.utc)

    class FrozenDatetime(datetime.datetime):
        @classmethod
        def now(cls, tz=None):
            call_count["n"] += 1
            return fixed_now

    with patch(BENIVO_POST), patch(BENIVO_GET), patch("app.services.posting_service.datetime", FrozenDatetime):
        results = posting.post_candidates(candidates, dry_run=True)

    assert call_count["n"] == 1
    assert {r["effective_start_date"] for r in results} == {"2026-11-01"}
    assert {r["start_date_source"] for r in results} == {"CALCULATED"}


# ---------------------------------------------------------------------------
# build_benivo_payload() -- population_api_value, never a fabricated label
# ---------------------------------------------------------------------------

def test_build_benivo_payload_non_vip_non_gp_shuffler_sends_tier_3():
    # Confirmed 2026-09-04 business rule: no Game Presenter/Shuffler
    # dealer_shuffler value and mobility_vip != Yes -> Population "Tier 3".
    candidate = {"first_name": "Jane", "last_name": "Doe", "email": "j@example.com", "is_vip": False, "start_date": None}
    office = {"officeId": "id-1", "officeName": "Serbia (Live Casino)"}

    assert posting.build_benivo_payload(candidate, office, datetime.date(2026, 1, 1))["policy"] == "Tier 3"


def test_build_benivo_payload_vip_non_gp_shuffler_resolves_to_tier_1_and_validates():
    # Confirmed 2026-09-04 business rule: no Game Presenter/Shuffler
    # dealer_shuffler value and mobility_vip == Yes -> Population "Tier 1".
    candidate_vip = {"first_name": "Jane", "last_name": "Doe", "email": "j@example.com", "is_vip": True, "start_date": None}
    office = {"officeId": "id-1", "officeName": "Serbia (Live Casino)"}

    payload = posting.build_benivo_payload(candidate_vip, office, datetime.date(2026, 1, 1))
    assert payload["policy"] == "Tier 1"
    assert posting._validate_payload(payload) is True


def test_build_benivo_payload_presenter_overrides_vip_status():
    # Confirmed 2026-09-04 business rule: Game Presenter/Shuffler wins
    # regardless of is_vip -- role is checked first.
    candidate = {
        "first_name": "Jane", "last_name": "Doe", "email": "j@example.com",
        "is_vip": True, "start_date": None, "dealer_shuffler": "Presenter",
    }
    office = {"officeId": "id-1", "officeName": "Serbia (Live Casino)"}

    payload = posting.build_benivo_payload(candidate, office, datetime.date(2026, 1, 1))
    assert payload["policy"] == "Game Presenters and Shufflers"


def test_build_benivo_payload_shuffler_sends_gp_and_shufflers():
    candidate = {
        "first_name": "Jane", "last_name": "Doe", "email": "j@example.com",
        "is_vip": False, "start_date": None, "dealer_shuffler": "Shuffler",
    }
    office = {"officeId": "id-1", "officeName": "Serbia (Live Casino)"}

    payload = posting.build_benivo_payload(candidate, office, datetime.date(2026, 1, 1))
    assert payload["policy"] == "Game Presenters and Shufflers"


def test_build_benivo_payload_dealer_is_gp_and_shufflers():
    # Confirmed 2026-09-04 (second round): dealer_shuffler is a controlled
    # selector -- "Dealer" IS a real catalog value and now counts as Game
    # Presenters and Shufflers, regardless of is_vip. Retired the narrower
    # Presenter/Shuffler-only allowlist from the first round.
    candidate = {
        "first_name": "Jane", "last_name": "Doe", "email": "j@example.com",
        "is_vip": True, "start_date": None, "dealer_shuffler": "Dealer",
    }
    office = {"officeId": "id-1", "officeName": "Serbia (Live Casino)"}

    payload = posting.build_benivo_payload(candidate, office, datetime.date(2026, 1, 1))
    assert payload["policy"] == "Game Presenters and Shufflers"


def test_build_benivo_payload_na_dealer_shuffler_falls_back_to_is_vip_rule():
    # The field's own "n/a" placeholder means "not a dealer/presenter/
    # shuffler role" -- falls through to the is_vip rule, unlike a real
    # catalog value.
    candidate = {
        "first_name": "Jane", "last_name": "Doe", "email": "j@example.com",
        "is_vip": True, "start_date": None, "dealer_shuffler": "n/a",
    }
    office = {"officeId": "id-1", "officeName": "Serbia (Live Casino)"}

    payload = posting.build_benivo_payload(candidate, office, datetime.date(2026, 1, 1))
    assert payload["policy"] == "Tier 1"


def test_build_benivo_payload_sends_effective_start_date_not_raw_candidate_start_date():
    # build_benivo_payload() is a pure formatter: it must use the
    # already-resolved effective_start_date it's given, never re-derive it
    # from candidate["start_date"] (which may be None here even though a
    # calculated date was already resolved upstream).
    candidate = {"first_name": "Jane", "last_name": "Doe", "email": "j@example.com", "is_vip": False, "start_date": None}
    office = {"officeId": "id-1", "officeName": "Serbia (Live Casino)"}

    payload = posting.build_benivo_payload(candidate, office, datetime.date(2026, 11, 1))

    assert payload["startDateOfAssignment"] == "2026-11-01"


def test_build_benivo_payload_never_includes_job_title():
    # Job Title investigation (completed): no confirmed Benivo API field
    # name exists in create-user, so it must never be sent there (it goes
    # to the Case PATCH's hostJobRole instead -- see build_case_update_payload).
    candidate = {"first_name": "Jane", "last_name": "Doe", "email": "j@example.com", "is_vip": False, "job_title": "Business Intelligence (BI) Specialist"}
    office = {"officeId": "id-1", "officeName": "Serbia (Live Casino)"}

    payload = posting.build_benivo_payload(candidate, office, datetime.date(2026, 1, 1))

    assert "jobTitle" not in payload
    assert "job_title" not in payload


def test_build_benivo_payload_includes_home_country():
    # Confirmed 2026-08-10 by Gina (Benivo): create-user must populate
    # homeCountry from candidates.home_country.
    candidate = {"first_name": "Jane", "last_name": "Doe", "email": "j@example.com", "is_vip": False, "home_country": "Serbia"}
    office = {"officeId": "id-1", "officeName": "Serbia (Live Casino)"}

    payload = posting.build_benivo_payload(candidate, office, datetime.date(2026, 1, 1))

    assert payload["homeCountry"] == "Serbia"


def test_build_benivo_payload_home_country_none_when_missing():
    candidate = {"first_name": "Jane", "last_name": "Doe", "email": "j@example.com", "is_vip": False}
    office = {"officeId": "id-1", "officeName": "Serbia (Live Casino)"}

    payload = posting.build_benivo_payload(candidate, office, datetime.date(2026, 1, 1))

    assert payload["homeCountry"] is None


def test_build_benivo_payload_falls_back_to_current_country():
    # Confirmed 2026-08-10 during the Country Data Issues investigation:
    # candidate_home_country has real gaps that Jobvite's own
    # current-location country field (current_country) reliably fills.
    candidate = {"first_name": "Jane", "last_name": "Doe", "email": "j@example.com", "is_vip": False, "home_country": None, "current_country": "Belarus"}
    office = {"officeId": "id-1", "officeName": "Serbia (Live Casino)"}

    payload = posting.build_benivo_payload(candidate, office, datetime.date(2026, 1, 1))

    assert payload["homeCountry"] == "Belarus"


def test_build_benivo_payload_prefers_home_country_over_current_country():
    candidate = {"first_name": "Jane", "last_name": "Doe", "email": "j@example.com", "is_vip": False, "home_country": "Georgia", "current_country": "Serbia"}
    office = {"officeId": "id-1", "officeName": "Serbia (Live Casino)"}

    payload = posting.build_benivo_payload(candidate, office, datetime.date(2026, 1, 1))

    assert payload["homeCountry"] == "Georgia"


# ---------------------------------------------------------------------------
# build_case_update_payload() -- only the confirmed Case PATCH fields
# ---------------------------------------------------------------------------

def test_build_case_update_payload_sends_only_confirmed_fields():
    candidate = {"job_title": "Game Presenter", "home_country": "Serbia"}

    payload = posting.build_case_update_payload(candidate, case_id=1010644)

    assert payload == {
        "findBy": {"caseId": 1010644},
        "data": {
            "hostJobRole": "Game Presenter",
            "homeLocation": {"country": "RS"},
        },
    }


def test_build_case_update_payload_handles_missing_fields():
    payload = posting.build_case_update_payload({}, case_id=None)

    assert payload == {
        "findBy": {"caseId": None},
        "data": {
            "hostJobRole": None,
            "homeLocation": {"country": None},
        },
    }


def test_build_case_update_payload_falls_back_to_current_country():
    candidate = {"job_title": "Game Presenter", "home_country": None, "current_country": "United Arab Emirates"}

    payload = posting.build_case_update_payload(candidate, case_id=1010644)

    assert payload["data"]["homeLocation"]["country"] == "AE"


def test_build_case_update_payload_converts_country_name_to_iso2():
    # Confirmed 2026-08-21 by a real Benivo UAT PATCH: error 4422 ("must be
    # a valid 2-character country code") if the full name is sent instead.
    candidate = {"job_title": "Game Presenter", "home_country": "United States"}

    payload = posting.build_case_update_payload(candidate, case_id=1010644)

    assert payload["data"]["homeLocation"]["country"] == "US"


def test_build_case_update_payload_country_none_when_unresolvable():
    # Fails safely: a country with no confirmed ISO mapping must never send
    # a guessed code -- None, same as any other unconfirmed field.
    candidate = {"job_title": "Game Presenter", "home_country": "Neverland"}

    payload = posting.build_case_update_payload(candidate, case_id=1010644)

    assert payload["data"]["homeLocation"]["country"] is None


def test_build_case_update_payload_agrees_with_create_user_payload_on_underlying_country():
    # Both payloads must resolve the same candidate's home country
    # identically before conversion -- see build_case_update_payload()'s
    # docstring. create-user sends the plain name; the Case PATCH converts
    # that exact same resolved name to its ISO 3166-1 alpha-2 code.
    candidate = {
        "first_name": "Jane", "last_name": "Doe", "email": "j@example.com", "is_vip": False,
        "job_title": "Game Presenter", "home_country": None, "current_country": "Belarus",
    }
    office = {"officeId": "id-1", "officeName": "Serbia (Live Casino)"}

    create_payload = posting.build_benivo_payload(candidate, office, datetime.date(2026, 1, 1))
    case_payload = posting.build_case_update_payload(candidate, case_id=1)

    assert create_payload["homeCountry"] == "Belarus"
    assert case_payload["data"]["homeLocation"]["country"] == "BY"


# ---------------------------------------------------------------------------
# post_single_candidate() -- Case PATCH follow-up after a successful create-user
# ---------------------------------------------------------------------------

def _lookup_not_found_response():
    response = MagicMock(status_code=200, content=b"{}")
    response.json.return_value = {"hasError": False, "data": {"user": None, "assignments": []}}
    return response


def _create_response(benivo_id=605070, assignment_id=1010644, email="jane@example.com"):
    response = MagicMock(status_code=200, content=b"{}")
    response.json.return_value = {
        "hasError": False,
        "data": [{"benivoId": benivo_id, "assignmentId": assignment_id, "email": email}],
    }
    return response


def test_post_single_candidate_calls_case_patch_after_successful_create_user():
    case_response = MagicMock(status_code=200, content=b"{}")
    case_response.json.return_value = {"hasError": False}

    refdata = {"offices": [{"id": "office-1", "officeName": "Colombia (Live Casino)"}]}
    candidate = {
        "application_eid": "APP-1",
        "email": "jane@example.com",
        "first_name": "Jane",
        "last_name": "Doe",
        "workplace": "Colombia Live Casino",
        "start_date": datetime.date(2026, 1, 1),
        "is_vip": False,
        "job_title": "Game Presenter",
        "home_country": "Serbia",
    }

    with patch(BENIVO_POST, side_effect=[_lookup_not_found_response(), _create_response()]), \
         patch(BENIVO_PATCH, return_value=case_response) as mock_patch:
        result = posting.post_single_candidate("fake-token", candidate, refdata, EXECUTION_TIMESTAMP)

    assert result["outcome"] == "success"
    assert result["status_code"] == 200  # top-level create-user HTTP status
    assert result["case_update"]["success"] is True
    assert result["case_update"]["status_code"] == 200
    assert result["case_update"]["case_id"] == 1010644
    assert result["case_update"]["request_payload"] == {
        "findBy": {"caseId": 1010644},
        "data": {
            "hostJobRole": "Game Presenter",
            "homeLocation": {"country": "RS"},
        },
    }
    mock_patch.assert_called_once()


def test_post_single_candidate_case_patch_succeeds_on_204_no_content():
    # Regression test for the 2026-08-21 misreport: a real Benivo 204
    # No Content response must be recorded as success=True, not FAILED.
    case_response = MagicMock(status_code=204, content=b"")

    refdata = {"offices": [{"id": "office-1", "officeName": "Colombia (Live Casino)"}]}
    candidate = {
        "application_eid": "APP-1",
        "email": "jane@example.com",
        "first_name": "Jane",
        "last_name": "Doe",
        "workplace": "Colombia Live Casino",
        "start_date": datetime.date(2026, 1, 1),
        "is_vip": False,
        "job_title": "Game Presenter",
        "home_country": "Serbia",
    }

    with patch(BENIVO_POST, side_effect=[_lookup_not_found_response(), _create_response()]), \
         patch(BENIVO_PATCH, return_value=case_response):
        result = posting.post_single_candidate("fake-token", candidate, refdata, EXECUTION_TIMESTAMP)

    assert result["case_update"]["success"] is True
    assert result["case_update"]["status_code"] == 204
    assert result["case_update"]["error_message"] is None
    assert result["case_update"]["response_payload"] == {}


def test_post_single_candidate_case_patch_failure_does_not_change_outcome():
    case_response = MagicMock(status_code=500, content=b"{}")
    case_response.json.return_value = {"message": "server error"}

    refdata = {"offices": [{"id": "office-1", "officeName": "Colombia (Live Casino)"}]}
    candidate = {
        "application_eid": "APP-1",
        "email": "jane@example.com",
        "first_name": "Jane",
        "last_name": "Doe",
        "workplace": "Colombia Live Casino",
        "start_date": datetime.date(2026, 1, 1),
        "is_vip": False,
        "job_title": "Game Presenter",
        "home_country": "Serbia",
    }

    with patch(BENIVO_POST, side_effect=[_lookup_not_found_response(), _create_response()]), \
         patch(BENIVO_PATCH, return_value=case_response):
        result = posting.post_single_candidate("fake-token", candidate, refdata, EXECUTION_TIMESTAMP)

    # create-user already succeeded -- a Case PATCH failure must never
    # change the create-user outcome (candidate remains POSTED downstream).
    assert result["outcome"] == "success"
    assert result["benivo_user_id"] == 605070
    assert result["case_update"]["success"] is False
    assert result["case_update"]["error_message"] is not None


def test_case_patch_not_attempted_when_user_already_exists():
    lookup_response = MagicMock(status_code=200, content=b"{}")
    lookup_response.json.return_value = {
        "hasError": False,
        "data": {"user": {"email": "jane@example.com", "benivoId": 1}, "assignments": [{"assignmentId": 99}]},
    }

    refdata = {"offices": [{"id": "office-1", "officeName": "Colombia (Live Casino)"}]}
    candidate = {
        "application_eid": "APP-1",
        "email": "jane@example.com",
        "first_name": "Jane",
        "last_name": "Doe",
        "workplace": "Colombia Live Casino",
        "start_date": datetime.date(2026, 1, 1),
        "is_vip": False,
    }

    with patch(BENIVO_POST, return_value=lookup_response), patch(BENIVO_PATCH) as mock_patch:
        result = posting.post_single_candidate("fake-token", candidate, refdata, EXECUTION_TIMESTAMP)

    assert result["outcome"] == "already_exists"
    assert "case_update" not in result
    mock_patch.assert_not_called()


def test_case_patch_not_attempted_when_office_unresolved():
    candidate = {"application_eid": "APP-2", "email": "x@example.com", "workplace": "Unknown Office"}

    with patch(BENIVO_POST), patch(BENIVO_PATCH) as mock_patch:
        result = posting.post_single_candidate("fake-token", candidate, refdata={"offices": []}, execution_timestamp=EXECUTION_TIMESTAMP)

    assert result["outcome"] == "failed"
    assert "case_update" not in result
    mock_patch.assert_not_called()


# ---------------------------------------------------------------------------
# post_log INSERT parameters (real schema) -- pure function, no DB
# ---------------------------------------------------------------------------

def test_build_post_log_insert_uses_real_columns():
    candidate = {"application_eid": "APP-1", "candidate_eid": "CAND-1", "email": "jane@example.com", "is_vip": False}
    result = {
        "outcome": "success",
        "request_payload": {"firstName": "Jane"},
        "response_payload": {"data": [{"benivoId": 1}]},
        "error_message": None,
        "benivo_user_id": 1,
        "benivo_assignment_id": 2,
        "benivo_profile_url": None,
    }

    row = posting.build_post_log_insert("run-123", candidate, result)

    assert row["run_id"] == "run-123"
    assert row["application_eid"] == "APP-1"
    assert row["candidate_eid"] == "CAND-1"
    assert row["email"] == "jane@example.com"
    assert row["action"] == "CREATE_USER"
    assert row["status"] == "SUCCESS"
    assert row["is_vip"] is False
    # policy_name/policy_api_value are the fixed post_log DB column names
    # (see build_post_log_insert()'s docstring) -- they now carry the
    # corrected Population value, not the retired Basic/VIP business label.
    assert row["policy_name"] == "Tier 3"
    assert row["policy_api_value"] == "Tier 3"
    assert row["benivo_user_id"] == 1
    assert row["benivo_assignment_id"] == 2
    assert row["request_payload"] == {"firstName": "Jane"}


def test_build_post_log_insert_vip_candidate():
    candidate = {"application_eid": "APP-VIP", "candidate_eid": "CAND-VIP", "email": "vip@example.com", "is_vip": True}
    result = {
        "outcome": "success",
        "request_payload": {"firstName": "VIP"},
        "response_payload": {},
        "error_message": None,
        "benivo_user_id": 9,
        "benivo_assignment_id": 10,
        "benivo_profile_url": None,
    }

    row = posting.build_post_log_insert("run-123", candidate, result)

    assert row["is_vip"] is True
    assert row["policy_name"] == "Tier 1"  # confirmed 2026-09-02 business rule
    assert row["policy_api_value"] == "Tier 1"


def test_build_post_log_insert_game_presenter_candidate():
    candidate = {
        "application_eid": "APP-GP", "candidate_eid": "CAND-GP", "email": "gp@example.com",
        "is_vip": False, "dealer_shuffler": "Presenter",
    }
    result = {
        "outcome": "success",
        "request_payload": {"firstName": "GP"},
        "response_payload": {},
        "error_message": None,
        "benivo_user_id": 9,
        "benivo_assignment_id": 10,
        "benivo_profile_url": None,
    }

    row = posting.build_post_log_insert("run-123", candidate, result)

    assert row["policy_name"] == "Game Presenters and Shufflers"
    assert row["policy_api_value"] == "Game Presenters and Shufflers"


def test_build_post_log_insert_includes_start_date_audit_fields():
    candidate = {"application_eid": "APP-1", "candidate_eid": "CAND-1", "email": "jane@example.com", "is_vip": False}
    result = {
        "outcome": "success",
        "request_payload": {"firstName": "Jane"},
        "response_payload": {"data": [{"benivoId": 1}]},
        "error_message": None,
        "benivo_user_id": 1,
        "benivo_assignment_id": 2,
        "benivo_profile_url": None,
        "execution_date": EXECUTION_TIMESTAMP,
        "effective_start_date": datetime.date(2026, 11, 1),
        "start_date_source": "CALCULATED",
    }

    row = posting.build_post_log_insert("run-123", candidate, result)

    assert row["execution_date"] == EXECUTION_TIMESTAMP
    assert row["effective_start_date"] == datetime.date(2026, 11, 1)
    assert row["start_date_source"] == "CALCULATED"


def test_build_post_log_insert_includes_http_status_code():
    candidate = {"application_eid": "APP-1", "candidate_eid": "CAND-1", "email": "jane@example.com", "is_vip": False}
    result = {
        "outcome": "success",
        "request_payload": {"firstName": "Jane"},
        "response_payload": {"data": [{"benivoId": 1}]},
        "error_message": None,
        "benivo_user_id": 1,
        "benivo_assignment_id": 2,
        "benivo_profile_url": None,
        "status_code": 200,
    }

    row = posting.build_post_log_insert("run-123", candidate, result)

    assert row["http_status_code"] == 200


def test_build_post_log_insert_http_status_code_none_when_absent():
    # Predates this rule / no create-user HTTP call happened on this path
    # (e.g. office-unresolved failure) -- must not KeyError.
    candidate = {"application_eid": "APP-1", "candidate_eid": "CAND-1", "email": "jane@example.com", "is_vip": False}
    result = {
        "outcome": "failed",
        "request_payload": None,
        "response_payload": None,
        "error_message": "no office",
        "benivo_user_id": None,
        "benivo_assignment_id": None,
        "benivo_profile_url": None,
    }

    row = posting.build_post_log_insert("run-123", candidate, result)

    assert row["http_status_code"] is None


def test_build_post_log_insert_defaults_start_date_audit_fields_to_none_when_absent():
    # Backward compatibility: a result dict built before this rule existed
    # (no execution_date/effective_start_date/start_date_source keys) must
    # not raise -- it just records NULL for the three new audit columns.
    candidate = {"application_eid": "APP-1", "candidate_eid": "CAND-1", "email": "jane@example.com", "is_vip": False}
    result = {
        "outcome": "success",
        "request_payload": {"firstName": "Jane"},
        "response_payload": {"data": [{"benivoId": 1}]},
        "error_message": None,
        "benivo_user_id": 1,
        "benivo_assignment_id": 2,
        "benivo_profile_url": None,
    }

    row = posting.build_post_log_insert("run-123", candidate, result)

    assert row["execution_date"] is None
    assert row["effective_start_date"] is None
    assert row["start_date_source"] is None


def test_build_post_log_insert_failed_outcome_maps_to_failed_status():
    candidate = {"application_eid": "APP-2", "candidate_eid": None, "email": None}
    result = {
        "outcome": "failed",
        "request_payload": None,
        "response_payload": None,
        "error_message": "office not resolved",
        "benivo_user_id": None,
        "benivo_assignment_id": None,
        "benivo_profile_url": None,
    }

    row = posting.build_post_log_insert("run-123", candidate, result)

    assert row["status"] == "FAILED"
    assert row["error_message"] == "office not resolved"


# ---------------------------------------------------------------------------
# build_case_update_post_log_insert() -- separate UPDATE_CASE audit row
# ---------------------------------------------------------------------------

def test_build_case_update_post_log_insert_success():
    candidate = {"application_eid": "APP-1", "candidate_eid": "CAND-1", "email": "jane@example.com", "is_vip": False}
    case_update = {
        "attempted": True,
        "success": True,
        "case_id": 1010644,
        "request_payload": {
            "findBy": {"caseId": 1010644},
            "data": {"hostJobRole": "Engineer", "homeLocation": {"country": "Serbia"}},
        },
        "response_payload": {"hasError": False},
        "error_message": None,
        "status_code": 204,
    }

    row = posting.build_case_update_post_log_insert("run-123", candidate, case_update)

    assert row["action"] == "UPDATE_CASE"
    assert row["status"] == "SUCCESS"
    assert row["benivo_assignment_id"] == 1010644
    assert row["request_payload"]["data"]["hostJobRole"] == "Engineer"
    assert row["policy_name"] is None  # no policy decision applies to a Case update
    assert row["policy_api_value"] is None
    assert row["http_status_code"] == 204


def test_build_case_update_post_log_insert_failure():
    candidate = {"application_eid": "APP-1", "candidate_eid": "CAND-1", "email": "jane@example.com", "is_vip": False}
    case_update = {
        "attempted": True,
        "success": False,
        "case_id": 1010644,
        "request_payload": {"findBy": {"caseId": 1010644}, "data": {}},
        "response_payload": {"hasError": True},
        "error_message": "boom",
    }

    row = posting.build_case_update_post_log_insert("run-123", candidate, case_update)

    assert row["action"] == "UPDATE_CASE"
    assert row["status"] == "FAILED"
    assert row["error_message"] == "boom"


# ---------------------------------------------------------------------------
# record_post_result() transaction behavior -- mocked DB, no real connection
# ---------------------------------------------------------------------------

def test_record_post_result_uses_one_transaction_for_both_writes():
    mock_cursor = MagicMock()

    class FakeTransaction:
        def __enter__(self):
            return mock_cursor

        def __exit__(self, *args):
            return False

    candidate = {"application_eid": "APP-1", "candidate_eid": "CAND-1", "email": "jane@example.com"}
    result = {
        "outcome": "success",
        "request_payload": {"firstName": "Jane"},
        "response_payload": {"ok": True},
        "error_message": None,
        "benivo_user_id": 1,
        "benivo_assignment_id": 2,
        "benivo_profile_url": None,
        "status_code": 200,
    }

    with patch("app.services.posting_service.transaction", return_value=FakeTransaction()) as mock_transaction:
        posting.record_post_result(candidate, result, run_id="run-123")

    mock_transaction.assert_called_once()  # exactly one transaction for both statements
    assert mock_cursor.execute.call_count == 2

    insert_sql, insert_params = mock_cursor.execute.call_args_list[0][0]
    assert "INSERT INTO benivo.post_log" in insert_sql
    assert "posted_at" in insert_sql
    assert "processed_at" not in insert_sql
    assert "http_status_code" in insert_sql
    assert insert_params[0] == "run-123"  # run_id
    assert insert_params[1] == "APP-1"  # application_eid
    assert insert_params[2] == "CAND-1"  # candidate_eid
    assert insert_params[3] == "jane@example.com"  # email
    assert insert_params[4] == "CREATE_USER"  # action
    assert insert_params[5] == "SUCCESS"  # status
    assert insert_params[6] is None  # is_vip (candidate has no is_vip key)
    assert insert_params[7] == "Tier 3"  # policy_name column, now holding population_name (None is_vip, no dealer_shuffler -> Tier 3)
    assert insert_params[8] == "Tier 3"  # policy_api_value column, now holding population_api_value
    assert insert_params[-1] == 200  # http_status_code -- last positional param before posted_at=NOW()

    update_sql, update_params = mock_cursor.execute.call_args_list[1][0]
    assert "UPDATE benivo.candidates" in update_sql
    assert update_params[0] == "POSTED"
    assert update_params[-1] == "APP-1"


def test_record_post_result_writes_case_update_row_when_present():
    # Case PATCH failed, but create-user succeeded -- three statements in
    # one transaction: CREATE_USER insert, UPDATE_CASE insert, candidate
    # UPDATE. Candidate status must still be driven by the CREATE_USER
    # outcome only, unaffected by the failed Case PATCH.
    mock_cursor = MagicMock()

    class FakeTransaction:
        def __enter__(self):
            return mock_cursor

        def __exit__(self, *args):
            return False

    candidate = {"application_eid": "APP-1", "candidate_eid": "CAND-1", "email": "jane@example.com"}
    result = {
        "outcome": "success",
        "request_payload": {"firstName": "Jane"},
        "response_payload": {"ok": True},
        "error_message": None,
        "benivo_user_id": 1,
        "benivo_assignment_id": 2,
        "benivo_profile_url": None,
        "case_update": {
            "attempted": True,
            "success": False,
            "case_id": 2,
            "request_payload": {
                "findBy": {"caseId": 2},
                "data": {"hostJobRole": None, "homeLocation": {"country": None}},
            },
            "response_payload": {"hasError": True},
            "error_message": "boom",
        },
    }

    with patch("app.services.posting_service.transaction", return_value=FakeTransaction()) as mock_transaction:
        posting.record_post_result(candidate, result, run_id="run-123")

    mock_transaction.assert_called_once()  # exactly one transaction for all three statements
    assert mock_cursor.execute.call_count == 3

    case_insert_sql, case_insert_params = mock_cursor.execute.call_args_list[1][0]
    assert "INSERT INTO benivo.post_log" in case_insert_sql
    assert case_insert_params[0] == "run-123"  # run_id
    assert case_insert_params[1] == "APP-1"  # application_eid
    assert case_insert_params[4] == "UPDATE_CASE"  # action
    assert case_insert_params[5] == "FAILED"  # status

    update_sql, update_params = mock_cursor.execute.call_args_list[2][0]
    assert "UPDATE benivo.candidates" in update_sql
    assert update_params[0] == "POSTED"  # driven by CREATE_USER outcome, not the failed case_update


def test_record_post_result_skips_case_update_row_when_absent():
    # Backward compatible: a result dict without "case_update" (already_exists/
    # failed outcomes, or results built before this rule existed) writes
    # exactly the original two statements.
    mock_cursor = MagicMock()

    class FakeTransaction:
        def __enter__(self):
            return mock_cursor

        def __exit__(self, *args):
            return False

    candidate = {"application_eid": "APP-1", "candidate_eid": "CAND-1", "email": "jane@example.com"}
    result = {
        "outcome": "already_exists",
        "request_payload": {"firstName": "Jane"},
        "response_payload": {"ok": True},
        "error_message": None,
        "benivo_user_id": 1,
        "benivo_assignment_id": 2,
        "benivo_profile_url": None,
    }

    with patch("app.services.posting_service.transaction", return_value=FakeTransaction()):
        posting.record_post_result(candidate, result, run_id="run-123")

    assert mock_cursor.execute.call_count == 2


def test_record_post_result_rolls_back_and_reraises_on_db_error():
    mock_cursor = MagicMock()
    mock_cursor.execute.side_effect = psycopg2.Error("boom")

    class FakeTransaction:
        def __enter__(self):
            return mock_cursor

        def __exit__(self, exc_type, exc_val, exc_tb):
            return False  # propagate, mirroring database_client.transaction()'s real rollback-then-reraise

    candidate = {"application_eid": "APP-1", "candidate_eid": None, "email": None}
    result = {"outcome": "failed", "request_payload": None, "response_payload": None, "error_message": "x", "benivo_user_id": None, "benivo_assignment_id": None, "benivo_profile_url": None}

    with patch("app.services.posting_service.transaction", return_value=FakeTransaction()):
        with pytest.raises(psycopg2.Error):
            posting.record_post_result(candidate, result, run_id="run-123")


# ---------------------------------------------------------------------------
# select_postable_candidates() -- SQL LIMIT enforcement (no real DB)
# ---------------------------------------------------------------------------

def test_select_postable_candidates_passes_limit_through_to_sql(monkeypatch):
    monkeypatch.delenv("BENIVO_UAT_APPLICATION_EID", raising=False)
    monkeypatch.setenv("BENIVO_MAX_CANDIDATES", "5")
    captured = {}

    def fake_get_ready_candidates(limit=None):
        captured["limit"] = limit
        return []

    with patch("app.services.posting_service.get_ready_candidates", side_effect=fake_get_ready_candidates):
        posting.select_postable_candidates()

    assert captured["limit"] == 5


# ---------------------------------------------------------------------------
# build_benivo_payload() -- agency_name is deliberately NEVER sent
# (confirmed 2026-09-08, Mihai/Mobility business request): Benivo has not
# confirmed a destination for Agency Name at all -- not a property name,
# not even which endpoint. Data-prepared (see synchronization_service.py/
# reporting_service.py), API-disabled. This must hold even when the
# candidate dict carries a real agency_name value, so the real Benivo
# Create User payload stays byte-for-byte unchanged.
# ---------------------------------------------------------------------------

def test_build_benivo_payload_never_includes_agency_name():
    candidate = {
        "first_name": "Jane", "last_name": "Doe", "email": "j@example.com", "is_vip": False,
        "agency_name": "Randstad Romania",
    }
    office = {"officeId": "id-1", "officeName": "Serbia (Live Casino)"}

    payload = posting.build_benivo_payload(candidate, office, datetime.date(2026, 1, 1))

    # Exactly the 8 confirmed Create User keys -- no agency key under any
    # name, and the agency value itself never leaks into any other field.
    assert set(payload.keys()) == {
        "firstName", "lastName", "email", "policy", "officeId", "officeName",
        "startDateOfAssignment", "homeCountry",
    }
    assert "Randstad Romania" not in payload.values()
