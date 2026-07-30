import datetime
from unittest.mock import MagicMock, patch

import psycopg2
import pytest

from app.models.domain import TERMINAL_POST_LOG_STATUSES
from app.services import posting_service as posting

BENIVO_POST = "app.clients.benivo_client.requests.post"
BENIVO_GET = "app.clients.benivo_client.requests.get"


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
        result = posting.post_single_candidate("fake-token", candidate, refdata)

    assert result["outcome"] == "already_exists"
    mock_post.assert_called_once()  # only the lookup call, create_user never invoked


def test_post_single_candidate_fails_when_office_cannot_be_resolved():
    candidate = {"application_eid": "APP-2", "email": "x@example.com", "workplace": "Unknown Office"}

    with patch(BENIVO_POST) as mock_post:
        result = posting.post_single_candidate("fake-token", candidate, refdata={"offices": []})

    assert result["outcome"] == "failed"
    mock_post.assert_not_called()  # never even attempts an API call without a resolved office


def test_unresolved_office_prevents_posting():
    candidate = {"application_eid": "APP-3", "email": "x@example.com", "workplace": "Nonexistent Office"}

    with patch(BENIVO_POST) as mock_post:
        result = posting.post_single_candidate("fake-token", candidate, refdata={"offices": []})

    assert result["outcome"] == "failed"
    assert result["benivo_user_id"] is None
    mock_post.assert_not_called()  # never attempts create-user or lookup without a resolved office


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


def test_dry_run_preview_includes_policy_name(monkeypatch):
    monkeypatch.delenv("BENIVO_ALLOW_REFERENCE_DATA_CALLS", raising=False)
    candidates = [{"application_eid": "APP-1", "email": "jane@example.com", "first_name": "Jane", "last_name": "Doe", "workplace": "Serbia Live Casino", "start_date": None, "is_vip": False}]

    with patch(BENIVO_POST) as mock_post, patch(BENIVO_GET) as mock_get:
        results = posting.post_candidates(candidates, dry_run=True)

    mock_post.assert_not_called()
    mock_get.assert_not_called()
    assert results[0]["policy_name"] == "Basic"
    assert results[0]["policy_api_value"] == "Tier 1"
    assert results[0]["is_vip"] is False
    assert results[0]["payload"]["policy"] == "Tier 1"


# ---------------------------------------------------------------------------
# build_benivo_payload() -- policy_api_value, never the business label
# ---------------------------------------------------------------------------

def test_build_benivo_payload_sends_policy_api_value_not_business_label():
    # payload["policy"] must be the exact Benivo API value ("Tier 1"), never
    # the business label "Basic" -- that literal string is what got the
    # first real UAT attempt rejected ("Policy is misspelled").
    candidate_basic = {"first_name": "Jane", "last_name": "Doe", "email": "j@example.com", "is_vip": False, "start_date": None}
    office = {"officeId": "id-1", "officeName": "Serbia (Live Casino)"}

    assert posting.build_benivo_payload(candidate_basic, office)["policy"] == "Tier 1"


def test_build_benivo_payload_vip_has_no_confirmed_api_value_so_policy_is_none():
    # No confirmed Benivo API value exists for VIP -- payload["policy"] must
    # be None (never guessed), which blocks the payload from validating.
    candidate_vip = {"first_name": "Jane", "last_name": "Doe", "email": "j@example.com", "is_vip": True, "start_date": None}
    office = {"officeId": "id-1", "officeName": "Serbia (Live Casino)"}

    payload = posting.build_benivo_payload(candidate_vip, office)
    assert payload["policy"] is None
    assert posting._validate_payload(payload) is False


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
    assert row["policy_name"] == "Basic"
    assert row["policy_api_value"] == "Tier 1"
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
    assert row["policy_name"] == "VIP"
    assert row["policy_api_value"] is None  # unconfirmed for VIP -- must never be guessed


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
    }

    with patch("app.services.posting_service.transaction", return_value=FakeTransaction()) as mock_transaction:
        posting.record_post_result(candidate, result, run_id="run-123")

    mock_transaction.assert_called_once()  # exactly one transaction for both statements
    assert mock_cursor.execute.call_count == 2

    insert_sql, insert_params = mock_cursor.execute.call_args_list[0][0]
    assert "INSERT INTO benivo.post_log" in insert_sql
    assert "posted_at" in insert_sql
    assert "processed_at" not in insert_sql
    assert insert_params[0] == "run-123"  # run_id
    assert insert_params[1] == "APP-1"  # application_eid
    assert insert_params[2] == "CAND-1"  # candidate_eid
    assert insert_params[3] == "jane@example.com"  # email
    assert insert_params[4] == "CREATE_USER"  # action
    assert insert_params[5] == "SUCCESS"  # status
    assert insert_params[6] is None  # is_vip (candidate has no is_vip key)
    assert insert_params[7] == "Basic"  # policy_name (None is_vip still resolves to Basic)
    assert insert_params[8] == "Tier 1"  # policy_api_value

    update_sql, update_params = mock_cursor.execute.call_args_list[1][0]
    assert "UPDATE benivo.candidates" in update_sql
    assert update_params[0] == "POSTED"
    assert update_params[-1] == "APP-1"


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
