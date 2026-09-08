import json
from contextlib import contextmanager
from unittest.mock import patch

import pytest

from app.services import approved_batch_service as batch_service

ABS = "app.services.approved_batch_service"


@contextmanager
def patch_repo(candidates, terminal_eids, source_workflow=None, source_workflow_map=None):
    """Patches the three repository functions approved_batch_service imports directly."""

    def fake_get_candidate(eid):
        return candidates.get(eid)

    def fake_get_source_workflow_state(eid):
        if source_workflow_map is not None:
            return source_workflow_map.get(eid)
        return source_workflow

    with patch(f"{ABS}.get_candidate_by_application_eid", side_effect=fake_get_candidate), \
         patch(f"{ABS}.get_source_workflow_state", side_effect=fake_get_source_workflow_state), \
         patch(f"{ABS}.get_terminal_post_log_application_eids", return_value=terminal_eids):
        yield


def _write_batch_file(tmp_path, data, filename="batch.json"):
    path = tmp_path / filename
    path.write_text(json.dumps(data), encoding="utf-8")
    return str(path)


def _valid_batch_data(**overrides):
    base = {
        "batch_name": "benivo_first_import_20260908",
        "approved_at": "2026-09-08",
        "approved_by": "Mobility/GM",
        "application_eids": ["APP-1", "APP-2", "APP-3"],
    }
    base.update(overrides)
    return base


def _candidate(**overrides):
    base = {
        "application_eid": "APP-1",
        "benivo_status": "READY_TO_POST",
        "is_relocation_required": "Yes",
        "workflow_state": "Mobility in process",
    }
    base.update(overrides)
    return base


# ---------------------------------------------------------------------------
# load_approved_batch() -- fail-closed file validation
# ---------------------------------------------------------------------------

def test_load_approved_batch_valid_file_loads_correctly(tmp_path):
    path = _write_batch_file(tmp_path, _valid_batch_data())
    batch = batch_service.load_approved_batch(path)

    assert batch["batch_name"] == "benivo_first_import_20260908"
    assert batch["application_eids"] == ["APP-1", "APP-2", "APP-3"]


def test_load_approved_batch_strips_whitespace_from_eids(tmp_path):
    path = _write_batch_file(tmp_path, _valid_batch_data(application_eids=["  APP-1  ", "APP-2"]))
    batch = batch_service.load_approved_batch(path)

    assert batch["application_eids"] == ["APP-1", "APP-2"]


def test_load_approved_batch_missing_file_fails_closed(tmp_path):
    missing_path = str(tmp_path / "does_not_exist.json")

    with pytest.raises(batch_service.ApprovedBatchLoadError, match="not found"):
        batch_service.load_approved_batch(missing_path)


def test_load_approved_batch_invalid_json_fails_closed(tmp_path):
    path = tmp_path / "invalid.json"
    path.write_text("{not valid json", encoding="utf-8")

    with pytest.raises(batch_service.ApprovedBatchLoadError, match="not valid JSON"):
        batch_service.load_approved_batch(str(path))


def test_load_approved_batch_not_a_json_object_fails_closed(tmp_path):
    path = _write_batch_file(tmp_path, ["APP-1", "APP-2"])

    with pytest.raises(batch_service.ApprovedBatchLoadError, match="JSON object"):
        batch_service.load_approved_batch(path)


def test_load_approved_batch_missing_application_eids_key_fails_closed(tmp_path):
    path = _write_batch_file(tmp_path, {"batch_name": "x"})

    with pytest.raises(batch_service.ApprovedBatchLoadError, match="application_eids"):
        batch_service.load_approved_batch(path)


def test_load_approved_batch_application_eids_not_a_list_fails_closed(tmp_path):
    path = _write_batch_file(tmp_path, _valid_batch_data(application_eids="APP-1"))

    with pytest.raises(batch_service.ApprovedBatchLoadError, match="must be a JSON list"):
        batch_service.load_approved_batch(path)


def test_load_approved_batch_empty_list_fails_closed(tmp_path):
    path = _write_batch_file(tmp_path, _valid_batch_data(application_eids=[]))

    with pytest.raises(batch_service.ApprovedBatchLoadError, match="empty"):
        batch_service.load_approved_batch(path)


def test_load_approved_batch_blank_entry_fails_closed(tmp_path):
    path = _write_batch_file(tmp_path, _valid_batch_data(application_eids=["APP-1", "   "]))

    with pytest.raises(batch_service.ApprovedBatchLoadError, match="non-blank string"):
        batch_service.load_approved_batch(path)


def test_load_approved_batch_non_string_entry_fails_closed(tmp_path):
    path = _write_batch_file(tmp_path, _valid_batch_data(application_eids=["APP-1", 12345]))

    with pytest.raises(batch_service.ApprovedBatchLoadError, match="non-blank string"):
        batch_service.load_approved_batch(path)


def test_load_approved_batch_duplicate_eids_fail_closed(tmp_path):
    # Explicit chosen policy: duplicates invalidate the whole file rather
    # than being silently de-duplicated (see load_approved_batch() docstring).
    path = _write_batch_file(tmp_path, _valid_batch_data(application_eids=["APP-1", "APP-2", "APP-1"]))

    with pytest.raises(batch_service.ApprovedBatchLoadError, match="duplicate"):
        batch_service.load_approved_batch(path)


# ---------------------------------------------------------------------------
# evaluate_approved_batch() / _evaluate_single() -- per-eid revalidation
# ---------------------------------------------------------------------------

def test_evaluate_approved_batch_eligible_candidate():
    with patch_repo(candidates={"APP-1": _candidate()}, terminal_eids=set(), source_workflow="Mobility in process"):
        result = batch_service.evaluate_approved_batch(["APP-1"])

    assert len(result["eligible"]) == 1
    assert result["eligible"][0]["application_eid"] == "APP-1"
    assert result["ineligible"] == []


def test_evaluate_approved_batch_candidate_not_found():
    with patch_repo(candidates={}, terminal_eids=set(), source_workflow=None):
        result = batch_service.evaluate_approved_batch(["APP-MISSING"])

    assert result["eligible"] == []
    assert result["ineligible"] == [{"application_eid": "APP-MISSING", "reason": "candidate_not_found"}]


def test_evaluate_approved_batch_already_posted():
    with patch_repo(
        candidates={"APP-1": _candidate()},
        terminal_eids={"APP-1"},
        source_workflow="Mobility in process",
    ):
        result = batch_service.evaluate_approved_batch(["APP-1"])

    assert result["eligible"] == []
    assert result["ineligible"] == [{"application_eid": "APP-1", "reason": "already_posted"}]


def test_evaluate_approved_batch_not_ready_to_post():
    with patch_repo(
        candidates={"APP-1": _candidate(benivo_status="NO_LONGER_ELIGIBLE")},
        terminal_eids=set(),
        source_workflow="Offer rescinded",
    ):
        result = batch_service.evaluate_approved_batch(["APP-1"])

    assert result["eligible"] == []
    assert result["ineligible"] == [{"application_eid": "APP-1", "reason": "not_ready_to_post"}]


def test_evaluate_approved_batch_relocation_not_required():
    with patch_repo(
        candidates={"APP-1": _candidate(is_relocation_required="No")},
        terminal_eids=set(),
        source_workflow="Mobility in process",
    ):
        result = batch_service.evaluate_approved_batch(["APP-1"])

    assert result["eligible"] == []
    assert result["ineligible"] == [{"application_eid": "APP-1", "reason": "relocation_not_required"}]


def test_evaluate_approved_batch_jobvite_workflow_left_mobility():
    # THE CRITICAL CASE: approved and still cached as READY_TO_POST, but the
    # live authoritative Jobvite source has moved on since approval.
    with patch_repo(
        candidates={"APP-1": _candidate()},  # cached benivo_status=READY_TO_POST
        terminal_eids=set(),
        source_workflow="Offer rescinded",  # live source disagrees
    ):
        result = batch_service.evaluate_approved_batch(["APP-1"])

    assert result["eligible"] == []
    assert result["ineligible"] == [{"application_eid": "APP-1", "reason": "jobvite_workflow_not_mobility"}]


def test_evaluate_approved_batch_invalid_eid_short_circuits():
    result = batch_service.evaluate_approved_batch([""])
    assert result["eligible"] == []
    assert result["ineligible"] == [{"application_eid": "", "reason": "invalid_application_eid"}]


def test_evaluate_approved_batch_mixed_batch_separates_eligible_and_ineligible():
    with patch_repo(
        candidates={"APP-1": _candidate(), "APP-2": _candidate(application_eid="APP-2", benivo_status="NO_LONGER_ELIGIBLE")},
        terminal_eids=set(),
        source_workflow_map={"APP-1": "Mobility in process", "APP-2": "Hired"},
    ):
        result = batch_service.evaluate_approved_batch(["APP-1", "APP-2"])

    assert [c["application_eid"] for c in result["eligible"]] == ["APP-1"]
    assert result["ineligible"] == [{"application_eid": "APP-2", "reason": "not_ready_to_post"}]


def test_candidate_not_in_approved_batch_can_never_be_selected():
    # A fully eligible READY_TO_POST candidate that simply was never listed
    # in the approved batch must never appear -- evaluate_approved_batch()
    # only ever iterates the explicit application_eids it's given, it never
    # scans/queries for "everyone else who happens to be eligible".
    with patch_repo(
        candidates={
            "APP-APPROVED": _candidate(application_eid="APP-APPROVED"),
            "APP-NOT-APPROVED": _candidate(application_eid="APP-NOT-APPROVED"),
        },
        terminal_eids=set(),
        source_workflow="Mobility in process",
    ):
        result = batch_service.evaluate_approved_batch(["APP-APPROVED"])

    assert [c["application_eid"] for c in result["eligible"]] == ["APP-APPROVED"]
    assert not any(c["application_eid"] == "APP-NOT-APPROVED" for c in result["eligible"])
    assert result["ineligible"] == []


# ---------------------------------------------------------------------------
# select_approved_batch_candidates() -- fail-closed orchestration + audit logging
# ---------------------------------------------------------------------------

def test_select_approved_batch_candidates_fails_closed_on_missing_file(tmp_path, caplog):
    missing_path = str(tmp_path / "nope.json")

    with caplog.at_level("ERROR"):
        result = batch_service.select_approved_batch_candidates(missing_path)

    assert result == []
    assert "No candidates selected, no fallback" in caplog.text


def test_select_approved_batch_candidates_logs_audit_counts(tmp_path, caplog):
    path = _write_batch_file(tmp_path, _valid_batch_data(application_eids=["APP-1", "APP-2"]))

    with patch_repo(
        candidates={"APP-1": _candidate(), "APP-2": _candidate(application_eid="APP-2", benivo_status="POSTED")},
        terminal_eids=set(),
        source_workflow_map={"APP-1": "Mobility in process", "APP-2": "Mobility in process"},
    ), caplog.at_level("INFO"):
        result = batch_service.select_approved_batch_candidates(path)

    assert [c["application_eid"] for c in result] == ["APP-1"]
    assert "APPROVED_EIDS_COUNT=2" in caplog.text
    assert "ELIGIBLE_APPROVED_COUNT=1" in caplog.text
    assert "INELIGIBLE_APPROVED_COUNT=1" in caplog.text
    assert "application_eid=APP-2 reason=not_ready_to_post" in caplog.text
    # No PII (email/phone/name) anywhere in the audit log.
    assert "@" not in caplog.text


def test_select_approved_batch_candidates_never_truncates_a_large_eligible_batch(tmp_path):
    eids = [f"APP-{i}" for i in range(85)]
    path = _write_batch_file(tmp_path, _valid_batch_data(application_eids=eids))
    candidates = {eid: _candidate(application_eid=eid) for eid in eids}
    source_workflow_map = {eid: "Mobility in process" for eid in eids}

    with patch_repo(candidates=candidates, terminal_eids=set(), source_workflow_map=source_workflow_map):
        result = batch_service.select_approved_batch_candidates(path)

    assert len(result) == 85
