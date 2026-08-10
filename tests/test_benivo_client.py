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


def test_update_case_reports_success():
    mock_response = MagicMock(status_code=200, content=b"{}")
    mock_response.json.return_value = {"hasError": False}

    payload = {"caseId": 1010644, "hostJobRole": "Engineer", "homeLocation": {"country": "Serbia"}}

    with patch("app.clients.benivo_client.requests.patch", return_value=mock_response) as mock_patch:
        result = benivo_client.update_case("fake-token", payload)

    assert result["success"] is True
    mock_patch.assert_called_once()
    call_args, call_kwargs = mock_patch.call_args
    assert call_args[0] == benivo_client.CASE_URL
    assert call_kwargs["json"] == payload


def test_update_case_reports_failure_on_has_error():
    mock_response = MagicMock(status_code=200, content=b"{}")
    mock_response.json.return_value = {"hasError": True, "message": "boom"}

    with patch("app.clients.benivo_client.requests.patch", return_value=mock_response):
        result = benivo_client.update_case("fake-token", {"caseId": 1010644})

    assert result["success"] is False
    assert result["error"] is not None


def test_update_case_reports_failure_on_non_200():
    mock_response = MagicMock(status_code=500, content=b"{}")
    mock_response.json.return_value = {"message": "server error"}

    with patch("app.clients.benivo_client.requests.patch", return_value=mock_response):
        result = benivo_client.update_case("fake-token", {"caseId": 1010644})

    assert result["success"] is False
