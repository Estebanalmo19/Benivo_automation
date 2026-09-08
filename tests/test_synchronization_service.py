from unittest.mock import MagicMock, patch

from app.services import synchronization_service


def test_upsert_sql_refreshes_source_owned_fields_on_conflict():
    sql = synchronization_service._UPSERT_SQL

    for column in ("start_date", "workplace", "job_title", "department", "location", "email", "first_name", "last_name"):
        assert f"{column} = EXCLUDED.{column}" in sql, f"{column} should be refreshed on every sync"


def test_upsert_sql_never_touches_integration_owned_fields_on_conflict():
    sql = synchronization_service._UPSERT_SQL
    do_update_clause = sql.split("DO UPDATE SET")[1]

    for column in ("benivo_status", "benivo_user_id", "benivo_assignment_id", "benivo_profile_url", "benivo_response"):
        assert f"{column} = EXCLUDED.{column}" not in do_update_clause, f"{column} must be preserved, not overwritten by sync"


def test_upsert_sql_refreshes_is_vip_from_mobility_vip_on_conflict():
    # Confirmed 2026-08-24: mobility_vip IS a confirmed Jobvite source for
    # VIP, so is_vip is now source-owned and refreshed every sync -- the
    # opposite of the pre-2026-08-24 rule (see population_service.py for
    # the one centralized place this then feeds into a Benivo Population
    # value, alongside dealer_shuffler).
    sql = synchronization_service._UPSERT_SQL
    assert "mobility_vip" in sql
    assert "is_vip = EXCLUDED.is_vip" in sql.split("DO UPDATE SET")[1]

    insert_columns = sql.split("INSERT INTO benivo.candidates (")[1].split(")")[0]
    assert "is_vip" in insert_columns


def test_upsert_sql_defaults_is_vip_false_when_mobility_vip_absent_or_no():
    # COALESCE(... = 'Yes', FALSE) must never leave is_vip NULL just
    # because the customField is missing for a given candidate -- absent
    # means "No" (-> Tier 1), the same as an explicit "No".
    sql = synchronization_service._UPSERT_SQL
    assert "COALESCE(" in sql
    assert "= 'Yes'" in sql
    assert "AS is_vip" in sql
    coalesce_to_is_vip = sql.split("COALESCE(")[1].split("AS is_vip")[0]
    assert "FALSE" in coalesce_to_is_vip


def test_upsert_sql_inserts_start_date_and_workplace():
    sql = synchronization_service._UPSERT_SQL
    insert_columns = sql.split("INSERT INTO benivo.candidates (")[1].split(")")[0]

    assert "start_date" in insert_columns
    assert "workplace" in insert_columns


def test_upsert_sql_syncs_home_country_from_candidate_home_country_custom_field():
    sql = synchronization_service._UPSERT_SQL

    assert "candidate_home_country" in sql
    assert "home_country = EXCLUDED.home_country" in sql

    insert_columns = sql.split("INSERT INTO benivo.candidates (")[1].split(")")[0]
    assert "home_country" in insert_columns


def test_upsert_sql_syncs_current_country_from_country_name():
    # Fallback source for the home-country business rule (see
    # app/services/home_country_service.py) -- must never be confused with
    # 'location' (a display string) or application.job.location (the job's
    # location, not the candidate's).
    sql = synchronization_service._UPSERT_SQL

    assert "countryName" in sql
    assert "current_country = EXCLUDED.current_country" in sql

    insert_columns = sql.split("INSERT INTO benivo.candidates (")[1].split(")")[0]
    assert "current_country" in insert_columns


def test_mark_out_of_scope_uses_same_eligibility_filter_as_upsert():
    upsert_sql = synchronization_service._UPSERT_SQL
    mark_sql = synchronization_service._MARK_OUT_OF_SCOPE_SQL

    for fragment in ("workflow_state = %(workflow_state)s", "relocation_field_code)s", "relocation_values)s"):
        assert fragment in upsert_sql
        assert fragment in mark_sql


def test_mark_out_of_scope_is_an_update_not_a_delete():
    # Confirmed 2026-09-08: candidates leaving scope must be preserved
    # (workflow_state refreshed, benivo_status transitioned), never deleted.
    sql = synchronization_service._MARK_OUT_OF_SCOPE_SQL
    assert sql.strip().upper().startswith("UPDATE BENIVO.CANDIDATES")
    assert "DELETE" not in sql.upper()


def test_mark_out_of_scope_sets_no_longer_eligible_status():
    sql = synchronization_service._MARK_OUT_OF_SCOPE_SQL
    assert "benivo_status = %(no_longer_eligible_status)s" in sql


def test_mark_out_of_scope_refreshes_workflow_state_from_live_source():
    sql = synchronization_service._MARK_OUT_OF_SCOPE_SQL
    assert "workflow_state = COALESCE(" in sql
    assert "jv_arrise_data_schema.jobvite_applications" in sql
    # Falls back to the row's own current value (never a guess) when the
    # source row has disappeared entirely.
    assert "c.workflow_state" in sql


def test_mark_out_of_scope_never_touches_posted_candidates():
    # Confirmed 2026-09-08: a successfully posted candidate must never
    # become eligible for a duplicate Create User call because their
    # workflow later moved on, and their terminal historical status must
    # never be overwritten.
    sql = synchronization_service._MARK_OUT_OF_SCOPE_SQL
    assert "c.benivo_status IS DISTINCT FROM %(posted_status)s" in sql


def test_scope_history_upsert_uses_same_eligibility_filter_as_upsert():
    # Go-live gating depends on scope_history and benivo.candidates always
    # agreeing on who's "in scope" -- see candidate_repository.
    # get_ready_candidates() and migrations/0008.
    upsert_sql = synchronization_service._UPSERT_SQL
    scope_history_sql = synchronization_service._SCOPE_HISTORY_UPSERT_SQL

    for fragment in ("workflow_state = %(workflow_state)s", "relocation_field_code)s", "relocation_values)s"):
        assert fragment in upsert_sql
        assert fragment in scope_history_sql


def test_scope_history_upsert_never_overwrites_first_seen_on_conflict():
    # ON CONFLICT DO NOTHING (not DO UPDATE) is the entire mechanism that
    # makes first_seen_in_scope_at survive a candidate leaving and
    # re-entering scope -- it must never be touched once set.
    sql = synchronization_service._SCOPE_HISTORY_UPSERT_SQL
    assert "ON CONFLICT (application_eid) DO NOTHING" in sql
    assert "INSERT INTO benivo.scope_history" in sql


def test_sync_candidates_writes_scope_history_before_marking_out_of_scope():
    sql_calls = []

    class FakeCursor:
        def execute(self, sql, params=None):
            sql_calls.append(sql)
            self.rowcount = 0

    class FakeTransaction:
        def __enter__(self):
            return FakeCursor()

        def __exit__(self, *args):
            return False

    import unittest.mock as mock

    with mock.patch("app.services.synchronization_service.transaction", return_value=FakeTransaction()):
        synchronization_service.sync_candidates()

    assert sql_calls == [
        synchronization_service._UPSERT_SQL,
        synchronization_service._SCOPE_HISTORY_UPSERT_SQL,
        synchronization_service._MARK_OUT_OF_SCOPE_SQL,
    ]


def test_sync_candidates_returns_marked_no_longer_eligible_not_removed():
    # Confirmed 2026-09-08: the "removed" metric key no longer exists --
    # nothing is deleted from benivo.candidates anymore.
    mock_cursor = MagicMock()
    mock_cursor.rowcount = 5

    class FakeTransaction:
        def __enter__(self):
            return mock_cursor

        def __exit__(self, *args):
            return False

    with patch("app.services.synchronization_service.transaction", return_value=FakeTransaction()):
        metrics = synchronization_service.sync_candidates()

    assert "marked_no_longer_eligible" in metrics
    assert "removed" not in metrics
