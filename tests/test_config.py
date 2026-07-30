import pytest

from app import config


def test_missing_required_settings_lists_only_unset_ones(monkeypatch):
    monkeypatch.setattr(config, "REQUIRED_SETTINGS", {"A": "value", "B": None, "C": ""})
    assert config.missing_required_settings() == ["B", "C"]


def test_validate_raises_naming_exact_missing_settings(monkeypatch):
    monkeypatch.setattr(config, "REQUIRED_SETTINGS", {"DB_HOST": None, "DB_PORT": "5432"})
    monkeypatch.setattr(config, "BENIVO_GRANT_TYPE", "client_credentials")

    with pytest.raises(RuntimeError, match="DB_HOST"):
        config.validate()


def test_validate_rejects_wrong_grant_type(monkeypatch):
    monkeypatch.setattr(config, "REQUIRED_SETTINGS", {"DB_HOST": "x"})
    monkeypatch.setattr(config, "BENIVO_GRANT_TYPE", "password")

    with pytest.raises(RuntimeError, match="client_credentials"):
        config.validate()


def test_validate_passes_when_everything_present(monkeypatch):
    monkeypatch.setattr(config, "REQUIRED_SETTINGS", {"DB_HOST": "x"})
    monkeypatch.setattr(config, "BENIVO_GRANT_TYPE", "client_credentials")

    config.validate()  # must not raise


def test_is_dry_run_defaults_true(monkeypatch):
    monkeypatch.delenv("BENIVO_DRY_RUN", raising=False)
    assert config.is_dry_run() is True


def test_is_dry_run_reads_fresh_each_call(monkeypatch):
    monkeypatch.setenv("BENIVO_DRY_RUN", "false")
    assert config.is_dry_run() is False
    monkeypatch.setenv("BENIVO_DRY_RUN", "true")
    assert config.is_dry_run() is True


def test_uat_application_eid_none_when_unset(monkeypatch):
    monkeypatch.delenv("BENIVO_UAT_APPLICATION_EID", raising=False)
    assert config.uat_application_eid() is None


def test_uat_application_eid_strips_whitespace(monkeypatch):
    monkeypatch.setenv("BENIVO_UAT_APPLICATION_EID", "  pCu0IxwQ  ")
    assert config.uat_application_eid() == "pCu0IxwQ"


def test_max_candidates_falls_back_on_invalid(monkeypatch):
    monkeypatch.setenv("BENIVO_MAX_CANDIDATES", "-3")
    assert config.max_candidates() == 1
