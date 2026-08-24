from unittest.mock import MagicMock, patch

from app.repositories import candidate_repository


def _fake_db_cursor(rows=None):
    cursor = MagicMock()
    cursor.fetchall.return_value = rows or []
    context_manager = MagicMock()
    context_manager.__enter__.return_value = cursor
    context_manager.__exit__.return_value = False
    return context_manager, cursor


def test_get_ready_candidates_filters_by_scope_history_when_go_live_at_set():
    context_manager, cursor = _fake_db_cursor()

    with patch("app.repositories.candidate_repository.db_cursor", return_value=context_manager), \
         patch("app.repositories.candidate_repository.config.go_live_at", return_value="2026-09-01T00:00:00+00:00"):
        candidate_repository.get_ready_candidates(limit=1)

    sql, params = cursor.execute.call_args[0]
    assert "benivo.scope_history" in sql
    assert "first_seen_in_scope_at >= %(go_live_at)s" in sql
    assert params["go_live_at"] == "2026-09-01T00:00:00+00:00"


def test_get_ready_candidates_applies_no_go_live_filter_when_unset():
    context_manager, cursor = _fake_db_cursor()

    with patch("app.repositories.candidate_repository.db_cursor", return_value=context_manager), \
         patch("app.repositories.candidate_repository.config.go_live_at", return_value=None):
        candidate_repository.get_ready_candidates(limit=1)

    _sql, params = cursor.execute.call_args[0]
    assert params["go_live_at"] is None


def test_get_scope_history_map_returns_application_eid_keyed_dict():
    rows = [
        {"application_eid": "APP-1", "first_seen_in_scope_at": "2026-01-01"},
        {"application_eid": "APP-2", "first_seen_in_scope_at": "2026-02-01"},
    ]
    context_manager, _cursor = _fake_db_cursor(rows)

    with patch("app.repositories.candidate_repository.db_cursor", return_value=context_manager):
        result = candidate_repository.get_scope_history_map()

    assert result == {"APP-1": "2026-01-01", "APP-2": "2026-02-01"}
