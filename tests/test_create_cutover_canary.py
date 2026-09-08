import json
from unittest.mock import patch

import scripts.create_cutover_canary as create_canary

CCC = "scripts.create_cutover_canary"


def _row(eid, workplace, first_seen):
    return {"application_eid": eid, "workplace": workplace, "first_seen_in_scope_at": first_seen}


def _full_batch_rows():
    # Already ORDER BY first_seen_in_scope_at, application_eid, exactly as
    # get_workplace_and_first_seen() itself would return.
    return [
        _row("APP-EARLY-RAK", "RAK Live Casino", "2026-08-24T21:14:39+00:00"),
        _row("APP-LATE-RAK", "RAK Live Casino", "2026-08-25T00:00:00+00:00"),
        _row("APP-SERBIA", "Serbia Live Casino", "2026-08-26T00:00:00+00:00"),
        _row("APP-ROMANIA", "Romania Live Casino", "2026-08-27T00:00:00+00:00"),
        _row("APP-MALTA", "Malta", "2026-08-28T00:00:00+00:00"),
    ]


def _write_batch_file(tmp_path, eids, filename="full_batch.json"):
    path = tmp_path / filename
    path.write_text(json.dumps({"batch_name": "full", "application_eids": eids}), encoding="utf-8")
    return str(path)


# ---------------------------------------------------------------------------
# select_canary_eids() -- pure grouping/selection logic
# ---------------------------------------------------------------------------

def test_select_canary_eids_picks_exactly_three():
    result = create_canary.select_canary_eids(_full_batch_rows())
    assert len(result) == 3


def test_select_canary_eids_earliest_first_seen_wins_within_group():
    result = create_canary.select_canary_eids(_full_batch_rows())
    assert "APP-EARLY-RAK" in result
    assert "APP-LATE-RAK" not in result


def test_select_canary_eids_represents_uae_rak_group():
    result = create_canary.select_canary_eids(_full_batch_rows())
    assert "APP-EARLY-RAK" in result


def test_select_canary_eids_represents_serbia_group():
    result = create_canary.select_canary_eids(_full_batch_rows())
    assert "APP-SERBIA" in result


def test_select_canary_eids_represents_third_group():
    result = create_canary.select_canary_eids(_full_batch_rows())
    assert "APP-ROMANIA" in result  # earlier first_seen than APP-MALTA within the third group


def test_select_canary_eids_uae_value_also_matches_uae_rak_group():
    rows = [_row("APP-UAE", "UAE", "2026-08-24T21:14:39+00:00")] + _full_batch_rows()[2:]
    result = create_canary.select_canary_eids(rows)
    assert "APP-UAE" in result


def test_select_canary_eids_fails_closed_when_group_missing():
    rows = [row for row in _full_batch_rows() if row["workplace"] != "Serbia Live Casino"]

    try:
        create_canary.select_canary_eids(rows)
        raised = False
    except ValueError as exc:
        raised = True
        assert "serbia" in str(exc)

    assert raised is True


def test_select_canary_eids_deterministic_across_repeated_calls():
    first = create_canary.select_canary_eids(_full_batch_rows())
    second = create_canary.select_canary_eids(_full_batch_rows())
    assert first == second


# ---------------------------------------------------------------------------
# main() -- orchestration, DB restriction, fail-closed, no PII
# ---------------------------------------------------------------------------

def test_main_never_introduces_an_eid_outside_the_full_batch(tmp_path):
    full_batch_eids = ["APP-EARLY-RAK", "APP-SERBIA", "APP-ROMANIA"]
    batch_path = _write_batch_file(tmp_path, full_batch_eids)
    output_path = tmp_path / "canary.json"

    with patch("app.config.validate"), \
         patch(f"{CCC}.get_workplace_and_first_seen", return_value=_full_batch_rows()) as mock_lookup:
        exit_code = create_canary.main(["--batch", batch_path, "--output", str(output_path)])

    assert exit_code == 0
    mock_lookup.assert_called_once_with(full_batch_eids)  # restricted to exactly the full batch's own eids

    written = json.loads(output_path.read_text(encoding="utf-8"))
    assert all(eid in full_batch_eids for eid in written["application_eids"])


def test_main_writes_exactly_three_eids(tmp_path):
    batch_path = _write_batch_file(tmp_path, [r["application_eid"] for r in _full_batch_rows()])
    output_path = tmp_path / "canary.json"

    with patch("app.config.validate"), \
         patch(f"{CCC}.get_workplace_and_first_seen", return_value=_full_batch_rows()):
        exit_code = create_canary.main(["--batch", batch_path, "--output", str(output_path)])

    assert exit_code == 0
    written = json.loads(output_path.read_text(encoding="utf-8"))
    assert len(written["application_eids"]) == 3


def test_main_fails_closed_when_batch_file_invalid(tmp_path):
    missing_path = str(tmp_path / "nope.json")
    output_path = tmp_path / "canary.json"

    with patch("app.config.validate"), \
         patch(f"{CCC}.get_workplace_and_first_seen") as mock_lookup:
        exit_code = create_canary.main(["--batch", missing_path, "--output", str(output_path)])

    assert exit_code == 1
    mock_lookup.assert_not_called()
    assert not output_path.exists()


def test_main_fails_closed_when_required_group_missing(tmp_path, capsys):
    eids = ["APP-EARLY-RAK", "APP-ROMANIA"]
    batch_path = _write_batch_file(tmp_path, eids)
    output_path = tmp_path / "canary.json"
    rows_without_serbia = [r for r in _full_batch_rows() if r["workplace"] != "Serbia Live Casino"]

    with patch("app.config.validate"), \
         patch(f"{CCC}.get_workplace_and_first_seen", return_value=rows_without_serbia):
        exit_code = create_canary.main(["--batch", batch_path, "--output", str(output_path)])

    assert exit_code == 1
    assert not output_path.exists()
    assert "serbia" in capsys.readouterr().out.lower()


def test_main_refuses_overwrite_without_force(tmp_path):
    batch_path = _write_batch_file(tmp_path, [r["application_eid"] for r in _full_batch_rows()])
    output_path = tmp_path / "canary.json"
    output_path.write_text("{}", encoding="utf-8")

    with patch("app.config.validate"), \
         patch(f"{CCC}.get_workplace_and_first_seen", return_value=_full_batch_rows()):
        exit_code = create_canary.main(["--batch", batch_path, "--output", str(output_path)])

    assert exit_code == 1
    assert output_path.read_text(encoding="utf-8") == "{}"


def test_main_no_pii_in_artifact_or_output(tmp_path, capsys):
    batch_path = _write_batch_file(tmp_path, [r["application_eid"] for r in _full_batch_rows()])
    output_path = tmp_path / "canary.json"

    with patch("app.config.validate"), \
         patch(f"{CCC}.get_workplace_and_first_seen", return_value=_full_batch_rows()):
        create_canary.main(["--batch", batch_path, "--output", str(output_path)])

    out = capsys.readouterr().out
    written = output_path.read_text(encoding="utf-8")
    for text in (out, written):
        assert "@" not in text
        assert "email" not in text.lower()
    # workplace/first_seen_in_scope_at are selection-only, never in the artifact.
    written_json = json.loads(written)
    assert "workplace" not in written_json
    assert "first_seen_in_scope_at" not in written_json


def test_main_never_imports_benivo_client_or_reporting():
    import scripts.create_cutover_canary as module

    assert "benivo_client" not in vars(module)
    assert "reporting_service" not in vars(module)
    assert "report_delivery_service" not in vars(module)
