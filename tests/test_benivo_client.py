from unittest.mock import MagicMock, patch

import pytest

from app.clients import benivo_client


def test_get_access_token_uses_mocked_response_only():
    mock_response = MagicMock(status_code=200)
    mock_response.json.return_value = {"access_token": "fake-token"}

    with patch("app.clients.benivo_client.requests.post", return_value=mock_response) as mock_post:
        token = benivo_client.get_access_token()

    assert token == "fake-token"
    mock_post.assert_called_once()


def test_get_access_token_raises_on_non_200():
    mock_response = MagicMock(status_code=401, text="unauthorized")

    with patch("app.clients.benivo_client.requests.post", return_value=mock_response):
        with pytest.raises(RuntimeError):
            benivo_client.get_access_token()


def test_find_user_by_email_reports_found():
    mock_response = MagicMock(status_code=200, content=b"{}")
    mock_response.json.return_value = {
        "hasError": False,
        "data": {
            "user": {"email": "jane@example.com", "benivoId": 123},
            "assignments": [{"assignmentId": 456}],
        },
    }

    with patch("app.clients.benivo_client.requests.post", return_value=mock_response):
        result = benivo_client.find_user_by_email("fake-token", "jane@example.com")

    assert result["found"] is True
    assert result["benivo_user_id"] == 123
    assert result["benivo_assignment_id"] == 456


def test_create_user_reports_success():
    mock_response = MagicMock(status_code=200, content=b"{}")
    mock_response.json.return_value = {
        "hasError": False,
        "data": [{"benivoId": 605070, "assignmentId": 1010644, "email": "jane@example.com"}],
    }

    with patch("app.clients.benivo_client.requests.post", return_value=mock_response):
        result = benivo_client.create_user("fake-token", {"firstName": "Jane"})

    assert result["success"] is True
    assert result["created"]["benivoId"] == 605070


def test_update_case_reports_success_on_200_with_json():
    mock_response = MagicMock(status_code=200, content=b"{}")
    mock_response.json.return_value = {"hasError": False}

    payload = {
        "findBy": {"caseId": 1010644},
        "data": {"hostJobRole": "Engineer", "homeLocation": {"country": "Serbia"}},
    }

    with patch("app.clients.benivo_client.requests.patch", return_value=mock_response) as mock_patch:
        result = benivo_client.update_case("fake-token", payload)

    assert result["success"] is True
    assert result["status_code"] == 200
    assert result["error"] is None
    mock_patch.assert_called_once()
    call_args, call_kwargs = mock_patch.call_args
    assert call_args[0] == benivo_client.CASE_URL
    assert call_kwargs["json"] == payload


def test_update_case_reports_success_on_202():
    # Any 2xx is transport success unless the body explicitly says
    # hasError=true -- see the 2026-08-21 fix.
    mock_response = MagicMock(status_code=202, content=b"{}")
    mock_response.json.return_value = {"hasError": False}

    with patch("app.clients.benivo_client.requests.patch", return_value=mock_response):
        result = benivo_client.update_case("fake-token", {"findBy": {"caseId": 1010644}})

    assert result["success"] is True
    assert result["status_code"] == 202
    assert result["error"] is None


def test_update_case_reports_success_on_204_no_content():
    # Root cause of the 2026-08-21 misreport: 204 No Content (a normal,
    # bodyless PATCH-success convention) was previously treated as a
    # failure because only status_code == 200 counted as success.
    mock_response = MagicMock(status_code=204, content=b"")

    with patch("app.clients.benivo_client.requests.patch", return_value=mock_response) as mock_patch:
        result = benivo_client.update_case("fake-token", {"findBy": {"caseId": 1010644}})

    assert result["success"] is True
    assert result["status_code"] == 204
    assert result["raw_response"] == {}
    assert result["error"] is None
    mock_response.json.assert_not_called()  # empty content must never be parsed as JSON
    mock_patch.assert_called_once()


def test_update_case_reports_failure_on_has_error():
    mock_response = MagicMock(status_code=200, content=b"{}")
    mock_response.json.return_value = {"hasError": True, "message": "boom"}

    with patch("app.clients.benivo_client.requests.patch", return_value=mock_response):
        result = benivo_client.update_case("fake-token", {"caseId": 1010644})

    assert result["success"] is False
    assert result["status_code"] == 200
    assert result["error"] is not None


def test_update_case_reports_failure_on_400_with_json_error():
    mock_response = MagicMock(status_code=400, content=b'{"code":4422}')
    mock_response.json.return_value = {"hasError": True, "code": 4422, "message": {"description": "bad country code"}}

    with patch("app.clients.benivo_client.requests.patch", return_value=mock_response):
        result = benivo_client.update_case("fake-token", {"caseId": 1010644})

    assert result["success"] is False
    assert result["status_code"] == 400
    assert "4422" in result["error"]


def test_update_case_reports_failure_on_500_with_empty_body():
    # A genuine server error with an empty body must still be
    # distinguishable from the 204-success case via status_code/success,
    # even though raw_response is {} in both.
    mock_response = MagicMock(status_code=500, content=b"")

    with patch("app.clients.benivo_client.requests.patch", return_value=mock_response) as mock_patch:
        result = benivo_client.update_case("fake-token", {"caseId": 1010644})

    assert result["success"] is False
    assert result["status_code"] == 500
    assert result["raw_response"] == {}
    assert result["error"] is not None
    mock_response.json.assert_not_called()
