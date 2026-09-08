import json
from unittest.mock import patch

import scripts.validate_approved_batch as validate_script

VAB = "scripts.validate_approved_batch"


def _write_batch_file(tmp_path, data, filename="batch.json"):
    path = tmp_path / filename
    path.write_text(json.dumps(data), encoding="utf-8")
    return str(path)


def test_main_requires_exactly_one_argument(capsys):
    exit_code = validate_script.main([])
    assert exit_code == 1
    assert "Usage:" in capsys.readouterr().out


def test_main_fails_closed_on_missing_configuration(capsys):
    with patch("app.config.validate", side_effect=RuntimeError("Missing required configuration: DB_HOST")), \
         patch(f"{VAB}.load_approved_batch") as mock_load:
        exit_code = validate_script.main(["some/path.json"])

    assert exit_code == 1
    mock_load.assert_not_called()
    assert "Configuration error" in capsys.readouterr().out


def test_main_fails_closed_on_missing_batch_file(tmp_path, capsys):
    missing_path = str(tmp_path / "nope.json")

    with patch("app.config.validate"):
        exit_code = validate_script.main([missing_path])

    assert exit_code == 1
    assert "invalid" in capsys.readouterr().out.lower()


def test_main_reports_all_eligible_exit_zero(tmp_path, capsys):
    path = _write_batch_file(
        tmp_path,
        {"batch_name": "canary_20260908", "application_eids": ["APP-1", "APP-2", "APP-3"]},
    )
    eligible = [{"application_eid": eid} for eid in ["APP-1", "APP-2", "APP-3"]]

    with patch("app.config.validate"), \
         patch(f"{VAB}.evaluate_approved_batch", return_value={"eligible": eligible, "ineligible": []}) as mock_eval:
        exit_code = validate_script.main([path])

    mock_eval.assert_called_once_with(["APP-1", "APP-2", "APP-3"])
    assert exit_code == 0

    out = capsys.readouterr().out
    assert "BATCH_NAME = canary_20260908" in out
    assert "APPROVED_EIDS_COUNT = 3" in out
    assert "ELIGIBLE_APPROVED_COUNT = 3" in out
    assert "INELIGIBLE_APPROVED_COUNT = 0" in out
    assert "APP-1" in out and "APP-2" in out and "APP-3" in out


def test_main_reports_ineligible_reasons_and_exits_nonzero(tmp_path, capsys):
    path = _write_batch_file(
        tmp_path,
        {"batch_name": "canary_20260908", "application_eids": ["APP-1", "APP-2"]},
    )
    result = {
        "eligible": [{"application_eid": "APP-1"}],
        "ineligible": [{"application_eid": "APP-2", "reason": "jobvite_workflow_not_mobility"}],
    }

    with patch("app.config.validate"), \
         patch(f"{VAB}.evaluate_approved_batch", return_value=result):
        exit_code = validate_script.main([path])

    assert exit_code == 1

    out = capsys.readouterr().out
    assert "ELIGIBLE_APPROVED_COUNT = 1" in out
    assert "INELIGIBLE_APPROVED_COUNT = 1" in out
    assert "APP-2: jobvite_workflow_not_mobility" in out


def test_main_never_prints_pii(tmp_path, capsys):
    path = _write_batch_file(tmp_path, {"batch_name": "x", "application_eids": ["APP-1"]})
    eligible = [{
        "application_eid": "APP-1",
        "email": "jane.doe@example.com",
        "first_name": "Jane",
        "last_name": "Doe",
        "phone_number": "+123456789",
    }]

    with patch("app.config.validate"), \
         patch(f"{VAB}.evaluate_approved_batch", return_value={"eligible": eligible, "ineligible": []}):
        validate_script.main([path])

    out = capsys.readouterr().out
    assert "jane.doe@example.com" not in out
    assert "Jane" not in out
    assert "Doe" not in out
    assert "+123456789" not in out
    assert "@" not in out


def test_main_never_imports_benivo_client_or_report_delivery():
    import scripts.validate_approved_batch as module

    assert "benivo_client" not in vars(module)
    assert "reporting_service" not in vars(module)
    assert "report_delivery_service" not in vars(module)
    assert "posting_service" not in vars(module)
