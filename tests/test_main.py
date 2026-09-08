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
         patch("app.main.reporting_service.generate_reports", return_value=("report.xlsx", {})), \
         patch("app.main.report_delivery_service.deliver_report") as mock_deliver:
        exit_code = app_main.main(["report"])

    assert exit_code == 0
    mock_select.assert_called_once()
    # dry_run=True was forced regardless of posting_service.is_dry_run() -> False
    assert mock_post.call_args.kwargs.get("dry_run") is True or mock_post.call_args[0][1] is True
    mock_record.assert_not_called()  # never writes post_log/candidate status
    mock_deliver.assert_called_once()  # report delivery still attempted, independent of posting


def test_cmd_post_records_results_when_not_dry_run():
    with patch("app.main.config.validate"), \
         patch("app.main.posting_service.is_dry_run", return_value=False), \
         patch("app.main.posting_service._get_max_candidates", return_value=1), \
         patch("app.main.posting_service.select_postable_candidates", return_value=[{"application_eid": "APP-1"}]), \
         patch("app.main.posting_service.post_candidates", return_value=[{"outcome": "success"}]), \
         patch("app.main.posting_service.record_post_result") as mock_record, \
         patch("app.main.reporting_service.generate_reports", return_value=("report.xlsx", {})), \
         patch("app.main.report_delivery_service.deliver_report") as mock_deliver:
        exit_code = app_main.main(["post"])

    assert exit_code == 0
    mock_record.assert_called_once()
    mock_deliver.assert_called_once()


def test_cmd_post_skips_recording_when_dry_run():
    with patch("app.main.config.validate"), \
         patch("app.main.posting_service.is_dry_run", return_value=True), \
         patch("app.main.posting_service._get_max_candidates", return_value=1), \
         patch("app.main.posting_service.select_postable_candidates", return_value=[{"application_eid": "APP-1"}]), \
         patch("app.main.posting_service.post_candidates", return_value=[{"office_resolved": True, "payload": {}}]), \
         patch("app.main.posting_service.record_post_result") as mock_record, \
         patch("app.main.reporting_service.generate_reports", return_value=("report.xlsx", {})), \
         patch("app.main.report_delivery_service.deliver_report") as mock_deliver:
        exit_code = app_main.main(["post"])

    assert exit_code == 0
    mock_record.assert_not_called()
    mock_deliver.assert_called_once()


def test_generate_and_deliver_report_calls_delivery_exactly_once():
    # The single orchestration boundary: one generate_reports() call must
    # produce exactly one deliver_report() call, regardless of command.
    with patch("app.main.reporting_service.generate_reports", return_value=("report.xlsx", {"ready_to_post": 1})) as mock_generate, \
         patch("app.main.report_delivery_service.deliver_report", return_value={"attempted": True, "success": True}) as mock_deliver:
        report_path = app_main._generate_and_deliver_report([{"application_eid": "APP-1"}], dry_run=True, posting_limit=1, run_id="run-1")

    assert report_path == "report.xlsx"
    mock_generate.assert_called_once()
    mock_deliver.assert_called_once_with("report.xlsx", "run-1", {"ready_to_post": 1})


# ---------------------------------------------------------------------------
# post --no-report-delivery (confirmed 2026-09-08, UAT canary safeguard):
# posting behavior is unaffected; only whether the report generated
# afterward is also delivered to Power Automate.
# ---------------------------------------------------------------------------

def test_cmd_post_without_flag_still_delivers_report_by_default():
    with patch("app.main.config.validate"), \
         patch("app.main.posting_service.is_dry_run", return_value=True), \
         patch("app.main.posting_service._get_max_candidates", return_value=1), \
         patch("app.main.posting_service.select_postable_candidates", return_value=[{"application_eid": "APP-1"}]), \
         patch("app.main.posting_service.post_candidates", return_value=[{"office_resolved": True, "payload": {}}]), \
         patch("app.main.posting_service.record_post_result"), \
         patch("app.main.reporting_service.generate_reports", return_value=("report.xlsx", {})), \
         patch("app.main.report_delivery_service.deliver_report") as mock_deliver:
        exit_code = app_main.main(["post"])

    assert exit_code == 0
    mock_deliver.assert_called_once()  # unchanged default behavior


def test_cmd_post_with_flag_still_posts_but_skips_delivery(caplog):
    with patch("app.main.config.validate"), \
         patch("app.main.posting_service.is_dry_run", return_value=False), \
         patch("app.main.posting_service._get_max_candidates", return_value=1), \
         patch("app.main.posting_service.select_postable_candidates", return_value=[{"application_eid": "APP-1"}]), \
         patch("app.main.posting_service.post_candidates", return_value=[{"outcome": "success"}]) as mock_post, \
         patch("app.main.posting_service.record_post_result") as mock_record, \
         patch("app.main.reporting_service.generate_reports", return_value=("report.xlsx", {})) as mock_generate, \
         patch("app.main.report_delivery_service.deliver_report") as mock_deliver, \
         caplog.at_level("INFO"):
        exit_code = app_main.main(["post", "--no-report-delivery"])

    assert exit_code == 0
    mock_post.assert_called_once()  # posting still happened
    mock_record.assert_called_once()  # post_log/candidate status write still happened (dry_run=False)
    mock_generate.assert_called_once()  # local Excel report still generated (Option A)
    mock_deliver.assert_not_called()  # Power Automate delivery skipped
    assert "REPORT_DELIVERY = DISABLED_FOR_THIS_RUN" in caplog.text


def test_cmd_post_no_report_delivery_flag_does_not_change_business_posting_logic():
    # The flag must reach only report delivery -- record_post_result still
    # gets the exact same candidate/result objects select_postable_candidates()
    # and post_candidates() produced, untouched by the flag.
    candidate = {"application_eid": "APP-1"}
    result = {"outcome": "success"}

    with patch("app.main.config.validate"), \
         patch("app.main.posting_service.is_dry_run", return_value=False), \
         patch("app.main.posting_service._get_max_candidates", return_value=1), \
         patch("app.main.posting_service.select_postable_candidates", return_value=[candidate]), \
         patch("app.main.posting_service.post_candidates", return_value=[result]), \
         patch("app.main.posting_service.record_post_result") as mock_record, \
         patch("app.main.reporting_service.generate_reports", return_value=("report.xlsx", {})), \
         patch("app.main.report_delivery_service.deliver_report"):
        app_main.main(["post", "--no-report-delivery"])

    mock_record.assert_called_once()
    called_candidate, called_result = mock_record.call_args[0]
    assert called_candidate is candidate
    assert called_result is result
    assert "run_id" in mock_record.call_args.kwargs


def test_cmd_post_no_report_delivery_handled_failure_still_recorded_as_post_failed():
    with patch("app.main.config.validate"), \
         patch("app.main.posting_service.is_dry_run", return_value=False), \
         patch("app.main.posting_service._get_max_candidates", return_value=1), \
         patch("app.main.posting_service.select_postable_candidates", return_value=[{"application_eid": "APP-1"}]), \
         patch("app.main.posting_service.post_candidates", return_value=[{"outcome": "failed"}]), \
         patch("app.main.posting_service.record_post_result") as mock_record, \
         patch("app.main.reporting_service.generate_reports", return_value=("report.xlsx", {})), \
         patch("app.main.report_delivery_service.deliver_report") as mock_deliver:
        exit_code = app_main.main(["post", "--no-report-delivery"])

    assert exit_code == 0  # cmd_post() itself doesn't fail the process on a handled posting failure
    mock_record.assert_called_once()
    mock_deliver.assert_not_called()


def test_generate_and_deliver_report_deliver_false_skips_delivery_but_still_generates(caplog):
    with patch("app.main.reporting_service.generate_reports", return_value=("report.xlsx", {"ready_to_post": 1})) as mock_generate, \
         patch("app.main.report_delivery_service.deliver_report") as mock_deliver, \
         caplog.at_level("INFO"):
        report_path = app_main._generate_and_deliver_report(
            [{"application_eid": "APP-1"}], dry_run=True, posting_limit=1, run_id="run-1", deliver=False
        )

    assert report_path == "report.xlsx"
    mock_generate.assert_called_once()
    mock_deliver.assert_not_called()
    assert "REPORT_DELIVERY = DISABLED_FOR_THIS_RUN" in caplog.text


def test_generate_and_deliver_report_deliver_default_true_unchanged():
    with patch("app.main.reporting_service.generate_reports", return_value=("report.xlsx", {})), \
         patch("app.main.report_delivery_service.deliver_report") as mock_deliver:
        app_main._generate_and_deliver_report([{"application_eid": "APP-1"}], dry_run=True, posting_limit=1, run_id="run-1")

    mock_deliver.assert_called_once()
