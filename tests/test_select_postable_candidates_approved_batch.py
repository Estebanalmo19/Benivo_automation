from unittest.mock import patch

from app.services import posting_service as posting

PS = "app.services.posting_service"


# ---------------------------------------------------------------------------
# select_postable_candidates() routing: BENIVO_APPROVED_BATCH_FILE
# ---------------------------------------------------------------------------

def test_batch_file_unset_leaves_existing_normal_selection_unchanged(monkeypatch):
    monkeypatch.delenv("BENIVO_APPROVED_BATCH_FILE", raising=False)
    monkeypatch.delenv("BENIVO_UAT_APPLICATION_EID", raising=False)
    monkeypatch.setenv("BENIVO_MAX_CANDIDATES", "1")

    with patch(f"{PS}.select_approved_batch_candidates") as mock_batch, \
         patch(f"{PS}.get_ready_candidates", return_value=["normal-path-candidate"]) as mock_ready:
        result = posting.select_postable_candidates()

    mock_batch.assert_not_called()
    mock_ready.assert_called_once_with(limit=1)
    assert result == ["normal-path-candidate"]


def test_batch_file_unset_leaves_existing_uat_override_unchanged(monkeypatch):
    monkeypatch.delenv("BENIVO_APPROVED_BATCH_FILE", raising=False)
    monkeypatch.setenv("BENIVO_UAT_APPLICATION_EID", "APP-UAT-1")

    with patch(f"{PS}.select_approved_batch_candidates") as mock_batch, \
         patch(f"{PS}._select_explicit_uat_candidate", return_value=["uat-candidate"]) as mock_uat:
        result = posting.select_postable_candidates()

    mock_batch.assert_not_called()
    mock_uat.assert_called_once_with("APP-UAT-1")
    assert result == ["uat-candidate"]


def test_batch_file_set_routes_to_approved_batch_selection(monkeypatch):
    monkeypatch.setenv("BENIVO_APPROVED_BATCH_FILE", "/some/path/batch.json")
    monkeypatch.setenv("BENIVO_UAT_APPLICATION_EID", "APP-UAT-1")  # must NOT be used when batch file is set

    with patch(f"{PS}.select_approved_batch_candidates", return_value=["batch-candidate"]) as mock_batch, \
         patch(f"{PS}._select_explicit_uat_candidate") as mock_uat, \
         patch(f"{PS}.get_ready_candidates") as mock_ready:
        result = posting.select_postable_candidates()

    mock_batch.assert_called_once_with("/some/path/batch.json")
    mock_uat.assert_not_called()
    mock_ready.assert_not_called()
    assert result == ["batch-candidate"]


def test_batch_mode_ignores_benivo_max_candidates(monkeypatch):
    # BENIVO_MAX_CANDIDATES defaults to 1 -- batch mode must not silently
    # truncate to it. select_approved_batch_candidates() takes no limit
    # argument at all, so there is nothing to truncate with.
    monkeypatch.setenv("BENIVO_APPROVED_BATCH_FILE", "/some/path/batch.json")
    monkeypatch.delenv("BENIVO_MAX_CANDIDATES", raising=False)  # defaults to 1

    eligible = [{"application_eid": f"APP-{i}"} for i in range(85)]

    with patch(f"{PS}.select_approved_batch_candidates", return_value=eligible) as mock_batch:
        result = posting.select_postable_candidates()

    mock_batch.assert_called_once_with("/some/path/batch.json")
    assert len(result) == 85


def test_normal_mode_still_respects_benivo_max_candidates_of_one(monkeypatch):
    # Confirms normal (non-batch) posting behavior is completely unchanged.
    monkeypatch.delenv("BENIVO_APPROVED_BATCH_FILE", raising=False)
    monkeypatch.delenv("BENIVO_UAT_APPLICATION_EID", raising=False)
    monkeypatch.setenv("BENIVO_MAX_CANDIDATES", "1")

    captured = {}

    def fake_get_ready_candidates(limit=None):
        captured["limit"] = limit
        return ["one-candidate"]

    with patch(f"{PS}.get_ready_candidates", side_effect=fake_get_ready_candidates):
        result = posting.select_postable_candidates()

    assert captured["limit"] == 1
    assert result == ["one-candidate"]


# ---------------------------------------------------------------------------
# No business-write / Benivo-write path is reachable through batch selection
# ---------------------------------------------------------------------------

def test_batch_selection_never_calls_create_user_case_patch_or_find_user(monkeypatch):
    monkeypatch.setenv("BENIVO_APPROVED_BATCH_FILE", "/some/path/batch.json")

    with patch(f"{PS}.select_approved_batch_candidates", return_value=[{"application_eid": "APP-1"}]), \
         patch("app.clients.benivo_client.create_user") as mock_create, \
         patch("app.clients.benivo_client.update_case") as mock_case, \
         patch("app.clients.benivo_client.find_user_by_email") as mock_lookup, \
         patch("app.repositories.post_log_repository.insert_post_log_row") as mock_post_log, \
         patch("app.repositories.candidate_repository.update_candidate_after_posting") as mock_update:
        posting.select_postable_candidates()

    mock_create.assert_not_called()
    mock_case.assert_not_called()
    mock_lookup.assert_not_called()
    mock_post_log.assert_not_called()
    mock_update.assert_not_called()


def test_batch_selection_does_not_touch_go_live_at_or_scope_history(monkeypatch):
    monkeypatch.setenv("BENIVO_APPROVED_BATCH_FILE", "/some/path/batch.json")

    with patch(f"{PS}.select_approved_batch_candidates", return_value=[]) as mock_batch, \
         patch(f"{PS}.config.go_live_at") as mock_go_live_at, \
         patch(f"{PS}.get_ready_candidates") as mock_ready:
        posting.select_postable_candidates()

    mock_batch.assert_called_once()
    mock_go_live_at.assert_not_called()
    mock_ready.assert_not_called()  # get_ready_candidates() is the only reader of scope_history/go_live_at
