import datetime
from unittest.mock import patch

import scripts.validate_ready_candidates_refdata as validator

SOME_DATE = datetime.date(2026, 1, 1)

REFDATA = {
    "offices": [
        {"id": "office-uae-1", "officeName": "UAE (Live Casino)"},
        {"id": "office-serbia-1", "officeName": "Serbia (Live Casino)"},
    ]
}

VS = "scripts.validate_ready_candidates_refdata"


def _candidate(**overrides):
    base = {
        "application_eid": "APP-1",
        "candidate_eid": "CAND-1",
        "email": "jane@example.com",
        "first_name": "Jane",
        "last_name": "Doe",
        "workplace": "Serbia Live Casino",
        "start_date": SOME_DATE,
        "home_country": "Serbia",
        "current_country": "Serbia",
        "dealer_shuffler": None,
        "is_vip": False,
    }
    base.update(overrides)
    return base


def _no_writes_patches():
    """
    Every write-capable Benivo/DB/report call this script must never reach.
    Returned as a list of unittest.mock.patch context managers for callers
    to enter together.
    """
    return [
        patch("app.clients.benivo_client.create_user"),
        patch("app.clients.benivo_client.update_case"),
        patch("app.clients.benivo_client.find_user_by_email"),
        patch("app.repositories.post_log_repository.insert_post_log_row"),
        patch("app.repositories.candidate_repository.update_candidate_after_posting"),
        patch("app.services.report_delivery_service.deliver_report"),
    ]


# ---------------------------------------------------------------------------
# Safety: only token + refdata are used, every write path is unreachable
# ---------------------------------------------------------------------------

def test_run_validation_only_calls_token_and_refdata_never_writes():
    no_write_patches = _no_writes_patches()
    mocks = [p.start() for p in no_write_patches]

    try:
        with patch(f"{VS}.get_ready_candidates", return_value=[_candidate()]), \
             patch("app.clients.benivo_client.get_access_token", return_value="tok-123") as mock_token, \
             patch("app.clients.benivo_client.get_refdata", return_value=REFDATA) as mock_refdata:
            summary = validator.run_validation()

        mock_token.assert_called_once_with()
        mock_refdata.assert_called_once_with("tok-123")
        assert summary["total"] == 1
    finally:
        for p in no_write_patches:
            p.stop()

    for mock in mocks:
        mock.assert_not_called()


def test_main_never_writes_and_never_delivers_report(capsys):
    no_write_patches = _no_writes_patches()
    mocks = [p.start() for p in no_write_patches]

    try:
        with patch(f"{VS}.get_ready_candidates", return_value=[_candidate()]), \
             patch("app.clients.benivo_client.get_access_token", return_value="tok-123"), \
             patch("app.clients.benivo_client.get_refdata", return_value=REFDATA), \
             patch("app.config.validate"):
            exit_code = validator.main([])
    finally:
        for p in no_write_patches:
            p.stop()

    for mock in mocks:
        mock.assert_not_called()

    assert exit_code == 0
    out = capsys.readouterr().out
    assert "READ-ONLY REFDATA VALIDATION" in out
    assert "Benivo business writes: DISABLED" in out
    assert "Database writes:        DISABLED" in out
    assert "Report delivery:        DISABLED" in out


def test_run_validation_stops_on_refdata_failure_no_alternative_endpoint():
    with patch(f"{VS}.get_ready_candidates", return_value=[_candidate()]), \
         patch("app.clients.benivo_client.get_access_token", side_effect=RuntimeError("token failed")) as mock_token, \
         patch("app.clients.benivo_client.get_refdata") as mock_refdata:
        try:
            validator.run_validation()
            raised = False
        except RuntimeError:
            raised = True

    assert raised is True
    mock_token.assert_called_once()
    mock_refdata.assert_not_called()


def test_main_fails_closed_on_missing_configuration(capsys):
    with patch("app.config.validate", side_effect=RuntimeError("Missing required configuration: BENIVO_CLIENT_ID")), \
         patch(f"{VS}.get_ready_candidates") as mock_get_candidates, \
         patch("app.clients.benivo_client.get_access_token") as mock_token, \
         patch("app.clients.benivo_client.get_refdata") as mock_refdata:
        exit_code = validator.main([])

    assert exit_code == 1
    mock_get_candidates.assert_not_called()
    mock_token.assert_not_called()
    mock_refdata.assert_not_called()
    assert "Configuration error" in capsys.readouterr().out


def test_main_stops_validation_on_refdata_failure(capsys):
    with patch(f"{VS}.get_ready_candidates", return_value=[_candidate()]), \
         patch("app.clients.benivo_client.get_access_token", return_value="tok-123"), \
         patch("app.clients.benivo_client.get_refdata", side_effect=RuntimeError("refdata down")), \
         patch("app.config.validate"):
        exit_code = validator.main([])

    assert exit_code == 1
    assert "RefData retrieval failed" in capsys.readouterr().out


# ---------------------------------------------------------------------------
# Office resolution aggregation
# ---------------------------------------------------------------------------

def test_resolved_office_is_aggregated_correctly():
    with patch(f"{VS}.get_ready_candidates", return_value=[_candidate(workplace="Serbia Live Casino")]), \
         patch("app.clients.benivo_client.get_access_token", return_value="tok"), \
         patch("app.clients.benivo_client.get_refdata", return_value=REFDATA):
        summary = validator.run_validation()

    assert summary["office_resolved"] == 1
    assert summary["office_unresolved"] == 0

    entry = summary["workplace_summary"]["Serbia Live Casino"]
    assert entry["status"] == "RESOLVED"
    assert entry["resolved_office_name"] == "Serbia (Live Casino)"
    assert entry["resolved_office_id"] == "office-serbia-1"
    assert entry["expected_mapped_office_name"] == "Serbia (Live Casino)"
    assert entry["candidate_count"] == 1


def test_unresolved_workplace_is_reported_correctly():
    # "Nowhere Casino" has no entry in WORKPLACE_TO_OFFICE_NAME at all.
    candidate = _candidate(application_eid="APP-2", workplace="Nowhere Casino")

    with patch(f"{VS}.get_ready_candidates", return_value=[candidate]), \
         patch("app.clients.benivo_client.get_access_token", return_value="tok"), \
         patch("app.clients.benivo_client.get_refdata", return_value=REFDATA):
        summary = validator.run_validation()

    assert summary["office_resolved"] == 0
    assert summary["office_unresolved"] == 1

    entry = summary["workplace_summary"]["Nowhere Casino"]
    assert entry["status"] == "UNRESOLVED"
    assert entry["resolved_office_name"] is None
    assert entry["resolved_office_id"] is None
    assert entry["expected_mapped_office_name"] is None

    # An unresolved office means no officeId/officeName -- payload can't be ready.
    assert summary["payload_not_ready"] == 1
    assert "officeId" in summary["missing_field_reasons"]
    assert "officeName" in summary["missing_field_reasons"]
    assert summary["failed_eids"] == ["APP-2"]


def test_mapped_but_missing_from_live_refdata_is_unresolved():
    # "Malta" IS in WORKPLACE_TO_OFFICE_NAME, but "Malta (Global)" is not in
    # this particular refdata response -- must resolve to UNRESOLVED, never
    # invented from the local mapping alone.
    candidate = _candidate(application_eid="APP-3", workplace="Malta")

    with patch(f"{VS}.get_ready_candidates", return_value=[candidate]), \
         patch("app.clients.benivo_client.get_access_token", return_value="tok"), \
         patch("app.clients.benivo_client.get_refdata", return_value=REFDATA):
        summary = validator.run_validation()

    entry = summary["workplace_summary"]["Malta"]
    assert entry["status"] == "UNRESOLVED"
    assert entry["expected_mapped_office_name"] == "Malta (Global)"
    assert entry["resolved_office_name"] is None


# ---------------------------------------------------------------------------
# Payload readiness
# ---------------------------------------------------------------------------

def test_payload_ready_when_office_resolved_and_fields_present():
    with patch(f"{VS}.get_ready_candidates", return_value=[_candidate()]), \
         patch("app.clients.benivo_client.get_access_token", return_value="tok"), \
         patch("app.clients.benivo_client.get_refdata", return_value=REFDATA):
        summary = validator.run_validation()

    assert summary["payload_ready"] == 1
    assert summary["payload_not_ready"] == 0
    assert summary["failed_eids"] == []


def test_payload_missing_email_is_reported_with_reason():
    candidate = _candidate(application_eid="APP-4", email=None)

    with patch(f"{VS}.get_ready_candidates", return_value=[candidate]), \
         patch("app.clients.benivo_client.get_access_token", return_value="tok"), \
         patch("app.clients.benivo_client.get_refdata", return_value=REFDATA):
        summary = validator.run_validation()

    assert summary["payload_ready"] == 0
    assert summary["payload_not_ready"] == 1
    assert summary["missing_field_reasons"].get("email") == 1
    assert summary["failed_eids"] == ["APP-4"]

    result = summary["results"][0]
    assert result["office_resolved"] is True  # office resolution itself is unaffected by a missing email
    assert "email" in result["missing_fields"]


def test_total_ready_candidates_not_hardcoded():
    candidates = [
        _candidate(application_eid=f"APP-{i}", workplace="Serbia Live Casino") for i in range(7)
    ]

    with patch(f"{VS}.get_ready_candidates", return_value=candidates) as mock_get, \
         patch("app.clients.benivo_client.get_access_token", return_value="tok"), \
         patch("app.clients.benivo_client.get_refdata", return_value=REFDATA):
        summary = validator.run_validation()

    assert summary["total"] == 7
    mock_get.assert_called_once_with(limit=1000)


def test_zero_ready_candidates_skips_refdata_call_entirely():
    with patch(f"{VS}.get_ready_candidates", return_value=[]), \
         patch("app.clients.benivo_client.get_access_token") as mock_token, \
         patch("app.clients.benivo_client.get_refdata") as mock_refdata:
        summary = validator.run_validation()

    assert summary["total"] == 0
    mock_token.assert_not_called()
    mock_refdata.assert_not_called()
