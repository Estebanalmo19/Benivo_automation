import datetime
import json
from unittest.mock import patch

import scripts.create_cutover_batch as create_batch

CCB = "scripts.create_cutover_batch"


def _row(eid, workplace="Serbia Live Casino", first_seen="2026-08-24T21:14:39+00:00"):
    return {"application_eid": eid, "workplace": workplace, "first_seen_in_scope_at": first_seen}


def test_build_batch_artifact_contains_only_application_eids_in_identity_list():
    candidates = [_row("APP-2"), _row("APP-1")]
    t0 = datetime.datetime(2026, 9, 8, 14, 0, 0, tzinfo=datetime.timezone.utc)

    artifact = create_batch.build_batch_artifact(candidates, t0, "batch1", "2026-09-08", "Mobility/GM")

    assert artifact["application_eids"] == ["APP-2", "APP-1"]  # preserves input order (already deterministic from SQL)
    assert artifact["batch_name"] == "batch1"
    assert artifact["approved_at"] == "2026-09-08"
    assert artifact["approved_by"] == "Mobility/GM"
    assert artifact["source_cutover_timestamp"] == t0.isoformat()
    # No workplace, first_seen_in_scope_at, or any other field leaks into the artifact.
    assert "workplace" not in artifact
    assert "first_seen_in_scope_at" not in json.dumps(artifact)


def test_build_batch_artifact_omits_approved_fields_when_not_given():
    t0 = datetime.datetime(2026, 9, 8, tzinfo=datetime.timezone.utc)
    artifact = create_batch.build_batch_artifact([_row("APP-1")], t0, "batch1", None, None)

    assert "approved_at" not in artifact
    assert "approved_by" not in artifact


def test_build_batch_artifact_fails_on_duplicate_application_eid():
    t0 = datetime.datetime(2026, 9, 8, tzinfo=datetime.timezone.utc)

    try:
        create_batch.build_batch_artifact([_row("APP-1"), _row("APP-1")], t0, "batch1", None, None)
        raised = False
    except ValueError:
        raised = True

    assert raised is True


def test_write_artifact_refuses_overwrite_without_force(tmp_path):
    output_path = tmp_path / "batch.json"
    output_path.write_text("{}", encoding="utf-8")

    try:
        create_batch.write_artifact({"application_eids": ["APP-1"]}, output_path, force=False)
        raised = False
    except FileExistsError:
        raised = True

    assert raised is True


def test_write_artifact_overwrites_with_force(tmp_path):
    output_path = tmp_path / "batch.json"
    output_path.write_text("{}", encoding="utf-8")

    create_batch.write_artifact({"application_eids": ["APP-1"]}, output_path, force=True)

    written = json.loads(output_path.read_text(encoding="utf-8"))
    assert written["application_eids"] == ["APP-1"]


def test_write_artifact_creates_parent_directory(tmp_path):
    output_path = tmp_path / "nested" / "dir" / "batch.json"

    create_batch.write_artifact({"application_eids": ["APP-1"]}, output_path, force=False)

    assert output_path.exists()


def test_main_fails_closed_on_empty_historical_population(tmp_path, capsys):
    output_path = tmp_path / "batch.json"

    with patch("app.config.validate"), \
         patch(f"{CCB}.get_historical_batch_candidates", return_value=[]):
        exit_code = create_batch.main(["--t0", "2026-09-08T14:00:00Z", "--output", str(output_path)])

    assert exit_code == 1
    assert not output_path.exists()
    assert "No historical candidates found" in capsys.readouterr().out


def test_main_writes_deterministic_batch_file(tmp_path, capsys):
    output_path = tmp_path / "config" / "approved_batches" / "benivo_first_import_20260908.json"
    rows = [_row("APP-1"), _row("APP-2")]

    with patch("app.config.validate"), \
         patch(f"{CCB}.get_historical_batch_candidates", return_value=rows) as mock_query:
        exit_code = create_batch.main([
            "--t0", "2026-09-08T14:00:00Z",
            "--output", str(output_path),
            "--approved-at", "2026-09-08",
            "--approved-by", "Mobility/GM",
        ])

    assert exit_code == 0
    mock_query.assert_called_once()
    written = json.loads(output_path.read_text(encoding="utf-8"))
    assert written["application_eids"] == ["APP-1", "APP-2"]
    assert written["batch_name"] == "benivo_first_import_20260908"  # derived from output stem
    assert written["approved_at"] == "2026-09-08"
    assert written["approved_by"] == "Mobility/GM"


def test_main_refuses_overwrite_without_force(tmp_path):
    output_path = tmp_path / "batch.json"
    output_path.write_text("{}", encoding="utf-8")

    with patch("app.config.validate"), \
         patch(f"{CCB}.get_historical_batch_candidates", return_value=[_row("APP-1")]):
        exit_code = create_batch.main(["--t0", "2026-09-08T14:00:00Z", "--output", str(output_path)])

    assert exit_code == 1
    assert output_path.read_text(encoding="utf-8") == "{}"  # untouched


def test_main_no_pii_in_output_or_artifact(tmp_path, capsys):
    output_path = tmp_path / "batch.json"

    with patch("app.config.validate"), \
         patch(f"{CCB}.get_historical_batch_candidates", return_value=[_row("APP-1")]):
        create_batch.main(["--t0", "2026-09-08T14:00:00Z", "--output", str(output_path)])

    out = capsys.readouterr().out
    written = output_path.read_text(encoding="utf-8")
    for text in (out, written):
        assert "@" not in text
        assert "email" not in text.lower()


def test_main_never_imports_benivo_client_or_reporting():
    import scripts.create_cutover_batch as module

    assert "benivo_client" not in vars(module)
    assert "reporting_service" not in vars(module)
    assert "report_delivery_service" not in vars(module)
