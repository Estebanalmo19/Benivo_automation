from unittest.mock import patch

import pytest

from app import main as app_main


def test_main_returns_1_on_missing_configuration(monkeypatch):
    with patch("app.main.config.validate", side_effect=RuntimeError("Missing required configuration: DB_HOST")):
        assert app_main.main(["sync"]) == 1


def test_main_rejects_unknown_command():
    with patch("app.main.config.validate"):
        with pytest.raises(SystemExit):
            app_main.main(["not-a-real-command"])


def test_cmd_sync_returns_0_on_success():
    with patch("app.main.config.validate"), \
         patch("app.main.synchronization_service.sync_candidates", return_value={"inserted_or_updated": 5, "removed": 0}):
        assert app_main.main(["sync"]) == 0


def test_run_aborts_before_classify_if_sync_fails():
    with patch("app.main.config.validate"), \
         patch("app.main.synchronization_service.sync_candidates", side_effect=Exception("db down")) as mock_sync, \
         patch("app.main.classification_service.classify_candidates") as mock_classify:
        exit_code = app_main.main(["run"])

    assert exit_code == 1
    mock_sync.assert_called_once()
    mock_classify.assert_not_called()  # a failed candidate shouldn't crash the batch, but a fatal sync failure must stop the run


def test_run_aborts_before_posting_if_classify_fails():
    with patch("app.main.config.validate"), \
         patch("app.main.synchronization_service.sync_candidates", return_value={}), \
         patch("app.main.classification_service.classify_candidates", side_effect=Exception("boom")), \
         patch("app.main.posting_service.select_postable_candidates") as mock_select:
        exit_code = app_main.main(["run"])

    assert exit_code == 1
    mock_select.assert_not_called()


def test_cmd_report_never_posts_for_real_even_if_dry_run_is_false():
    with patch("app.main.config.validate"), \
         patch("app.main.posting_service.is_dry_run", return_value=False), \
         patch("app.main.posting_service._get_max_candidates", return_value=1), \
         patch("app.main.posting_service.select_postable_candidates", return_value=[{"application_eid": "APP-1"}]) as mock_select, \
         patch("app.main.posting_service.post_candidates", return_value=[{"office_resolved": True, "payload": {}}]) as mock_post, \
         patch("app.main.posting_service.record_post_result") as mock_record, \
         patch("app.main.reporting_service.generate_reports", return_value="report.xlsx"):
        exit_code = app_main.main(["report"])

    assert exit_code == 0
    mock_select.assert_called_once()
    # dry_run=True was forced regardless of posting_service.is_dry_run() -> False
    assert mock_post.call_args.kwargs.get("dry_run") is True or mock_post.call_args[0][1] is True
    mock_record.assert_not_called()  # never writes post_log/candidate status


def test_cmd_post_records_results_when_not_dry_run():
    with patch("app.main.config.validate"), \
         patch("app.main.posting_service.is_dry_run", return_value=False), \
         patch("app.main.posting_service._get_max_candidates", return_value=1), \
         patch("app.main.posting_service.select_postable_candidates", return_value=[{"application_eid": "APP-1"}]), \
         patch("app.main.posting_service.post_candidates", return_value=[{"outcome": "success"}]), \
         patch("app.main.posting_service.record_post_result") as mock_record, \
         patch("app.main.reporting_service.generate_reports", return_value="report.xlsx"):
        exit_code = app_main.main(["post"])

    assert exit_code == 0
    mock_record.assert_called_once()


def test_cmd_post_skips_recording_when_dry_run():
    with patch("app.main.config.validate"), \
         patch("app.main.posting_service.is_dry_run", return_value=True), \
         patch("app.main.posting_service._get_max_candidates", return_value=1), \
         patch("app.main.posting_service.select_postable_candidates", return_value=[{"application_eid": "APP-1"}]), \
         patch("app.main.posting_service.post_candidates", return_value=[{"office_resolved": True, "payload": {}}]), \
         patch("app.main.posting_service.record_post_result") as mock_record, \
         patch("app.main.reporting_service.generate_reports", return_value="report.xlsx"):
        exit_code = app_main.main(["post"])

    assert exit_code == 0
    mock_record.assert_not_called()
