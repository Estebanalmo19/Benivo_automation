from unittest.mock import patch

import scripts.check_cutover_status as status_script

CCS = "scripts.check_cutover_status"


def test_tri_state_unset():
    assert status_script.tri_state("BENIVO_TOTALLY_MADE_UP_VAR") == "unset"


def test_tri_state_true_variants(monkeypatch):
    for value in ("true", "TRUE", "1", "yes"):
        monkeypatch.setenv("BENIVO_DRY_RUN", value)
        assert status_script.tri_state("BENIVO_DRY_RUN") == "true"


def test_tri_state_false_variants(monkeypatch):
    for value in ("false", "FALSE", "0", "no"):
        monkeypatch.setenv("BENIVO_DRY_RUN", value)
        assert status_script.tri_state("BENIVO_DRY_RUN") == "false"


def test_presence_set_vs_unset(monkeypatch):
    monkeypatch.delenv("BENIVO_GO_LIVE_AT", raising=False)
    assert status_script.presence("BENIVO_GO_LIVE_AT") == "unset"

    monkeypatch.setenv("BENIVO_GO_LIVE_AT", "2026-09-08T14:00:00+00:00")
    assert status_script.presence("BENIVO_GO_LIVE_AT") == "set"


def test_value_or_unset(monkeypatch):
    monkeypatch.delenv("BENIVO_MAX_CANDIDATES", raising=False)
    assert status_script.value_or_unset("BENIVO_MAX_CANDIDATES") == "unset"

    monkeypatch.setenv("BENIVO_MAX_CANDIDATES", "85")
    assert status_script.value_or_unset("BENIVO_MAX_CANDIDATES") == "85"


def test_classify_endpoint_identifies_uat_hostname():
    assert status_script.classify_endpoint("https://externalapi.uat.benivo.com/token") == "UAT"
    assert status_script.classify_endpoint("https://hubapi.uat.benivo.com/refdata") == "UAT"


def test_classify_endpoint_never_returns_prod_for_a_non_uat_looking_host():
    # No confirmed production hostname pattern exists in this codebase --
    # this must report UNKNOWN, never guess PROD.
    result = status_script.classify_endpoint("https://api.benivo.com/create-user")
    assert result == "UNKNOWN"
    assert result != "PROD"


def test_classify_endpoint_unset_or_malformed():
    assert status_script.classify_endpoint(None) == "UNKNOWN"
    assert status_script.classify_endpoint("") == "UNKNOWN"
    assert status_script.classify_endpoint("not a url") == "UNKNOWN"


def test_main_prints_all_required_lines_with_unset_vars(monkeypatch, capsys):
    for var in ("BENIVO_DRY_RUN", "BENIVO_GO_LIVE_AT", "BENIVO_APPROVED_BATCH_FILE", "BENIVO_MAX_CANDIDATES", "BENIVO_REPORT_DELIVERY_ENABLED"):
        monkeypatch.delenv(var, raising=False)

    with patch(f"{CCS}.config.BENIVO_TOKEN_URL", "https://externalapi.uat.benivo.com"), \
         patch(f"{CCS}.config.BENIVO_REFDATA_URL", "https://hubapi.uat.benivo.com"), \
         patch(f"{CCS}.config.BENIVO_CREATE_USER_URL", "https://hubapi.uat.benivo.com"), \
         patch(f"{CCS}.config.BENIVO_USER_LOOKUP_URL", "https://hubapi.uat.benivo.com"), \
         patch(f"{CCS}.config.BENIVO_CASE_URL", "https://externalapi.uat.benivo.com"):
        exit_code = status_script.main([])

    out = capsys.readouterr().out
    assert exit_code == 0
    assert "BENIVO_DRY_RUN = unset" in out
    assert "BENIVO_GO_LIVE_AT = unset" in out
    assert "BENIVO_APPROVED_BATCH_FILE = unset" in out
    assert "BENIVO_MAX_CANDIDATES = unset" in out
    assert "BENIVO_REPORT_DELIVERY_ENABLED = unset" in out
    assert "TOKEN_ENDPOINT_ENV = UAT" in out
    assert "REFDATA_ENDPOINT_ENV = UAT" in out
    assert "CREATE_USER_ENDPOINT_ENV = UAT" in out
    assert "USER_LOOKUP_ENDPOINT_ENV = UAT" in out
    assert "CASE_ENDPOINT_ENV = UAT" in out


def test_main_never_prints_secrets(monkeypatch, capsys):
    monkeypatch.setenv("BENIVO_GO_LIVE_AT", "2026-09-08T14:00:00+00:00")

    with patch(f"{CCS}.config.BENIVO_TOKEN_URL", "https://externalapi.uat.benivo.com/token?secret=SUPERSECRET123"), \
         patch("app.config.BENIVO_CLIENT_SECRET", "sekrit-value"), \
         patch("app.config.DB_PASSWORD", "db-sekrit"):
        status_script.main([])

    out = capsys.readouterr().out
    assert "SUPERSECRET123" not in out  # only the classification result is printed, never the URL itself
    assert "sekrit-value" not in out
    assert "db-sekrit" not in out
    assert "2026-09-08T14:00:00" not in out  # GO_LIVE_AT is set/unset only, never the value


def test_main_performs_no_http_or_db_calls():
    with patch("requests.post") as mock_post, patch("requests.get") as mock_get, \
         patch("psycopg2.connect") as mock_connect:
        status_script.main([])

    mock_post.assert_not_called()
    mock_get.assert_not_called()
    mock_connect.assert_not_called()


def test_module_never_imports_benivo_client_or_db_client():
    import scripts.check_cutover_status as module

    assert "benivo_client" not in vars(module)
    assert "db_cursor" not in vars(module)
    assert "transaction" not in vars(module)
    assert "reporting_service" not in vars(module)
    assert "report_delivery_service" not in vars(module)
