import datetime
from unittest.mock import patch

import scripts.derive_historical_batch as derive_script

DHB = "scripts.derive_historical_batch"


def _row(eid="APP-1", workplace="Serbia Live Casino", first_seen="2026-08-24T21:14:39.323702+00:00"):
    return {"application_eid": eid, "workplace": workplace, "first_seen_in_scope_at": first_seen}


def test_parse_t0_accepts_iso8601_with_z_suffix():
    t0 = derive_script.parse_t0("2026-09-08T14:00:00Z")
    assert t0 == datetime.datetime(2026, 9, 8, 14, 0, 0, tzinfo=datetime.timezone.utc)


def test_main_requires_exactly_one_argument(capsys):
    exit_code = derive_script.main([])
    assert exit_code == 1
    assert "Usage:" in capsys.readouterr().out


def test_main_rejects_invalid_t0(capsys):
    exit_code = derive_script.main(["not-a-timestamp"])
    assert exit_code == 1
    assert "Invalid T0" in capsys.readouterr().out


def test_main_fails_closed_on_missing_configuration(capsys):
    with patch("app.config.validate", side_effect=RuntimeError("Missing required configuration: DB_HOST")), \
         patch(f"{DHB}.get_historical_batch_candidates") as mock_query:
        exit_code = derive_script.main(["2026-09-08T14:00:00Z"])

    assert exit_code == 1
    mock_query.assert_not_called()
    assert "Configuration error" in capsys.readouterr().out


def test_main_prints_t0_and_count_and_rows_no_pii(capsys):
    rows = [_row("APP-1", "Serbia Live Casino"), _row("APP-2", "RAK Live Casino")]

    with patch("app.config.validate"), \
         patch(f"{DHB}.get_historical_batch_candidates", return_value=rows) as mock_query:
        exit_code = derive_script.main(["2026-09-08T14:00:00Z"])

    assert exit_code == 0
    mock_query.assert_called_once()
    (called_t0,), _kwargs = mock_query.call_args
    assert called_t0 == datetime.datetime(2026, 9, 8, 14, 0, 0, tzinfo=datetime.timezone.utc)

    out = capsys.readouterr().out
    assert "T0 = 2026-09-08T14:00:00+00:00" in out
    assert "HISTORICAL_ELIGIBLE_COUNT = 2" in out
    assert "APP-1" in out
    assert "Serbia Live Casino" in out
    assert "APP-2" in out
    assert "RAK Live Casino" in out
    # No PII of any kind.
    assert "@" not in out
    assert "email" not in out.lower()
    assert "phone" not in out.lower()


def test_main_handles_empty_historical_population(capsys):
    with patch("app.config.validate"), \
         patch(f"{DHB}.get_historical_batch_candidates", return_value=[]):
        exit_code = derive_script.main(["2026-09-08T14:00:00Z"])

    assert exit_code == 0
    out = capsys.readouterr().out
    assert "HISTORICAL_ELIGIBLE_COUNT = 0" in out


def test_main_never_imports_benivo_client_or_reporting():
    # This script must have zero reachable path to Benivo HTTP or report
    # generation/delivery -- confirmed by checking its own module globals,
    # not just by not calling them in this test.
    import scripts.derive_historical_batch as module

    assert "benivo_client" not in vars(module)
    assert "reporting_service" not in vars(module)
    assert "report_delivery_service" not in vars(module)
    assert "posting_service" not in vars(module)
