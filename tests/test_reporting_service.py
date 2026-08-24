import datetime

from openpyxl import Workbook, load_workbook

from app.services import reporting_service as reporting

SOME_DATE = datetime.date(2026, 1, 1)
EXECUTION_TIMESTAMP = datetime.datetime(2026, 8, 6, tzinfo=datetime.timezone.utc)


def _candidate(
    application_eid,
    is_relocation_required="Yes",
    start_date=SOME_DATE,
    workplace="Serbia Live Casino",
    benivo_status="READY_TO_POST",
    workflow_state="Mobility in process",
    first_name="Jane",
    last_name="Doe",
    is_vip=False,
    job_title="Presenter",
    home_country=None,
    current_country=None,
    home_city=None,
    phone_number=None,
    benivo_assignment_id=None,
):
    return {
        "application_eid": application_eid,
        "candidate_eid": f"C-{application_eid}",
        "email": f"{application_eid}@example.com",
        "phone_number": phone_number,
        "first_name": first_name,
        "last_name": last_name,
        "workflow_state": workflow_state,
        "is_relocation_required": is_relocation_required,
        "start_date": start_date,
        "workplace": workplace,
        "job_title": job_title,
        "requisition_id": "REQ-1",
        "department": "Live Casino",
        "location": "Somewhere",
        "home_country": home_country,
        "current_country": current_country,
        "home_city": home_city,
        "benivo_status": benivo_status,
        "is_vip": is_vip,
        "benivo_assignment_id": benivo_assignment_id,
        "created_at": datetime.datetime(2026, 1, 1, tzinfo=datetime.timezone.utc),
        "updated_at": datetime.datetime(2026, 1, 1, tzinfo=datetime.timezone.utc),
    }


_OFFICE = {"officeId": "office-1", "officeName": "Serbia (Live Casino)", "hostCountry": "Serbia"}


def _complete_candidate(application_eid="APP-1", **overrides):
    defaults = dict(
        job_title="Game Presenter",
        home_country="Serbia",
        home_city="Belgrade",
        phone_number="+38160123456",
        is_vip=False,
    )
    defaults.update(overrides)
    return _candidate(application_eid, **defaults)


# ---------------------------------------------------------------------------
# Pure helper functions
# ---------------------------------------------------------------------------

def test_relocation_bucket_yes():
    assert reporting._relocation_bucket("Yes") == "yes"
    assert reporting._relocation_bucket("yes") == "yes"


def test_relocation_bucket_no():
    assert reporting._relocation_bucket("No") == "no"


def test_relocation_bucket_blank_or_unrecognized():
    assert reporting._relocation_bucket(None) == "blank_or_unrecognized"
    assert reporting._relocation_bucket("") == "blank_or_unrecognized"
    assert reporting._relocation_bucket("Maybe") == "blank_or_unrecognized"


def test_candidate_name_joins_first_and_last():
    assert reporting._candidate_name({"first_name": "Jane", "last_name": "Doe"}) == "Jane Doe"


def test_candidate_name_handles_missing_parts():
    assert reporting._candidate_name({"first_name": None, "last_name": "Doe"}) == "Doe"
    assert reporting._candidate_name({"first_name": None, "last_name": None}) is None


def test_count_with_pct_formats_count_and_percentage():
    assert reporting._count_with_pct(826, 840) == "826 (98.3%)"


def test_count_with_pct_handles_zero_denominator():
    assert reporting._count_with_pct(0, 0) == "0"


def test_pct_formats_percentage():
    assert reporting._pct(730, 826) == "88.4%"


def test_pct_returns_na_for_zero_denominator():
    assert reporting._pct(0, 0) == "N/A"


# ---------------------------------------------------------------------------
# _go_live_category() -- pure go-live classification, independent of benivo_status storage
# ---------------------------------------------------------------------------

GO_LIVE_AT = datetime.datetime(2026, 9, 1, tzinfo=datetime.timezone.utc)
BEFORE_GO_LIVE = datetime.datetime(2026, 8, 1, tzinfo=datetime.timezone.utc)
AFTER_GO_LIVE = datetime.datetime(2026, 9, 15, tzinfo=datetime.timezone.utc)


def test_go_live_category_already_posted_regardless_of_timing():
    assert reporting._go_live_category("POSTED", AFTER_GO_LIVE, GO_LIVE_AT) == reporting.GO_LIVE_ALREADY_POSTED
    assert reporting._go_live_category("POSTED", None, GO_LIVE_AT) == reporting.GO_LIVE_ALREADY_POSTED


def test_go_live_category_backlog_when_go_live_not_configured():
    assert reporting._go_live_category("READY_TO_POST", AFTER_GO_LIVE, None) == reporting.GO_LIVE_PRE_GO_LIVE_BACKLOG


def test_go_live_category_backlog_when_first_seen_before_cutover():
    assert reporting._go_live_category("READY_TO_POST", BEFORE_GO_LIVE, GO_LIVE_AT) == reporting.GO_LIVE_PRE_GO_LIVE_BACKLOG


def test_go_live_category_backlog_when_no_scope_history_row_at_all():
    # Conservative default: never treat "unknown" as "new" -- matches
    # candidate_repository.get_ready_candidates()'s own EXISTS-based filter.
    assert reporting._go_live_category("READY_TO_POST", None, GO_LIVE_AT) == reporting.GO_LIVE_PRE_GO_LIVE_BACKLOG


def test_go_live_category_automatically_eligible_when_new_and_ready():
    assert (
        reporting._go_live_category("READY_TO_POST", AFTER_GO_LIVE, GO_LIVE_AT)
        == reporting.GO_LIVE_AUTOMATICALLY_ELIGIBLE
    )


def test_go_live_category_newly_eligible_when_new_but_not_ready():
    assert (
        reporting._go_live_category("PENDING_OFFICE_MAPPING", AFTER_GO_LIVE, GO_LIVE_AT)
        == reporting.GO_LIVE_NEWLY_ELIGIBLE
    )


def test_build_row_has_all_required_columns():
    row = reporting._build_row(_candidate("APP-1"), "some reason", EXECUTION_TIMESTAMP)
    assert set(row.keys()) == set(reporting.REQUIRED_COLUMNS)


def test_build_row_ready_reason_default():
    row = reporting._build_row(_candidate("APP-1"), reporting.READY_REASON, EXECUTION_TIMESTAMP)
    assert row["reason"] == reporting.READY_REASON


def test_build_row_policy_name_from_is_vip():
    row_basic = reporting._build_row(_candidate("APP-1", is_vip=False), "reason", EXECUTION_TIMESTAMP)
    row_vip = reporting._build_row(_candidate("APP-2", is_vip=True), "reason", EXECUTION_TIMESTAMP)

    assert row_basic["is_vip"] is False
    assert row_basic["policy_name"] == "Basic"
    assert row_vip["is_vip"] is True
    assert row_vip["policy_name"] == "VIP"


def test_build_row_reports_jobvite_start_date_source_when_present():
    row = reporting._build_row(_candidate("APP-1", start_date=SOME_DATE), "reason", EXECUTION_TIMESTAMP)
    assert row["start_date_source"] == "JOBVITE"
    assert row["effective_start_date"] == SOME_DATE


def test_build_row_reports_calculated_start_date_source_when_jobvite_date_missing():
    row = reporting._build_row(_candidate("APP-1", start_date=None), "reason", EXECUTION_TIMESTAMP)
    assert row["start_date_source"] == "CALCULATED"
    assert row["effective_start_date"] == datetime.date(2026, 11, 1)


# ---------------------------------------------------------------------------
# _build_ready_to_post_row() -- the clean, executive-facing sheet row.
# payload_ready is passed in (computed once upstream by
# _build_payload_preview_row()), never recomputed here.
# ---------------------------------------------------------------------------

def test_build_ready_to_post_row_has_all_columns():
    row = reporting._build_ready_to_post_row(_complete_candidate(), _OFFICE, EXECUTION_TIMESTAMP, payload_ready=True)
    assert set(row.keys()) == set(reporting.READY_TO_POST_COLUMNS)


def test_build_ready_to_post_row_uses_business_policy_label():
    row_basic = reporting._build_ready_to_post_row(_complete_candidate(is_vip=False), _OFFICE, EXECUTION_TIMESTAMP, payload_ready=True)
    row_vip = reporting._build_ready_to_post_row(_complete_candidate(is_vip=True), _OFFICE, EXECUTION_TIMESTAMP, payload_ready=False)

    assert row_basic["Policy"] == "Basic"
    assert row_vip["Policy"] == "VIP"


def test_build_ready_to_post_row_mobility_vip_and_tier_columns():
    row_basic = reporting._build_ready_to_post_row(_complete_candidate(is_vip=False), _OFFICE, EXECUTION_TIMESTAMP, payload_ready=True)
    row_vip = reporting._build_ready_to_post_row(_complete_candidate(is_vip=True), _OFFICE, EXECUTION_TIMESTAMP, payload_ready=True)

    assert row_basic["Mobility VIP"] == "No"
    assert row_basic["Policy (Tier)"] == "Tier 1"

    assert row_vip["Mobility VIP"] == "Yes"
    assert row_vip["Policy (Tier)"] == "Tier 2"


def test_build_ready_to_post_row_reflects_passed_in_payload_ready():
    row_ready = reporting._build_ready_to_post_row(_complete_candidate(), _OFFICE, EXECUTION_TIMESTAMP, payload_ready=True)
    row_not_ready = reporting._build_ready_to_post_row(_complete_candidate(), _OFFICE, EXECUTION_TIMESTAMP, payload_ready=False)

    assert row_ready["Payload Ready"] is True
    assert row_not_ready["Payload Ready"] is False


def test_build_ready_to_post_row_unresolved_office_leaves_office_fields_none():
    row = reporting._build_ready_to_post_row(_complete_candidate(), None, EXECUTION_TIMESTAMP, payload_ready=False)

    assert row["Resolved Office"] is None
    assert row["OfficeId"] is None
    assert row["Host Country"] is None


# ---------------------------------------------------------------------------
# _build_payload_preview_row() -- reuses posting_service's real payload
# builders, plus data-quality flags. Formerly named _build_ready_to_post_row
# before the 2026-08-10 report redesign split the exec sheet from this one.
# ---------------------------------------------------------------------------

def test_build_payload_preview_row_has_all_columns():
    row = reporting._build_payload_preview_row(_complete_candidate(), _OFFICE, EXECUTION_TIMESTAMP, selected=False, terminal_eids=set())
    assert set(row.keys()) == set(reporting.PAYLOAD_PREVIEW_COLUMNS)


def test_build_payload_preview_row_create_payload_matches_posting_service_builder():
    from app.services import posting_service

    candidate = _complete_candidate()
    effective_start_date, _ = reporting.resolve_effective_start_date(candidate["start_date"], EXECUTION_TIMESTAMP)
    expected = posting_service.build_benivo_payload(candidate, _OFFICE, effective_start_date)

    row = reporting._build_payload_preview_row(candidate, _OFFICE, EXECUTION_TIMESTAMP, selected=False, terminal_eids=set())

    assert row["create_firstName"] == expected["firstName"]
    assert row["create_lastName"] == expected["lastName"]
    assert row["create_email"] == expected["email"]
    assert row["create_homeCountry"] == expected["homeCountry"] == "Serbia"
    assert row["create_policy"] == expected["policy"] == "Tier 1"
    assert row["create_officeId"] == expected["officeId"] == "office-1"
    assert row["create_officeName"] == expected["officeName"] == "Serbia (Live Casino)"
    assert row["create_startDateOfAssignment"] == expected["startDateOfAssignment"]


def test_build_payload_preview_row_case_id_pending_when_not_yet_posted():
    candidate = _complete_candidate(benivo_assignment_id=None)
    row = reporting._build_payload_preview_row(candidate, _OFFICE, EXECUTION_TIMESTAMP, selected=False, terminal_eids=set())

    assert row["case_caseId_available"] == "PENDING_CREATE_USER"
    assert row["case_hostJobRole"] == "Game Presenter"
    assert row["case_home_country"] == "Serbia"
    assert row["case_home_country_iso"] == "RS"


def test_build_payload_preview_row_case_id_shown_when_already_posted():
    candidate = _complete_candidate(benivo_assignment_id=1010696)
    row = reporting._build_payload_preview_row(candidate, _OFFICE, EXECUTION_TIMESTAMP, selected=False, terminal_eids=set())

    assert row["case_caseId_available"] == 1010696


def test_build_payload_preview_row_mobility_vip_and_population_basic():
    candidate = _complete_candidate(is_vip=False)
    row = reporting._build_payload_preview_row(candidate, _OFFICE, EXECUTION_TIMESTAMP, selected=False, terminal_eids=set())

    assert row["mobility_vip"] == "No"
    assert row["create_policy"] == "Tier 1"


def test_build_payload_preview_row_mobility_vip_and_population_vip():
    candidate = _complete_candidate(is_vip=True)
    row = reporting._build_payload_preview_row(candidate, _OFFICE, EXECUTION_TIMESTAMP, selected=False, terminal_eids=set())

    assert row["mobility_vip"] == "Yes"
    assert row["create_policy"] == "Tier 2"


def test_build_payload_preview_row_fully_ready_when_complete():
    candidate = _complete_candidate()
    row = reporting._build_payload_preview_row(candidate, _OFFICE, EXECUTION_TIMESTAMP, selected=False, terminal_eids=set())

    assert row["ready_to_create_user"] is True
    assert row["ready_to_update_case"] is True
    assert row["payload_ready"] is True
    assert row["reason_not_payload_ready"] == ""
    assert row["missing_home_country"] is False
    assert row["missing_job_title"] is False
    assert row["unresolved_office"] is False
    assert row["invalid_policy"] is False


def test_build_payload_preview_row_missing_home_country_blocks_case_readiness_only():
    # homeCountry is required for create-user readiness AND is the Case
    # PATCH's homeLocation.country -- missing it blocks both.
    candidate = _complete_candidate(home_country=None)
    row = reporting._build_payload_preview_row(candidate, _OFFICE, EXECUTION_TIMESTAMP, selected=False, terminal_eids=set())

    assert row["missing_home_country"] is True
    assert row["ready_to_create_user"] is False
    assert row["ready_to_update_case"] is False
    assert row["payload_ready"] is False
    assert "Create User missing: homeCountry" in row["reason_not_payload_ready"]
    assert "Case Update missing: homeLocation.country" in row["reason_not_payload_ready"]


def test_build_payload_preview_row_missing_job_title_blocks_case_readiness_only():
    # job_title only feeds the Case PATCH's hostJobRole -- create-user
    # readiness is unaffected by it.
    candidate = _complete_candidate(job_title=None)
    row = reporting._build_payload_preview_row(candidate, _OFFICE, EXECUTION_TIMESTAMP, selected=False, terminal_eids=set())

    assert row["missing_job_title"] is True
    assert row["ready_to_create_user"] is True
    assert row["ready_to_update_case"] is False
    assert row["payload_ready"] is False
    assert row["reason_not_payload_ready"] == "Case Update missing: hostJobRole"


def test_build_payload_preview_row_unresolved_office_blocks_create_user_readiness():
    candidate = _complete_candidate()
    row = reporting._build_payload_preview_row(candidate, None, EXECUTION_TIMESTAMP, selected=False, terminal_eids=set())

    assert row["unresolved_office"] is True
    assert row["resolved_office_name"] is None
    assert row["ready_to_create_user"] is False
    assert row["payload_ready"] is False


def test_build_payload_preview_row_vip_resolves_to_tier_2_and_is_ready():
    # Confirmed 2026-08-24: VIP now resolves to a confirmed Benivo policy
    # tier ("Tier 2"), so it no longer blocks readiness -- see policy_service.py.
    candidate = _complete_candidate(is_vip=True)
    row = reporting._build_payload_preview_row(candidate, _OFFICE, EXECUTION_TIMESTAMP, selected=False, terminal_eids=set())

    assert row["invalid_policy"] is False
    assert row["create_policy"] == "Tier 2"
    assert row["ready_to_create_user"] is True
    assert row["payload_ready"] is True


def test_build_payload_preview_row_has_terminal_create_user_result_reflects_terminal_eids():
    candidate = _complete_candidate(application_eid="APP-TERMINAL")
    row = reporting._build_payload_preview_row(
        candidate, _OFFICE, EXECUTION_TIMESTAMP, selected=False, terminal_eids={"APP-TERMINAL"}
    )

    assert row["has_terminal_create_user_result"] is True


def test_write_table_sheet_uses_custom_columns():
    wb = Workbook()
    ws = wb.active
    reporting._write_table_sheet(ws, [{"a": 1, "b": 2}], columns=["b", "a"])

    header = [cell.value for cell in ws[1]]
    data_row = [cell.value for cell in ws[2]]
    assert header == ["b", "a"]
    assert data_row == [2, 1]


# ---------------------------------------------------------------------------
# _write_summary_sheet() -- section headers render bold, metrics render as
# plain label/value rows
# ---------------------------------------------------------------------------

def test_write_summary_sheet_renders_section_header_bold():
    wb = Workbook()
    ws = wb.active
    reporting._write_summary_sheet(ws, [("Total Candidates", 840), ("Data Quality", reporting.SECTION_HEADER), ("Office Mapping Completeness", "99.3%")])

    values = [[cell.value for cell in row] for row in ws.iter_rows()]
    assert values[1] == ["Total Candidates", 840]
    # Section header renders as a blank spacer row then a single-cell label row.
    section_row_idx = next(i for i, row in enumerate(values) if row and row[0] == "Data Quality")
    assert ws.cell(row=section_row_idx + 1, column=1).font.bold is True
    assert values[-1] == ["Office Mapping Completeness", "99.3%"]


# ---------------------------------------------------------------------------
# _build_posting_results_row() / _group_post_log_rows_by_candidate() --
# built directly from benivo.post_log rows for a given run_id
# ---------------------------------------------------------------------------

def test_group_post_log_rows_by_candidate_groups_by_application_eid():
    rows = [
        {"application_eid": "APP-1", "action": "CREATE_USER", "status": "SUCCESS"},
        {"application_eid": "APP-1", "action": "UPDATE_CASE", "status": "FAILED"},
        {"application_eid": "APP-2", "action": "CREATE_USER", "status": "ALREADY_EXISTS"},
    ]

    grouped = reporting._group_post_log_rows_by_candidate(rows)

    assert set(grouped.keys()) == {"APP-1", "APP-2"}
    assert grouped["APP-1"]["CREATE_USER"]["status"] == "SUCCESS"
    assert grouped["APP-1"]["UPDATE_CASE"]["status"] == "FAILED"
    assert "UPDATE_CASE" not in grouped["APP-2"]


def test_build_posting_results_row_success_with_case_update():
    actions = {
        "CREATE_USER": {"status": "SUCCESS", "benivo_user_id": 605114, "benivo_assignment_id": 1010696, "posted_at": EXECUTION_TIMESTAMP},
        "UPDATE_CASE": {"status": "FAILED", "posted_at": EXECUTION_TIMESTAMP},
    }
    candidates_by_eid = {"APP-1": {"first_name": "Jane", "last_name": "Doe"}}

    row = reporting._build_posting_results_row("APP-1", actions, candidates_by_eid)

    assert row["Candidate"] == "Jane Doe"
    assert row["Application EID"] == "APP-1"
    assert row["Create User Result"] == "SUCCESS"
    assert row["Case Update Result"] == "FAILED"
    assert row["Benivo User Id"] == 605114
    assert row["Assignment Id"] == 1010696
    assert row["Execution Time"] == EXECUTION_TIMESTAMP.replace(tzinfo=None)


def test_build_posting_results_row_case_update_not_attempted_when_absent():
    actions = {"CREATE_USER": {"status": "ALREADY_EXISTS", "benivo_user_id": 1, "benivo_assignment_id": 2, "posted_at": EXECUTION_TIMESTAMP}}
    candidates_by_eid = {"APP-1": {"first_name": "Jane", "last_name": "Doe"}}

    row = reporting._build_posting_results_row("APP-1", actions, candidates_by_eid)

    assert row["Case Update Result"] == "NOT_ATTEMPTED"


def test_build_posting_results_row_falls_back_to_eid_when_candidate_unknown():
    actions = {"CREATE_USER": {"status": "SUCCESS", "benivo_user_id": 1, "benivo_assignment_id": 2, "posted_at": EXECUTION_TIMESTAMP}}

    row = reporting._build_posting_results_row("APP-UNKNOWN", actions, {})

    assert row["Candidate"] == "APP-UNKNOWN"


# ---------------------------------------------------------------------------
# generate_reports() end-to-end (mocked repositories + no reference-data calls)
# ---------------------------------------------------------------------------

def _mock_population():
    # benivo_status here reflects what classification_service would already
    # have persisted (READY_TO_POST vs PENDING_OFFICE_MAPPING) -- reporting
    # trusts this status directly rather than re-deriving it. CALC-1 has no
    # Jobvite start_date but is READY_TO_POST -- under the current rule, a
    # calculated date makes it just as postable as a Jobvite-sourced one.
    return [
        _candidate("READY-1", is_relocation_required="Yes", start_date=SOME_DATE, workplace="Serbia Live Casino", benivo_status="READY_TO_POST", home_country="Serbia"),
        _candidate("OFFICE-PENDING-1", is_relocation_required="Yes", start_date=SOME_DATE, workplace="Unmapped Site", benivo_status="PENDING_OFFICE_MAPPING"),
        _candidate("CALC-1", is_relocation_required="Yes", start_date=None, workplace="Serbia Live Casino", benivo_status="READY_TO_POST", home_country="Serbia"),
        _candidate("REVIEW-NO-1", is_relocation_required="No", start_date=None, benivo_status="NEEDS_RECRUITER_REVIEW"),
        _candidate("REVIEW-BLANK-1", is_relocation_required=None, start_date=None, benivo_status="NEEDS_RECRUITER_REVIEW"),
    ]


def _patch_common(monkeypatch, tmp_path, population, post_log_rows=None, scope_history=None, go_live_at=None):
    monkeypatch.setattr(reporting, "_report_dir", lambda: tmp_path)
    monkeypatch.setattr(reporting.candidate_repository, "get_all_candidates_for_report", lambda: population)
    monkeypatch.setattr(reporting.candidate_repository, "get_scope_history_map", lambda: scope_history or {})
    monkeypatch.setattr(reporting.post_log_repository, "get_terminal_post_log_application_eids", lambda: set())
    monkeypatch.setattr(reporting.post_log_repository, "get_post_log_rows_for_run", lambda run_id: post_log_rows or [])
    monkeypatch.setattr(reporting.posting_service, "allow_reference_data_calls", lambda: False)
    monkeypatch.setattr(reporting.config, "go_live_at", lambda: go_live_at)


def test_generate_reports_produces_expected_sheet_names(tmp_path, monkeypatch):
    _patch_common(monkeypatch, tmp_path, _mock_population())

    report_path, _metrics = reporting.generate_reports(selected_candidates=[], dry_run=True, posting_limit=5, run_id="run-1")

    wb = load_workbook(report_path)
    assert set(wb.sheetnames) == {
        "Instructions", "Executive Summary", "Ready To Post", "Posting Results",
        "Pending Office Mapping", "Pending Recruiter Review", "Country Data Issues", "Payload Preview",
        "Go-Live Status",
    }
    assert wb.sheetnames[0] == "Instructions"
    assert wb.sheetnames[1] == "Executive Summary"
    # Jobvite Start Date / Calculated Start Date / Unresolved Start Date /
    # Relocation Field Review / Missing Office Mapping / Summary must be gone.
    assert "Jobvite Start Date" not in wb.sheetnames
    assert "Calculated Start Date" not in wb.sheetnames
    assert "Unresolved Start Date" not in wb.sheetnames
    assert "Summary" not in wb.sheetnames


def test_generate_reports_go_live_status_sheet_covers_all_four_categories(tmp_path, monkeypatch):
    population = [
        _candidate("BACKLOG-1", benivo_status="READY_TO_POST"),  # no scope_history row at all -> backlog
        _candidate("NEW-READY-1", benivo_status="READY_TO_POST"),
        _candidate("NEW-NOT-READY-1", benivo_status="PENDING_OFFICE_MAPPING"),
        _candidate("POSTED-1", benivo_status="POSTED"),
    ]
    scope_history = {
        "NEW-READY-1": AFTER_GO_LIVE,
        "NEW-NOT-READY-1": AFTER_GO_LIVE,
        "POSTED-1": BEFORE_GO_LIVE,
    }
    _patch_common(monkeypatch, tmp_path, population, scope_history=scope_history, go_live_at=GO_LIVE_AT)

    report_path, metrics = reporting.generate_reports(selected_candidates=[], dry_run=True, posting_limit=5, run_id="run-1")

    wb = load_workbook(report_path)
    ws = wb["Go-Live Status"]
    header = [c.value for c in ws[2]]
    rows_by_eid = {
        row[header.index("application_eid")]: dict(zip(header, row))
        for row in ws.iter_rows(min_row=3, values_only=True)
    }

    assert rows_by_eid["BACKLOG-1"]["go_live_category"] == reporting.GO_LIVE_PRE_GO_LIVE_BACKLOG
    assert rows_by_eid["NEW-READY-1"]["go_live_category"] == reporting.GO_LIVE_AUTOMATICALLY_ELIGIBLE
    assert rows_by_eid["NEW-NOT-READY-1"]["go_live_category"] == reporting.GO_LIVE_NEWLY_ELIGIBLE
    assert rows_by_eid["POSTED-1"]["go_live_category"] == reporting.GO_LIVE_ALREADY_POSTED

    assert metrics["go_live_pre_backlog"] == 1
    assert metrics["go_live_automatically_eligible"] == 1
    assert metrics["go_live_newly_eligible"] == 1
    assert metrics["go_live_already_posted"] == 1


def test_generate_reports_go_live_status_all_backlog_when_not_configured(tmp_path, monkeypatch):
    # go_live_at unset -- identical to pre-go-live behavior, nothing is
    # ever "new" until the cutover is deliberately configured.
    population = [_candidate("APP-1", benivo_status="READY_TO_POST")]
    _patch_common(monkeypatch, tmp_path, population, scope_history={"APP-1": AFTER_GO_LIVE}, go_live_at=None)

    report_path, metrics = reporting.generate_reports(selected_candidates=[], dry_run=True, posting_limit=5, run_id="run-1")

    wb = load_workbook(report_path)
    ws = wb["Go-Live Status"]
    header = [c.value for c in ws[2]]
    row = dict(zip(header, next(ws.iter_rows(min_row=3, values_only=True))))

    assert row["go_live_category"] == reporting.GO_LIVE_PRE_GO_LIVE_BACKLOG
    assert metrics["go_live_pre_backlog"] == 1
    assert metrics["go_live_automatically_eligible"] == 0


def test_generate_reports_ready_to_post_and_payload_preview_agree_on_readiness(tmp_path, monkeypatch):
    _patch_common(monkeypatch, tmp_path, _mock_population())

    report_path, _metrics = reporting.generate_reports(selected_candidates=[], dry_run=True, posting_limit=5, run_id="run-1")

    wb = load_workbook(report_path)

    ready_ws = wb["Ready To Post"]
    ready_header = [c.value for c in ready_ws[2]]
    assert ready_header == reporting.READY_TO_POST_COLUMNS
    ready_rows = {row[0]: dict(zip(ready_header, row)) for row in ready_ws.iter_rows(min_row=3, values_only=True)}

    preview_ws = wb["Payload Preview"]
    preview_header = [c.value for c in preview_ws[2]]
    assert preview_header == reporting.PAYLOAD_PREVIEW_COLUMNS
    preview_rows = {row[0]: dict(zip(preview_header, row)) for row in preview_ws.iter_rows(min_row=3, values_only=True)}

    assert set(ready_rows.keys()) == {"READY-1", "CALC-1"}
    assert set(preview_rows.keys()) == {"READY-1", "CALC-1"}

    for eid in ready_rows:
        assert ready_rows[eid]["Payload Ready"] == preview_rows[eid]["payload_ready"]

    # No refdata fetched (allow_reference_data_calls=False) -- office
    # unresolved for both, so payload_ready is False for both here.
    assert ready_rows["READY-1"]["Payload Ready"] is False
    assert preview_rows["READY-1"]["unresolved_office"] is True


def test_generate_reports_country_data_issues_sheet_includes_missing_and_fallback(tmp_path, monkeypatch):
    population = [
        _candidate("READY-NO-COUNTRY-AT-ALL", benivo_status="READY_TO_POST", home_country=None, current_country=None),
        _candidate("PENDING-NO-HOME-HAS-CURRENT", benivo_status="PENDING_OFFICE_MAPPING", workplace="Unmapped Site", home_country=None, current_country="Belarus"),
        _candidate("READY-WITH-PRIMARY-HOME", benivo_status="READY_TO_POST", home_country="Serbia", current_country="Georgia"),
    ]
    _patch_common(monkeypatch, tmp_path, population)

    report_path, _metrics = reporting.generate_reports(selected_candidates=[], dry_run=True, posting_limit=5, run_id="run-1")

    wb = load_workbook(report_path)
    ws = wb["Country Data Issues"]
    header = [c.value for c in ws[2]]
    assert header == reporting.COUNTRY_DATA_ISSUES_COLUMNS

    rows = {row[0]: dict(zip(header, row)) for row in ws.iter_rows(min_row=3, values_only=True)}

    # Only the missing and fallback-using candidates appear -- the one with
    # a primary candidate_home_country is fully clean, not an "issue".
    assert set(rows.keys()) == {"READY-NO-COUNTRY-AT-ALL", "PENDING-NO-HOME-HAS-CURRENT"}

    missing_row = rows["READY-NO-COUNTRY-AT-ALL"]
    assert missing_row["Country Source"] == "MISSING"
    assert missing_row["Country Sent to Benivo"] is None
    assert missing_row["Operational Status"] == "READY_TO_POST"

    fallback_row = rows["PENDING-NO-HOME-HAS-CURRENT"]
    assert fallback_row["Candidate Home Country"] is None
    assert fallback_row["Current Country"] == "Belarus"
    assert fallback_row["Country Sent to Benivo"] == "Belarus"
    assert fallback_row["Country Source"] == "CURRENT_LOCATION"
    assert fallback_row["Operational Status"] == "PENDING_OFFICE_MAPPING"

    summary = {row[0].value: row[1].value for row in wb["Executive Summary"].iter_rows(min_row=2) if row[0].value}
    assert summary["Candidate Home Country"] == "1 (33.3%)"
    assert summary["Current Location Fallback"] == "1 (33.3%)"
    assert summary["Missing"] == "1 (33.3%)"


def test_generate_reports_country_data_issues_sheet_absent_when_clean(tmp_path, monkeypatch):
    # Exception-only sheet: no worksheet at all when there's nothing to
    # show, not an empty-with-note sheet -- the KPI in Executive Summary
    # still shows 0 either way.
    population = [_candidate("READY-1", benivo_status="READY_TO_POST", home_country="Serbia")]
    _patch_common(monkeypatch, tmp_path, population)

    report_path, _metrics = reporting.generate_reports(selected_candidates=[], dry_run=True, posting_limit=5, run_id="run-1")

    wb = load_workbook(report_path)
    assert "Country Data Issues" not in wb.sheetnames

    summary = {row[0].value: row[1].value for row in wb["Executive Summary"].iter_rows(min_row=2) if row[0].value}
    assert summary["Missing"] == "0 (0.0%)"


def test_generate_reports_exception_sheets_absent_when_all_clean(tmp_path, monkeypatch):
    population = [_candidate("READY-1", benivo_status="READY_TO_POST", home_country="Serbia")]
    _patch_common(monkeypatch, tmp_path, population)

    report_path, _metrics = reporting.generate_reports(selected_candidates=[], dry_run=True, posting_limit=5, run_id="run-1")

    wb = load_workbook(report_path)
    assert "Pending Office Mapping" not in wb.sheetnames
    assert "Pending Recruiter Review" not in wb.sheetnames
    assert "Country Data Issues" not in wb.sheetnames

    summary = {row[0].value: row[1].value for row in wb["Executive Summary"].iter_rows(min_row=2) if row[0].value}
    assert summary["Pending Office Mapping"] == "0 (0.0%)"
    assert summary["Pending Recruiter Review"] == "0 (0.0%)"


def test_generate_reports_exception_sheets_present_when_nonempty(tmp_path, monkeypatch):
    _patch_common(monkeypatch, tmp_path, _mock_population())

    report_path, _metrics = reporting.generate_reports(selected_candidates=[], dry_run=True, posting_limit=5, run_id="run-1")

    wb = load_workbook(report_path)
    # _mock_population() has one PENDING_OFFICE_MAPPING candidate (also
    # missing home_country -> also a Country Data Issue) and two
    # NEEDS_RECRUITER_REVIEW candidates -- all three exception sheets must
    # exist and be non-empty.
    assert "Pending Office Mapping" in wb.sheetnames
    assert "Pending Recruiter Review" in wb.sheetnames
    assert "Country Data Issues" in wb.sheetnames
    assert wb["Pending Office Mapping"].max_row >= 3
    assert wb["Pending Recruiter Review"].max_row >= 3
    assert wb["Country Data Issues"].max_row >= 3


def test_generate_reports_ready_to_post_includes_country_columns(tmp_path, monkeypatch):
    population = [_candidate("READY-1", benivo_status="READY_TO_POST", home_country=None, current_country="Belarus")]
    _patch_common(monkeypatch, tmp_path, population)

    report_path, _metrics = reporting.generate_reports(selected_candidates=[], dry_run=True, posting_limit=5, run_id="run-1")

    wb = load_workbook(report_path)
    ws = wb["Ready To Post"]
    header = [c.value for c in ws[2]]
    row = dict(zip(header, next(ws.iter_rows(min_row=3, values_only=True))))

    assert row["Candidate Home Country"] is None
    assert row["Current Country"] == "Belarus"
    assert row["Country Sent to Benivo"] == "Belarus"
    assert row["Country Source"] == "CURRENT_LOCATION"


def test_generate_reports_payload_preview_shows_home_country_source_and_uses_fallback(tmp_path, monkeypatch):
    population = [_candidate("READY-1", benivo_status="READY_TO_POST", home_country=None, current_country="Belarus", job_title="Presenter")]
    _patch_common(monkeypatch, tmp_path, population)

    report_path, _metrics = reporting.generate_reports(selected_candidates=[], dry_run=True, posting_limit=5, run_id="run-1")

    wb = load_workbook(report_path)
    ws = wb["Payload Preview"]
    header = [c.value for c in ws[2]]
    row = dict(zip(header, next(ws.iter_rows(min_row=3, values_only=True))))

    assert row["home_country"] is None
    assert row["current_country"] == "Belarus"
    assert row["home_country_source"] == "CURRENT_LOCATION"
    # The actual payload preview fields must carry the EFFECTIVE (fallback) value.
    assert row["create_homeCountry"] == "Belarus"
    assert row["case_home_country"] == "Belarus"
    assert row["case_home_country_iso"] == "BY"
    # missing_home_country now reflects the effective value, not just the primary.
    assert row["missing_home_country"] is False


def test_generate_reports_posting_results_built_from_post_log_not_memory(tmp_path, monkeypatch):
    population = [_candidate("APP-1", benivo_status="POSTED", home_country="Serbia")]
    post_log_rows = [
        {"application_eid": "APP-1", "candidate_eid": "C-APP-1", "email": "APP-1@example.com", "action": "CREATE_USER",
         "status": "SUCCESS", "benivo_user_id": 605114, "benivo_assignment_id": 1010696, "error_message": None, "posted_at": EXECUTION_TIMESTAMP},
        {"application_eid": "APP-1", "candidate_eid": "C-APP-1", "email": "APP-1@example.com", "action": "UPDATE_CASE",
         "status": "FAILED", "benivo_user_id": None, "benivo_assignment_id": 1010696, "error_message": "server error", "posted_at": EXECUTION_TIMESTAMP},
    ]
    _patch_common(monkeypatch, tmp_path, population, post_log_rows=post_log_rows)

    report_path, _metrics = reporting.generate_reports(selected_candidates=[], dry_run=False, posting_limit=5, run_id="run-1")

    wb = load_workbook(report_path)
    ws = wb["Posting Results"]
    header = [c.value for c in ws[2]]
    assert header == reporting.POSTING_RESULTS_COLUMNS

    rows = list(ws.iter_rows(min_row=3, values_only=True))
    assert len(rows) == 1
    row = dict(zip(header, rows[0]))
    assert row["Application EID"] == "APP-1"
    assert row["Create User Result"] == "SUCCESS"
    assert row["Case Update Result"] == "FAILED"
    assert row["Benivo User Id"] == 605114
    assert row["Assignment Id"] == 1010696

    summary = {row[0].value: row[1].value for row in wb["Executive Summary"].iter_rows(min_row=2) if row[0].value}
    assert summary["Successfully Posted"] == "1 (100.0%)"
    assert summary["Failed"] == "0 (0.0%)"


def test_generate_reports_posting_results_empty_on_dry_run(tmp_path, monkeypatch):
    _patch_common(monkeypatch, tmp_path, _mock_population(), post_log_rows=[])

    report_path, _metrics = reporting.generate_reports(selected_candidates=[], dry_run=True, posting_limit=5, run_id="run-1")

    wb = load_workbook(report_path)
    assert wb["Posting Results"]["A2"].value == reporting.DRY_RUN_NOTE


def test_generate_reports_sync_metrics_shown_when_provided(tmp_path, monkeypatch):
    _patch_common(monkeypatch, tmp_path, _mock_population())

    report_path, _metrics = reporting.generate_reports(
        selected_candidates=[], dry_run=True, posting_limit=5, run_id="run-1",
        sync_metrics={"inserted_or_updated": 12, "removed": 3},
    )

    wb = load_workbook(report_path)
    summary = {row[0].value: row[1].value for row in wb["Executive Summary"].iter_rows(min_row=2) if row[0].value}
    assert summary["Candidates Synced"] == 12
    assert summary["Candidates Removed"] == 3


def test_generate_reports_sync_metrics_shown_as_na_when_absent(tmp_path, monkeypatch):
    _patch_common(monkeypatch, tmp_path, _mock_population())

    report_path, _metrics = reporting.generate_reports(selected_candidates=[], dry_run=True, posting_limit=5, run_id="run-1")

    wb = load_workbook(report_path)
    summary = {row[0].value: row[1].value for row in wb["Executive Summary"].iter_rows(min_row=2) if row[0].value}
    assert summary["Candidates Synced"] == "N/A (sync not run this execution)"
    assert summary["Candidates Removed"] == "N/A (sync not run this execution)"


def test_generate_reports_executive_summary_has_data_quality_section(tmp_path, monkeypatch):
    _patch_common(monkeypatch, tmp_path, _mock_population())

    report_path, _metrics = reporting.generate_reports(selected_candidates=[], dry_run=True, posting_limit=5, run_id="run-1")

    wb = load_workbook(report_path)
    labels = [row[0].value for row in wb["Executive Summary"].iter_rows(min_row=2) if row[0].value]

    assert "Data Quality" in labels
    assert "Office Mapping Completeness" in labels
    assert "Home Country Completeness (Effective, Ready To Post)" in labels
    assert "Ready To Create User (Ready To Post)" in labels
    assert "Ready To Update Case (Ready To Post)" in labels
    assert "Fully Payload Ready (Ready To Post)" in labels


def test_generate_reports_mobility_vip_distribution_counts_tier_1_and_tier_2(tmp_path, monkeypatch):
    population = [
        _candidate("BASIC-1", is_vip=False, benivo_status="READY_TO_POST"),
        _candidate("BASIC-2", is_vip=False, benivo_status="READY_TO_POST"),
        _candidate("VIP-1", is_vip=True, benivo_status="READY_TO_POST"),
    ]
    _patch_common(monkeypatch, tmp_path, population)

    report_path, metrics = reporting.generate_reports(selected_candidates=[], dry_run=True, posting_limit=5, run_id="run-1")

    wb = load_workbook(report_path)
    rows = {row[0].value: row[1].value for row in wb["Executive Summary"].iter_rows(min_row=2) if row[0].value}

    assert "Mobility VIP Distribution" in rows
    assert rows["Tier 1"] == "2 (66.7%)"
    assert rows["Tier 2"] == "1 (33.3%)"
    assert metrics["mobility_vip_tier_1"] == 2
    assert metrics["mobility_vip_tier_2"] == 1


def test_generate_reports_selected_count_never_exceeds_limit(tmp_path, monkeypatch):
    _patch_common(monkeypatch, tmp_path, _mock_population())

    selected = _mock_population()[:2]

    reporting.generate_reports(selected_candidates=selected, dry_run=True, posting_limit=5, run_id="run-1")

    assert len(selected) <= 5
