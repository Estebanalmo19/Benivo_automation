from app.services import synchronization_service


def test_upsert_sql_refreshes_source_owned_fields_on_conflict():
    sql = synchronization_service._UPSERT_SQL

    for column in ("start_date", "workplace", "job_title", "department", "location", "email", "first_name", "last_name"):
        assert f"{column} = EXCLUDED.{column}" in sql, f"{column} should be refreshed on every sync"


def test_upsert_sql_never_touches_integration_owned_fields_on_conflict():
    sql = synchronization_service._UPSERT_SQL
    do_update_clause = sql.split("DO UPDATE SET")[1]

    for column in ("benivo_status", "benivo_user_id", "benivo_assignment_id", "benivo_profile_url", "benivo_response", "is_vip"):
        assert f"{column} = EXCLUDED.{column}" not in do_update_clause, f"{column} must be preserved, not overwritten by sync"


def test_upsert_sql_preserves_existing_is_vip_true_on_conflict():
    # is_vip absent from DO UPDATE SET means an existing manually-set TRUE
    # value on a conflicting row is left completely untouched by the sync
    # UPDATE -- the column simply isn't in the SET list, so Postgres can't
    # touch it regardless of what value the row currently holds.
    do_update_clause = synchronization_service._UPSERT_SQL.split("DO UPDATE SET")[1]
    assert "is_vip" not in do_update_clause


def test_upsert_sql_omits_is_vip_from_insert_so_new_rows_get_the_column_default():
    # is_vip has no confirmed Jobvite source, so new rows must fall back to
    # the column's own DEFAULT FALSE (migrations/0003) rather than sync
    # setting it explicitly.
    insert_columns = synchronization_service._UPSERT_SQL.split("INSERT INTO benivo.candidates (")[1].split(")")[0]
    assert "is_vip" not in insert_columns


def test_upsert_sql_inserts_start_date_and_workplace():
    sql = synchronization_service._UPSERT_SQL
    insert_columns = sql.split("INSERT INTO benivo.candidates (")[1].split(")")[0]

    assert "start_date" in insert_columns
    assert "workplace" in insert_columns


def test_delete_out_of_scope_uses_same_eligibility_filter_as_upsert():
    upsert_sql = synchronization_service._UPSERT_SQL
    delete_sql = synchronization_service._DELETE_OUT_OF_SCOPE_SQL

    for fragment in ("workflow_state = %(workflow_state)s", "relocation_field_code)s", "relocation_values)s"):
        assert fragment in upsert_sql
        assert fragment in delete_sql
