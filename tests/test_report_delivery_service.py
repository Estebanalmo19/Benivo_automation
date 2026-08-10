import base64
import json
import logging

import pytest
from unittest.mock import MagicMock, patch

from app.services import report_delivery_service

FAKE_WEBHOOK_URL = "https://prod-00.westeurope.logic.azure.com:443/workflows/abc123/triggers/manual/paths/invoke?api-version=2016-06-01&sp=%2Ftriggers%2Fmanual%2Frun&sv=1.0&sig=THIS_IS_A_SECRET_SIGNATURE_VALUE"

SAMPLE_METRICS = {
    "ready_to_post": 832,
    "posted": 1,
    "already_exists": 0,
    "failed": 0,
    "pending_recruiter_review": 5,
    "pending_office_mapping": 0,
    "country_fallback": 99,
}


@pytest.fixture
def xlsx_file(tmp_path):
    content = b"PK\x03\x04 fake xlsx bytes for testing, not a real zip archive"
    path = tmp_path / "benivo_operational_report_20260810T000000Z.xlsx"
    path.write_bytes(content)
    return path


def _mock_response(status_code, body=None):
    response = MagicMock(status_code=status_code)
    response.json.return_value = body or {}
    response.text = json.dumps(body or {})
    return response


# ---------------------------------------------------------------------------
# 1. Delivery disabled
# ---------------------------------------------------------------------------

def test_delivery_disabled_makes_zero_http_requests(xlsx_file, monkeypatch):
    monkeypatch.setattr(report_delivery_service.config, "report_delivery_enabled", lambda: False)

    with patch("app.services.report_delivery_service.requests.post") as mock_post:
        result = report_delivery_service.deliver_report(xlsx_file, "run-1", SAMPLE_METRICS)

    mock_post.assert_not_called()
    assert result == {"attempted": False, "success": None, "status_code": None, "file_name": xlsx_file.name, "error": None}


# ---------------------------------------------------------------------------
# 2. Delivery enabled -- exactly one HTTP request
# ---------------------------------------------------------------------------

def test_delivery_enabled_makes_exactly_one_http_request(xlsx_file, monkeypatch):
    monkeypatch.setattr(report_delivery_service.config, "report_delivery_enabled", lambda: True)
    monkeypatch.setattr(report_delivery_service.config, "report_webhook_url", lambda: FAKE_WEBHOOK_URL)

    with patch("app.services.report_delivery_service.requests.post", return_value=_mock_response(200)) as mock_post:
        report_delivery_service.deliver_report(xlsx_file, "run-1", SAMPLE_METRICS)

    mock_post.assert_called_once()


# ---------------------------------------------------------------------------
# 3 + 5. Correct JSON contract + file metadata
# ---------------------------------------------------------------------------

def test_json_contract_has_all_required_fields(xlsx_file, monkeypatch):
    monkeypatch.setattr(report_delivery_service.config, "report_delivery_enabled", lambda: True)
    monkeypatch.setattr(report_delivery_service.config, "report_webhook_url", lambda: FAKE_WEBHOOK_URL)

    with patch("app.services.report_delivery_service.requests.post", return_value=_mock_response(200)) as mock_post:
        report_delivery_service.deliver_report(xlsx_file, "run-42", SAMPLE_METRICS)

    payload = mock_post.call_args.kwargs["json"]

    assert set(payload.keys()) == {
        "fileName", "contentType", "fileContentBase64", "fileSizeBytes",
        "runId", "generatedAt", "reportType", "summary",
    }
    assert payload["contentType"] == "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    assert payload["runId"] == "run-42"
    assert payload["reportType"] == "Benivo Operational Report"
    assert payload["generatedAt"].endswith("Z")

    assert payload["summary"] == {
        "readyToPost": 832,
        "posted": 1,
        "alreadyExists": 0,
        "failed": 0,
        "pendingRecruiterReview": 5,
        "pendingOfficeMapping": 0,
        "countryFallback": 99,
    }


def test_file_metadata_matches_current_report_path(xlsx_file, monkeypatch):
    monkeypatch.setattr(report_delivery_service.config, "report_delivery_enabled", lambda: True)
    monkeypatch.setattr(report_delivery_service.config, "report_webhook_url", lambda: FAKE_WEBHOOK_URL)

    with patch("app.services.report_delivery_service.requests.post", return_value=_mock_response(200)) as mock_post:
        report_delivery_service.deliver_report(xlsx_file, "run-1", SAMPLE_METRICS)

    payload = mock_post.call_args.kwargs["json"]

    assert payload["fileName"] == xlsx_file.name
    assert payload["fileSizeBytes"] == xlsx_file.stat().st_size


def test_missing_metric_sent_as_null_not_invented(xlsx_file, monkeypatch):
    monkeypatch.setattr(report_delivery_service.config, "report_delivery_enabled", lambda: True)
    monkeypatch.setattr(report_delivery_service.config, "report_webhook_url", lambda: FAKE_WEBHOOK_URL)

    incomplete_metrics = {"ready_to_post": 5}  # e.g. posted/failed unavailable for this command

    with patch("app.services.report_delivery_service.requests.post", return_value=_mock_response(200)) as mock_post:
        report_delivery_service.deliver_report(xlsx_file, "run-1", incomplete_metrics)

    payload = mock_post.call_args.kwargs["json"]
    assert payload["summary"]["readyToPost"] == 5
    assert payload["summary"]["posted"] is None
    assert payload["summary"]["failed"] is None


# ---------------------------------------------------------------------------
# 4. Base64 integrity
# ---------------------------------------------------------------------------

def test_base64_content_decodes_to_exact_original_bytes(xlsx_file, monkeypatch):
    monkeypatch.setattr(report_delivery_service.config, "report_delivery_enabled", lambda: True)
    monkeypatch.setattr(report_delivery_service.config, "report_webhook_url", lambda: FAKE_WEBHOOK_URL)

    original_bytes = xlsx_file.read_bytes()

    with patch("app.services.report_delivery_service.requests.post", return_value=_mock_response(200)) as mock_post:
        report_delivery_service.deliver_report(xlsx_file, "run-1", SAMPLE_METRICS)

    payload = mock_post.call_args.kwargs["json"]
    decoded = base64.b64decode(payload["fileContentBase64"])

    assert decoded == original_bytes


# ---------------------------------------------------------------------------
# 6/7/8. HTTP 2xx = success (200, 202, other 2xx)
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("status_code", [200, 202, 204, 299])
def test_any_2xx_status_is_success(xlsx_file, monkeypatch, status_code):
    monkeypatch.setattr(report_delivery_service.config, "report_delivery_enabled", lambda: True)
    monkeypatch.setattr(report_delivery_service.config, "report_webhook_url", lambda: FAKE_WEBHOOK_URL)

    with patch("app.services.report_delivery_service.requests.post", return_value=_mock_response(status_code)):
        result = report_delivery_service.deliver_report(xlsx_file, "run-1", SAMPLE_METRICS)

    assert result == {"attempted": True, "success": True, "status_code": status_code, "file_name": xlsx_file.name, "error": None}


# ---------------------------------------------------------------------------
# 9/10. HTTP 4xx / 5xx = failure, no business-state side effects (this
# module has no access to candidate/post_log state at all -- there is
# nothing it could side-effect even if it wanted to)
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("status_code", [400, 404, 429])
def test_4xx_status_is_failure(xlsx_file, monkeypatch, status_code):
    monkeypatch.setattr(report_delivery_service.config, "report_delivery_enabled", lambda: True)
    monkeypatch.setattr(report_delivery_service.config, "report_webhook_url", lambda: FAKE_WEBHOOK_URL)

    with patch("app.services.report_delivery_service.requests.post", return_value=_mock_response(status_code)):
        result = report_delivery_service.deliver_report(xlsx_file, "run-1", SAMPLE_METRICS)

    assert result == {"attempted": True, "success": False, "status_code": status_code, "file_name": xlsx_file.name, "error": f"HTTP {status_code}"}
    assert xlsx_file.exists()  # generated report is never touched by delivery


@pytest.mark.parametrize("status_code", [500, 502, 503])
def test_5xx_status_is_failure(xlsx_file, monkeypatch, status_code):
    monkeypatch.setattr(report_delivery_service.config, "report_delivery_enabled", lambda: True)
    monkeypatch.setattr(report_delivery_service.config, "report_webhook_url", lambda: FAKE_WEBHOOK_URL)

    with patch("app.services.report_delivery_service.requests.post", return_value=_mock_response(status_code)):
        result = report_delivery_service.deliver_report(xlsx_file, "run-1", SAMPLE_METRICS)

    assert result == {"attempted": True, "success": False, "status_code": status_code, "file_name": xlsx_file.name, "error": f"HTTP {status_code}"}
    assert xlsx_file.exists()


# ---------------------------------------------------------------------------
# 11. Timeout / network exception -- clean failure, file preserved
# ---------------------------------------------------------------------------

def test_timeout_returns_clean_failure_and_preserves_file(xlsx_file, monkeypatch):
    import requests as requests_module

    monkeypatch.setattr(report_delivery_service.config, "report_delivery_enabled", lambda: True)
    monkeypatch.setattr(report_delivery_service.config, "report_webhook_url", lambda: FAKE_WEBHOOK_URL)

    with patch("app.services.report_delivery_service.requests.post", side_effect=requests_module.exceptions.Timeout("timed out")):
        result = report_delivery_service.deliver_report(xlsx_file, "run-1", SAMPLE_METRICS)

    assert result["attempted"] is True
    assert result["success"] is False
    assert result["status_code"] is None
    assert result["error"] == "Request timed out"
    assert xlsx_file.exists()


def test_network_exception_returns_clean_failure_and_preserves_file(xlsx_file, monkeypatch):
    import requests as requests_module

    monkeypatch.setattr(report_delivery_service.config, "report_delivery_enabled", lambda: True)
    monkeypatch.setattr(report_delivery_service.config, "report_webhook_url", lambda: FAKE_WEBHOOK_URL)

    with patch(
        "app.services.report_delivery_service.requests.post",
        side_effect=requests_module.exceptions.ConnectionError(f"Failed to connect to {FAKE_WEBHOOK_URL}"),
    ):
        result = report_delivery_service.deliver_report(xlsx_file, "run-1", SAMPLE_METRICS)

    assert result["attempted"] is True
    assert result["success"] is False
    assert result["error"] == "Network error"  # never str(exc) -- that could leak the URL
    assert xlsx_file.exists()


def test_unexpected_exception_never_raises_out_of_deliver_report(xlsx_file, monkeypatch):
    monkeypatch.setattr(report_delivery_service.config, "report_delivery_enabled", lambda: True)
    monkeypatch.setattr(report_delivery_service.config, "report_webhook_url", lambda: FAKE_WEBHOOK_URL)

    with patch("app.services.report_delivery_service.requests.post", side_effect=RuntimeError("boom")):
        result = report_delivery_service.deliver_report(xlsx_file, "run-1", SAMPLE_METRICS)  # must not raise

    assert result["attempted"] is True
    assert result["success"] is False
    assert xlsx_file.exists()


# ---------------------------------------------------------------------------
# 12. Missing webhook URL while enabled
# ---------------------------------------------------------------------------

def test_enabled_but_url_missing_returns_configuration_failure_without_crashing(xlsx_file, monkeypatch):
    monkeypatch.setattr(report_delivery_service.config, "report_delivery_enabled", lambda: True)
    monkeypatch.setattr(report_delivery_service.config, "report_webhook_url", lambda: None)

    with patch("app.services.report_delivery_service.requests.post") as mock_post:
        result = report_delivery_service.deliver_report(xlsx_file, "run-1", SAMPLE_METRICS)  # must not raise

    mock_post.assert_not_called()
    assert result["success"] is False
    assert result["error"] == "BENIVO_REPORT_WEBHOOK_URL is not configured"
    assert xlsx_file.exists()


# ---------------------------------------------------------------------------
# 13. Security -- signed URL and Base64 content never appear in logs
# ---------------------------------------------------------------------------

def test_signed_url_never_appears_in_logs(xlsx_file, monkeypatch, caplog):
    monkeypatch.setattr(report_delivery_service.config, "report_delivery_enabled", lambda: True)
    monkeypatch.setattr(report_delivery_service.config, "report_webhook_url", lambda: FAKE_WEBHOOK_URL)

    with caplog.at_level(logging.DEBUG):
        with patch("app.services.report_delivery_service.requests.post", return_value=_mock_response(200)):
            report_delivery_service.deliver_report(xlsx_file, "run-1", SAMPLE_METRICS)

    all_log_text = "\n".join(record.getMessage() for record in caplog.records)
    assert FAKE_WEBHOOK_URL not in all_log_text
    assert "sig=THIS_IS_A_SECRET_SIGNATURE_VALUE" not in all_log_text
    assert "sig=" not in all_log_text


def test_signed_url_never_appears_in_logs_on_network_failure(xlsx_file, monkeypatch, caplog):
    import requests as requests_module

    monkeypatch.setattr(report_delivery_service.config, "report_delivery_enabled", lambda: True)
    monkeypatch.setattr(report_delivery_service.config, "report_webhook_url", lambda: FAKE_WEBHOOK_URL)

    with caplog.at_level(logging.DEBUG):
        with patch(
            "app.services.report_delivery_service.requests.post",
            side_effect=requests_module.exceptions.ConnectionError(f"Failed to connect to {FAKE_WEBHOOK_URL}"),
        ):
            report_delivery_service.deliver_report(xlsx_file, "run-1", SAMPLE_METRICS)

    all_log_text = "\n".join(record.getMessage() for record in caplog.records)
    assert FAKE_WEBHOOK_URL not in all_log_text
    assert "sig=" not in all_log_text


def test_base64_content_never_appears_in_logs(xlsx_file, monkeypatch, caplog):
    monkeypatch.setattr(report_delivery_service.config, "report_delivery_enabled", lambda: True)
    monkeypatch.setattr(report_delivery_service.config, "report_webhook_url", lambda: FAKE_WEBHOOK_URL)

    original_bytes = xlsx_file.read_bytes()
    expected_b64 = base64.b64encode(original_bytes).decode("ascii")

    with caplog.at_level(logging.DEBUG):
        with patch("app.services.report_delivery_service.requests.post", return_value=_mock_response(200)):
            report_delivery_service.deliver_report(xlsx_file, "run-1", SAMPLE_METRICS)

    all_log_text = "\n".join(record.getMessage() for record in caplog.records)
    assert expected_b64 not in all_log_text


def test_disabled_log_message_matches_allowed_format(xlsx_file, monkeypatch, caplog):
    monkeypatch.setattr(report_delivery_service.config, "report_delivery_enabled", lambda: False)

    with caplog.at_level(logging.INFO):
        report_delivery_service.deliver_report(xlsx_file, "run-1", SAMPLE_METRICS)

    assert "Report delivery disabled." in [r.getMessage() for r in caplog.records]


def test_success_log_message_includes_filename_and_status(xlsx_file, monkeypatch, caplog):
    monkeypatch.setattr(report_delivery_service.config, "report_delivery_enabled", lambda: True)
    monkeypatch.setattr(report_delivery_service.config, "report_webhook_url", lambda: FAKE_WEBHOOK_URL)

    with caplog.at_level(logging.INFO):
        with patch("app.services.report_delivery_service.requests.post", return_value=_mock_response(202)):
            report_delivery_service.deliver_report(xlsx_file, "run-1", SAMPLE_METRICS)

    messages = [r.getMessage() for r in caplog.records]
    assert any(xlsx_file.name in m and "202" in m and "succeeded" in m for m in messages)


# ---------------------------------------------------------------------------
# 14. Current-report guarantee -- exact report_path is sent, nothing scanned
# ---------------------------------------------------------------------------

def test_delivers_exact_report_path_never_scans_directory(tmp_path, monkeypatch):
    monkeypatch.setattr(report_delivery_service.config, "report_delivery_enabled", lambda: True)
    monkeypatch.setattr(report_delivery_service.config, "report_webhook_url", lambda: FAKE_WEBHOOK_URL)

    # An OLDER report sitting in the same directory -- must never be picked.
    old_report = tmp_path / "benivo_operational_report_20200101T000000Z.xlsx"
    old_report.write_bytes(b"OLD REPORT CONTENT")

    current_report = tmp_path / "benivo_operational_report_20260810T170308Z.xlsx"
    current_report.write_bytes(b"CURRENT REPORT CONTENT")

    with patch("app.services.report_delivery_service.requests.post", return_value=_mock_response(200)) as mock_post:
        report_delivery_service.deliver_report(current_report, "run-1", SAMPLE_METRICS)

    payload = mock_post.call_args.kwargs["json"]
    assert payload["fileName"] == current_report.name
    assert base64.b64decode(payload["fileContentBase64"]) == b"CURRENT REPORT CONTENT"


# ---------------------------------------------------------------------------
# 15. Single-delivery guarantee (orchestration boundary) -- see also
# tests/test_main.py::test_generate_and_deliver_report_calls_delivery_exactly_once
# ---------------------------------------------------------------------------

def test_single_call_produces_single_http_attempt(xlsx_file, monkeypatch):
    monkeypatch.setattr(report_delivery_service.config, "report_delivery_enabled", lambda: True)
    monkeypatch.setattr(report_delivery_service.config, "report_webhook_url", lambda: FAKE_WEBHOOK_URL)

    with patch("app.services.report_delivery_service.requests.post", return_value=_mock_response(200)) as mock_post:
        report_delivery_service.deliver_report(xlsx_file, "run-1", SAMPLE_METRICS)

    assert mock_post.call_count == 1


# ---------------------------------------------------------------------------
# HTTP implementation requirements: explicit timeout, JSON content-type
# ---------------------------------------------------------------------------

def test_request_uses_explicit_timeout_and_json_content_type(xlsx_file, monkeypatch):
    monkeypatch.setattr(report_delivery_service.config, "report_delivery_enabled", lambda: True)
    monkeypatch.setattr(report_delivery_service.config, "report_webhook_url", lambda: FAKE_WEBHOOK_URL)

    with patch("app.services.report_delivery_service.requests.post", return_value=_mock_response(200)) as mock_post:
        report_delivery_service.deliver_report(xlsx_file, "run-1", SAMPLE_METRICS)

    call = mock_post.call_args
    assert call.kwargs["timeout"] == report_delivery_service.REQUEST_TIMEOUT_SECONDS
    assert call.kwargs["headers"]["Content-Type"] == "application/json"
    assert call.args[0] == FAKE_WEBHOOK_URL or call.kwargs.get("url") == FAKE_WEBHOOK_URL
