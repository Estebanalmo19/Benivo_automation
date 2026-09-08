import datetime
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


# ---------------------------------------------------------------------------
# FINAL POSTING SAFETY GATE (confirmed 2026-09-08, root-caused from
# application_eid=pP98MxwU / Babak Guliyev): get_ready_candidates() must
# re-verify the authoritative Jobvite source at selection time, never trust
# benivo.candidates.benivo_status/workflow_state alone.
# ---------------------------------------------------------------------------

def test_get_ready_candidates_requires_live_source_workflow_state_match():
    context_manager, cursor = _fake_db_cursor()

    with patch("app.repositories.candidate_repository.db_cursor", return_value=context_manager), \
         patch("app.repositories.candidate_repository.config.go_live_at", return_value=None):
        candidate_repository.get_ready_candidates(limit=1)

    sql, params = cursor.execute.call_args[0]
    assert "jv_arrise_data_schema.jobvite_applications" in sql
    assert "j.workflow_state = %(mobility_workflow_state)s" in sql
    assert "j.application_eid = c.application_eid" in sql
    assert params["mobility_workflow_state"] == "Mobility in process"


def test_get_ready_candidates_still_requires_cached_ready_to_post_status():
    # The live source check is ADDITIONAL, not a replacement for the
    # existing benivo_status gate.
    context_manager, cursor = _fake_db_cursor()

    with patch("app.repositories.candidate_repository.db_cursor", return_value=context_manager), \
         patch("app.repositories.candidate_repository.config.go_live_at", return_value=None):
        candidate_repository.get_ready_candidates(limit=1)

    sql, _params = cursor.execute.call_args[0]
    assert "c.benivo_status = 'READY_TO_POST'" in sql


def test_get_source_workflow_state_reads_live_source_only():
    rows = [{"workflow_state": "Offer rescinded"}]
    context_manager, cursor = _fake_db_cursor(rows)
    cursor.fetchone.return_value = rows[0]

    with patch("app.repositories.candidate_repository.db_cursor", return_value=context_manager):
        result = candidate_repository.get_source_workflow_state("pP98MxwU")

    sql, params = cursor.execute.call_args[0]
    assert "jv_arrise_data_schema.jobvite_applications" in sql
    assert "benivo.candidates" not in sql
    assert params == {"application_eid": "pP98MxwU"}
    assert result == "Offer rescinded"


def test_get_source_workflow_state_none_when_source_row_missing():
    context_manager, cursor = _fake_db_cursor()
    cursor.fetchone.return_value = None

    with patch("app.repositories.candidate_repository.db_cursor", return_value=context_manager):
        result = candidate_repository.get_source_workflow_state("does-not-exist")

    assert result is None


def test_get_scope_history_map_returns_application_eid_keyed_dict():
    rows = [
        {"application_eid": "APP-1", "first_seen_in_scope_at": "2026-01-01"},
        {"application_eid": "APP-2", "first_seen_in_scope_at": "2026-02-01"},
    ]
    context_manager, _cursor = _fake_db_cursor(rows)

    with patch("app.repositories.candidate_repository.db_cursor", return_value=context_manager):
        result = candidate_repository.get_scope_history_map()

    assert result == {"APP-1": "2026-01-01", "APP-2": "2026-02-01"}


# ---------------------------------------------------------------------------
# DEALER_SHUFFLER_SUBQUERY / MOBILITY_SUPPORT_SUBQUERY -- Jobvite
# raw_payload is the authoritative source for both (confirmed 2026-09-04),
# joined by application_eid (an exact key match) rather than the retired
# HiBob email join -- see population_service.py's module docstring.
# ---------------------------------------------------------------------------

def test_dealer_shuffler_subquery_joins_by_application_eid_at_job_level():
    sql = candidate_repository.DEALER_SHUFFLER_SUBQUERY
    assert "jv_arrise_data_schema.jobvite_applications" in sql
    assert "j.application_eid = c.application_eid" in sql
    assert "'application'->'job'->'customField'" in sql
    assert "fieldCode' = 'dealer__shuffler'" in sql  # double underscore -- confirmed real fieldCode
    assert "hibob_etl" not in sql
    assert "email" not in sql


def test_dealer_shuffler_subquery_is_a_scalar_subquery():
    sql = candidate_repository.DEALER_SHUFFLER_SUBQUERY.strip()
    assert sql.startswith("(")
    assert sql.endswith("AS dealer_shuffler")
    assert "LIMIT 1" in sql


def test_mobility_support_subquery_joins_by_application_eid_at_application_level():
    sql = candidate_repository.MOBILITY_SUPPORT_SUBQUERY
    assert "jv_arrise_data_schema.jobvite_applications" in sql
    assert "j.application_eid = c.application_eid" in sql
    assert "'application'->'customField'" in sql
    assert "fieldCode' = 'mobility_support'" in sql
    assert "hibob_etl" not in sql


def test_mobility_support_subquery_is_a_scalar_subquery():
    sql = candidate_repository.MOBILITY_SUPPORT_SUBQUERY.strip()
    assert sql.startswith("(")
    assert sql.endswith("AS mobility_support")
    assert "LIMIT 1" in sql


def test_ready_candidate_fields_includes_dealer_shuffler_not_mobility_support():
    # get_ready_candidates() feeds posting -- only Population (dealer_shuffler)
    # is needed there; scope (mobility_support) was already decided at
    # classification time, before benivo_status became READY_TO_POST.
    assert "dealer_shuffler" in candidate_repository.READY_CANDIDATE_FIELDS
    assert candidate_repository.DEALER_SHUFFLER_SUBQUERY in candidate_repository.READY_CANDIDATE_FIELDS


def test_full_report_fields_includes_dealer_shuffler_and_mobility_support():
    assert candidate_repository.DEALER_SHUFFLER_SUBQUERY in candidate_repository.FULL_REPORT_FIELDS
    assert candidate_repository.MOBILITY_SUPPORT_SUBQUERY in candidate_repository.FULL_REPORT_FIELDS


def test_reporting_fields_includes_dealer_shuffler_mobility_support_and_is_vip():
    # is_vip/home_country/current_country were previously missing from
    # REPORTING_FIELDS entirely (Pending Office Mapping / Pending Recruiter
    # Review sheets always showed is_vip as None) -- fixed alongside this
    # change since Population/scope display for those sheets needs them too.
    assert candidate_repository.DEALER_SHUFFLER_SUBQUERY in candidate_repository.REPORTING_FIELDS
    assert candidate_repository.MOBILITY_SUPPORT_SUBQUERY in candidate_repository.REPORTING_FIELDS
    assert "c.is_vip" in candidate_repository.REPORTING_FIELDS
    assert "c.home_country" in candidate_repository.REPORTING_FIELDS
    assert "c.current_country" in candidate_repository.REPORTING_FIELDS


# ---------------------------------------------------------------------------
# get_historical_batch_candidates() -- Phase 2 read-only pre-cutover listing
# (confirmed 2026-09-08): mirror image of get_ready_candidates()'s go-live
# clause -- first_seen_in_scope_at < T0, never >= T0.
# ---------------------------------------------------------------------------

def test_get_historical_batch_candidates_uses_strictly_less_than_t0():
    context_manager, cursor = _fake_db_cursor()
    t0 = datetime.datetime(2026, 9, 8, 14, 0, 0, tzinfo=datetime.timezone.utc)

    with patch("app.repositories.candidate_repository.db_cursor", return_value=context_manager):
        candidate_repository.get_historical_batch_candidates(t0)

    sql, params = cursor.execute.call_args[0]
    assert "first_seen_in_scope_at < %(t0)s" in sql
    assert "first_seen_in_scope_at >=" not in sql
    assert params["t0"] == t0


def test_get_historical_batch_candidates_requires_scope_history_join_not_left_join():
    context_manager, cursor = _fake_db_cursor()
    t0 = datetime.datetime(2026, 9, 8, tzinfo=datetime.timezone.utc)

    with patch("app.repositories.candidate_repository.db_cursor", return_value=context_manager):
        candidate_repository.get_historical_batch_candidates(t0)

    sql, _params = cursor.execute.call_args[0]
    assert "JOIN benivo.scope_history sh ON sh.application_eid = c.application_eid" in sql
    assert "LEFT JOIN" not in sql


def test_get_historical_batch_candidates_preserves_every_other_safety_gate():
    context_manager, cursor = _fake_db_cursor()
    t0 = datetime.datetime(2026, 9, 8, tzinfo=datetime.timezone.utc)

    with patch("app.repositories.candidate_repository.db_cursor", return_value=context_manager):
        candidate_repository.get_historical_batch_candidates(t0)

    sql, params = cursor.execute.call_args[0]
    assert "c.benivo_status = 'READY_TO_POST'" in sql
    assert "c.is_relocation_required = 'Yes'" in sql
    assert "BTRIM(c.application_eid) <> ''" in sql
    assert "j.workflow_state = %(mobility_workflow_state)s" in sql
    assert params["mobility_workflow_state"] == "Mobility in process"
    assert "pl.action = %(create_user_action)s" in sql
    assert params["create_user_action"] == "CREATE_USER"
    assert params["terminal_statuses"] == ["SUCCESS", "ALREADY_EXISTS"]


def test_get_historical_batch_candidates_orders_by_first_seen_then_eid():
    context_manager, cursor = _fake_db_cursor()
    t0 = datetime.datetime(2026, 9, 8, tzinfo=datetime.timezone.utc)

    with patch("app.repositories.candidate_repository.db_cursor", return_value=context_manager):
        candidate_repository.get_historical_batch_candidates(t0)

    sql, _params = cursor.execute.call_args[0]
    assert "ORDER BY sh.first_seen_in_scope_at, c.application_eid" in sql


def test_get_historical_batch_candidates_selects_only_non_pii_columns():
    context_manager, cursor = _fake_db_cursor()
    t0 = datetime.datetime(2026, 9, 8, tzinfo=datetime.timezone.utc)

    with patch("app.repositories.candidate_repository.db_cursor", return_value=context_manager):
        candidate_repository.get_historical_batch_candidates(t0)

    sql, _params = cursor.execute.call_args[0]
    for pii_column in ("email", "first_name", "last_name", "phone_number"):
        assert pii_column not in sql
    assert "c.application_eid" in sql
    assert "c.workplace" in sql
    assert "sh.first_seen_in_scope_at" in sql


def test_get_historical_batch_candidates_returns_rows_as_dicts():
    rows = [{"application_eid": "APP-1", "workplace": "Serbia Live Casino", "first_seen_in_scope_at": "2026-08-24T21:14:39+00:00"}]
    context_manager, cursor = _fake_db_cursor(rows)
    t0 = datetime.datetime(2026, 9, 8, tzinfo=datetime.timezone.utc)

    with patch("app.repositories.candidate_repository.db_cursor", return_value=context_manager):
        result = candidate_repository.get_historical_batch_candidates(t0)

    assert result == rows


# ---------------------------------------------------------------------------
# get_workplace_and_first_seen() -- Change 3 canary-generation lookup
# (confirmed 2026-09-08): restricted to an explicit, caller-supplied
# application_eid set, never an independent eligibility query.
# ---------------------------------------------------------------------------

def test_get_workplace_and_first_seen_restricts_to_given_eids():
    context_manager, cursor = _fake_db_cursor()

    with patch("app.repositories.candidate_repository.db_cursor", return_value=context_manager):
        candidate_repository.get_workplace_and_first_seen(["APP-1", "APP-2"])

    sql, params = cursor.execute.call_args[0]
    assert "c.application_eid = ANY(%(application_eids)s)" in sql
    assert params["application_eids"] == ["APP-1", "APP-2"]


def test_get_workplace_and_first_seen_orders_by_first_seen_then_eid():
    context_manager, cursor = _fake_db_cursor()

    with patch("app.repositories.candidate_repository.db_cursor", return_value=context_manager):
        candidate_repository.get_workplace_and_first_seen(["APP-1"])

    sql, _params = cursor.execute.call_args[0]
    assert "ORDER BY sh.first_seen_in_scope_at, c.application_eid" in sql


def test_get_workplace_and_first_seen_selects_only_non_pii_columns():
    context_manager, cursor = _fake_db_cursor()

    with patch("app.repositories.candidate_repository.db_cursor", return_value=context_manager):
        candidate_repository.get_workplace_and_first_seen(["APP-1"])

    sql, _params = cursor.execute.call_args[0]
    for pii_column in ("email", "first_name", "last_name", "phone_number"):
        assert pii_column not in sql


def test_get_workplace_and_first_seen_empty_input_returns_empty_without_querying():
    with patch("app.repositories.candidate_repository.db_cursor") as mock_db_cursor:
        result = candidate_repository.get_workplace_and_first_seen([])

    assert result == []
    mock_db_cursor.assert_not_called()


def test_get_workplace_and_first_seen_returns_rows_as_dicts():
    rows = [{"application_eid": "APP-1", "workplace": "Serbia Live Casino", "first_seen_in_scope_at": "2026-08-24T21:14:39+00:00"}]
    context_manager, cursor = _fake_db_cursor(rows)

    with patch("app.repositories.candidate_repository.db_cursor", return_value=context_manager):
        result = candidate_repository.get_workplace_and_first_seen(["APP-1"])

    assert result == rows
