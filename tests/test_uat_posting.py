import datetime
from unittest.mock import MagicMock, patch

from app.services import posting_service as posting

SOME_DATE = datetime.date(2026, 1, 1)

UAT_OFFICES = [{"id": "68da6b8b-1e07-4742-9333-a882e284c3fb", "officeName": "Serbia (Live Casino)"}]

PS = "app.services.posting_service"
BENIVO_POST = "app.clients.benivo_client.requests.post"
BENIVO_GET = "app.clients.benivo_client.requests.get"

# Default patch for the FINAL POSTING SAFETY GATE's live source re-check
# (candidate_repository.get_source_workflow_state(), confirmed 2026-09-08)
# -- "Mobility in process" for every test that doesn't specifically exercise
# this new check, matching pre-existing eligible-candidate expectations.
SOURCE_WORKFLOW_STATE_MOBILITY = patch(f"{PS}.get_source_workflow_state", return_value="Mobility in process")


def _eligible_candidate(**overrides):
    base = {
        "application_eid": "APP-UAT-1",
        "candidate_eid": "CAND-1",
        "email": "jane@example.com",
        "first_name": "Jane",
        "last_name": "Doe",
        "workflow_state": "Mobility in process",
        "is_relocation_required": "Yes",
        "start_date": SOME_DATE,
        "workplace": "Serbia Live Casino",
        "benivo_status": "READY_TO_POST",
        "is_vip": False,
    }
    base.update(overrides)
    return base


# ---------------------------------------------------------------------------
# validate_uat_candidate() -- every condition checked individually
# ---------------------------------------------------------------------------

def test_validate_uat_candidate_all_conditions_pass():
    with patch(f"{PS}.get_candidate_by_application_eid", return_value=_eligible_candidate()), \
         patch(f"{PS}.get_terminal_post_log_application_eids", return_value=set()), \
         SOURCE_WORKFLOW_STATE_MOBILITY:
        result = posting.validate_uat_candidate("APP-UAT-1", refdata={"offices": UAT_OFFICES})

    assert result["eligible"] is True
    assert all(result["checks"].values())
    # No dealer_shuffler, is_vip False -> Tier 3 (confirmed 2026-09-04 rule).
    assert result["summary"]["population_name"] == "Tier 3"
    assert result["summary"]["population_api_value"] == "Tier 3"
    assert result["summary"]["resolved_office_id"] == "68da6b8b-1e07-4742-9333-a882e284c3fb"
    assert result["summary"]["email_masked"] == "j***@example.com"


def test_post_log_preserves_both_business_population_name_and_api_value():
    # Confirms both the business label AND the exact API value survive into
    # the post_log row -- policy_name/policy_api_value are the fixed DB
    # column names but now hold population_name/population_api_value (see
    # build_post_log_insert()'s docstring).
    candidate = {"application_eid": "APP-UAT-1", "candidate_eid": "CAND-1", "email": "jane@example.com", "is_vip": False}
    result = {
        "outcome": "success", "request_payload": {}, "response_payload": {},
        "error_message": None, "benivo_user_id": 1, "benivo_assignment_id": 2, "benivo_profile_url": None,
    }

    row = posting.build_post_log_insert("run-123", candidate, result)

    assert row["policy_name"] == "Tier 3"
    assert row["policy_api_value"] == "Tier 3"


def test_validate_uat_candidate_not_found():
    with patch(f"{PS}.get_candidate_by_application_eid", return_value=None):
        result = posting.validate_uat_candidate("MISSING-EID", refdata={"offices": UAT_OFFICES})

    assert result["eligible"] is False
    assert result["checks"] == {"candidate_exists": False}
    assert result["candidate"] is None


def test_validate_uat_candidate_wrong_workflow_state():
    candidate = _eligible_candidate(workflow_state="Hired")
    with patch(f"{PS}.get_candidate_by_application_eid", return_value=candidate), \
         patch(f"{PS}.get_terminal_post_log_application_eids", return_value=set()), \
         SOURCE_WORKFLOW_STATE_MOBILITY:
        result = posting.validate_uat_candidate("APP-UAT-1", refdata={"offices": UAT_OFFICES})

    assert result["eligible"] is False
    assert result["checks"]["workflow_state_is_mobility_in_process"] is False


def test_validate_uat_candidate_relocation_not_yes():
    candidate = _eligible_candidate(is_relocation_required="No")
    with patch(f"{PS}.get_candidate_by_application_eid", return_value=candidate), \
         patch(f"{PS}.get_terminal_post_log_application_eids", return_value=set()), \
         SOURCE_WORKFLOW_STATE_MOBILITY:
        result = posting.validate_uat_candidate("APP-UAT-1", refdata={"offices": UAT_OFFICES})

    assert result["eligible"] is False
    assert result["checks"]["is_relocation_required_is_yes"] is False


def test_validate_uat_candidate_missing_jobvite_start_date_is_now_eligible():
    # Rule change: a missing Jobvite start_date no longer blocks UAT
    # eligibility -- resolve_effective_start_date() supplies a calculated
    # fallback, so this candidate is fully eligible as long as every other
    # check passes (it does, per _eligible_candidate()'s defaults).
    candidate = _eligible_candidate(start_date=None)
    execution_timestamp = datetime.datetime(2026, 8, 6, tzinfo=datetime.timezone.utc)

    with patch(f"{PS}.get_candidate_by_application_eid", return_value=candidate), \
         patch(f"{PS}.get_terminal_post_log_application_eids", return_value=set()), \
         SOURCE_WORKFLOW_STATE_MOBILITY:
        result = posting.validate_uat_candidate("APP-UAT-1", refdata={"offices": UAT_OFFICES}, execution_timestamp=execution_timestamp)

    assert result["eligible"] is True
    assert result["checks"]["effective_start_date_resolved"] is True
    assert result["summary"]["start_date_source"] == "CALCULATED"
    assert result["summary"]["start_date"] == "2026-11-01"  # _format_start_date() on a plain date -> str(date)


def test_validate_uat_candidate_not_ready_to_post_status():
    candidate = _eligible_candidate(benivo_status="PENDING_MISSING_START_DATE")
    with patch(f"{PS}.get_candidate_by_application_eid", return_value=candidate), \
         patch(f"{PS}.get_terminal_post_log_application_eids", return_value=set()), \
         SOURCE_WORKFLOW_STATE_MOBILITY:
        result = posting.validate_uat_candidate("APP-UAT-1", refdata={"offices": UAT_OFFICES})

    assert result["eligible"] is False
    assert result["checks"]["benivo_status_is_ready_to_post"] is False


def test_validate_uat_candidate_already_has_terminal_post_log_result():
    with patch(f"{PS}.get_candidate_by_application_eid", return_value=_eligible_candidate()), \
         patch(f"{PS}.get_terminal_post_log_application_eids", return_value={"APP-UAT-1"}), \
         SOURCE_WORKFLOW_STATE_MOBILITY:
        result = posting.validate_uat_candidate("APP-UAT-1", refdata={"offices": UAT_OFFICES})

    assert result["eligible"] is False
    assert result["checks"]["no_terminal_post_log_result"] is False


def test_validate_uat_candidate_office_unresolved():
    candidate = _eligible_candidate(workplace="Some Unmapped Site")
    with patch(f"{PS}.get_candidate_by_application_eid", return_value=candidate), \
         patch(f"{PS}.get_terminal_post_log_application_eids", return_value=set()), \
         SOURCE_WORKFLOW_STATE_MOBILITY:
        result = posting.validate_uat_candidate("APP-UAT-1", refdata={"offices": UAT_OFFICES})

    assert result["eligible"] is False
    assert result["checks"]["office_resolved"] is False


def test_validate_uat_candidate_no_refdata_means_office_unresolved():
    with patch(f"{PS}.get_candidate_by_application_eid", return_value=_eligible_candidate()), \
         patch(f"{PS}.get_terminal_post_log_application_eids", return_value=set()), \
         SOURCE_WORKFLOW_STATE_MOBILITY:
        result = posting.validate_uat_candidate("APP-UAT-1", refdata=None)

    assert result["eligible"] is False
    assert result["checks"]["office_resolved"] is False


def test_validate_uat_candidate_vip_is_now_eligible():
    # Confirmed 2026-09-02: every Population value (Tier 1/Tier 3/Game
    # Presenters and Shufflers) is equally confirmed -- there is no more
    # "Basic only" restriction on the single-candidate UAT gate (that
    # restriction existed only because Tier 2/VIP was an unconfirmed guess
    # at the time; it is retired along with policy_service.py).
    candidate = _eligible_candidate(is_vip=True)
    with patch(f"{PS}.get_candidate_by_application_eid", return_value=candidate), \
         patch(f"{PS}.get_terminal_post_log_application_eids", return_value=set()), \
         SOURCE_WORKFLOW_STATE_MOBILITY:
        result = posting.validate_uat_candidate("APP-UAT-1", refdata={"offices": UAT_OFFICES})

    assert result["eligible"] is True
    assert result["checks"]["population_api_value_confirmed"] is True
    assert result["checks"]["payload_valid"] is True
    assert result["summary"]["population_name"] == "Tier 1"
    assert result["summary"]["population_api_value"] == "Tier 1"


def test_validate_uat_candidate_game_presenter_is_eligible():
    candidate = _eligible_candidate(dealer_shuffler="Presenter")
    with patch(f"{PS}.get_candidate_by_application_eid", return_value=candidate), \
         patch(f"{PS}.get_terminal_post_log_application_eids", return_value=set()), \
         SOURCE_WORKFLOW_STATE_MOBILITY:
        result = posting.validate_uat_candidate("APP-UAT-1", refdata={"offices": UAT_OFFICES})

    assert result["eligible"] is True
    assert result["summary"]["population_name"] == "Game Presenters and Shufflers"
    assert result["summary"]["population_api_value"] == "Game Presenters and Shufflers"


# ---------------------------------------------------------------------------
# FINAL POSTING SAFETY GATE, live source re-check (confirmed 2026-09-08,
# root-caused from application_eid=pP98MxwU / Babak Guliyev): the UAT
# single-candidate path bypasses get_ready_candidates()'s own bulk EXISTS
# check entirely, so it needs an equivalent live re-verification of its own.
# ---------------------------------------------------------------------------

def test_validate_uat_candidate_stale_cache_but_source_still_mobility_is_eligible():
    # Cached benivo.candidates.workflow_state says Mobility in process AND
    # the live source agrees -- fully eligible.
    with patch(f"{PS}.get_candidate_by_application_eid", return_value=_eligible_candidate()), \
         patch(f"{PS}.get_terminal_post_log_application_eids", return_value=set()), \
         patch(f"{PS}.get_source_workflow_state", return_value="Mobility in process"):
        result = posting.validate_uat_candidate("APP-UAT-1", refdata={"offices": UAT_OFFICES})

    assert result["eligible"] is True
    assert result["checks"]["source_still_mobility_in_process"] is True


def test_validate_uat_candidate_stale_cache_but_source_moved_on_is_ineligible():
    # THE CRITICAL CASE: cached benivo.candidates.workflow_state still says
    # "Mobility in process" (stale), but the live Jobvite source has moved
    # to "Offer rescinded" -- must be blocked, exactly the pP98MxwU scenario.
    candidate = _eligible_candidate()  # cached workflow_state == "Mobility in process"
    with patch(f"{PS}.get_candidate_by_application_eid", return_value=candidate), \
         patch(f"{PS}.get_terminal_post_log_application_eids", return_value=set()), \
         patch(f"{PS}.get_source_workflow_state", return_value="Offer rescinded"):
        result = posting.validate_uat_candidate("APP-UAT-1", refdata={"offices": UAT_OFFICES})

    assert result["eligible"] is False
    assert result["checks"]["workflow_state_is_mobility_in_process"] is True  # cache alone looked fine
    assert result["checks"]["source_still_mobility_in_process"] is False  # live re-check catches it


def test_validate_uat_candidate_source_row_missing_entirely_is_ineligible():
    candidate = _eligible_candidate()
    with patch(f"{PS}.get_candidate_by_application_eid", return_value=candidate), \
         patch(f"{PS}.get_terminal_post_log_application_eids", return_value=set()), \
         patch(f"{PS}.get_source_workflow_state", return_value=None):
        result = posting.validate_uat_candidate("APP-UAT-1", refdata={"offices": UAT_OFFICES})

    assert result["eligible"] is False
    assert result["checks"]["source_still_mobility_in_process"] is False


def test_validate_payload_requires_all_fields():
    assert posting._validate_payload({
        "firstName": "Jane", "lastName": "Doe", "email": "j@example.com",
        "policy": "Basic", "officeId": "id-1", "startDateOfAssignment": "2026-01-01",
    }) is True

    assert posting._validate_payload({
        "firstName": "Jane", "lastName": "Doe", "email": "j@example.com",
        "policy": "Basic", "officeId": None, "startDateOfAssignment": "2026-01-01",
    }) is False


# ---------------------------------------------------------------------------
# select_postable_candidates() -- BENIVO_UAT_APPLICATION_EID override
# ---------------------------------------------------------------------------

def test_select_postable_candidates_uses_normal_path_when_uat_eid_unset(monkeypatch):
    monkeypatch.delenv("BENIVO_UAT_APPLICATION_EID", raising=False)

    with patch(f"{PS}.get_ready_candidates", return_value=[]) as mock_ready:
        posting.select_postable_candidates()

    mock_ready.assert_called_once()


def test_select_postable_candidates_never_calls_normal_path_when_uat_eid_set(monkeypatch):
    monkeypatch.setenv("BENIVO_UAT_APPLICATION_EID", "APP-UAT-1")

    token_response = MagicMock(status_code=200)
    token_response.json.return_value = {"access_token": "fake-token"}
    refdata_response = MagicMock(status_code=200)
    refdata_response.json.return_value = {"hasError": False, "data": {"offices": UAT_OFFICES}}

    with patch(f"{PS}.get_ready_candidates") as mock_ready, \
         patch(BENIVO_POST, return_value=token_response), \
         patch(BENIVO_GET, return_value=refdata_response), \
         patch(f"{PS}.get_candidate_by_application_eid", return_value=_eligible_candidate()), \
         patch(f"{PS}.get_terminal_post_log_application_eids", return_value=set()), \
         SOURCE_WORKFLOW_STATE_MOBILITY:
        result = posting.select_postable_candidates(limit=1)

    mock_ready.assert_not_called()
    assert len(result) == 1
    assert result[0]["application_eid"] == "APP-UAT-1"


def test_select_postable_candidates_returns_empty_and_never_falls_back_on_failed_validation(monkeypatch):
    monkeypatch.setenv("BENIVO_UAT_APPLICATION_EID", "APP-UAT-1")

    token_response = MagicMock(status_code=200)
    token_response.json.return_value = {"access_token": "fake-token"}
    refdata_response = MagicMock(status_code=200)
    refdata_response.json.return_value = {"hasError": False, "data": {"offices": UAT_OFFICES}}

    ineligible_candidate = _eligible_candidate(benivo_status="PENDING_MISSING_START_DATE")

    with patch(f"{PS}.get_ready_candidates") as mock_ready, \
         patch(BENIVO_POST, return_value=token_response), \
         patch(BENIVO_GET, return_value=refdata_response), \
         patch(f"{PS}.get_candidate_by_application_eid", return_value=ineligible_candidate), \
         patch(f"{PS}.get_terminal_post_log_application_eids", return_value=set()), \
         SOURCE_WORKFLOW_STATE_MOBILITY:
        result = posting.select_postable_candidates(limit=1)

    assert result == []
    mock_ready.assert_not_called()  # never falls back to the normal bulk pool
