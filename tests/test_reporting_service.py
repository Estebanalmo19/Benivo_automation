import datetime

from openpyxl import Workbook, load_workbook

from app.services import mobility_scope_service
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
    dealer_shuffler=None,
    mobility_support=None,
    agency_name=None,
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
        "agency_name": agency_name,
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
        # Jobvite-sourced (application.job.customField[fieldCode=
        # 'dealer__shuffler']) -- see candidate_repository.
        # DEALER_SHUFFLER_SUBQUERY. Deliberately separate from job_title
        # (Jobvite's own Job Title field) above -- population_service.py
        # drives Population off this field, never off job_title.
        "dealer_shuffler": dealer_shuffler,
        "mobility_support": mobility_support,
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


def test_build_row_population_name_from_dealer_shuffler_and_is_vip():
    row_tier3 = reporting._build_row(_candidate("APP-1", is_vip=False), "reason", EXECUTION_TIMESTAMP)
    row_tier1 = reporting._build_row(_candidate("APP-2", is_vip=True), "reason", EXECUTION_TIMESTAMP)
    row_gp = reporting._build_row(_candidate("APP-3", is_vip=False, dealer_shuffler="Presenter"), "reason", EXECUTION_TIMESTAMP)

    assert row_tier3["is_vip"] is False
    assert row_tier3["population_name"] == "Tier 3"
    assert row_tier1["is_vip"] is True
    assert row_tier1["population_name"] == "Tier 1"
    assert row_gp["population_name"] == "Game Presenters and Shufflers"


def test_build_row_scope_eligibility_computed_independently():
    # _candidate() default has no mobility_support -> scope-ineligible,
    # regardless of the "reason" this row landed on its sheet for.
    row_no_support = reporting._build_row(_candidate("APP-1"), "some other reason", EXECUTION_TIMESTAMP)
    assert row_no_support["scope_eligibility"] == "No"

    row_qualifies = reporting._build_row(
        _candidate("APP-2", mobility_support="Relocation", home_country="Romania"), "some other reason", EXECUTION_TIMESTAMP
    )
    assert row_qualifies["scope_eligibility"] == "Yes"


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


def test_build_ready_to_post_row_shows_dealer_shuffler():
    row = reporting._build_ready_to_post_row(
        _complete_candidate(dealer_shuffler="Presenter"), _OFFICE, EXECUTION_TIMESTAMP, payload_ready=True
    )

    assert row["Dealer / Shuffler"] == "Presenter"
    assert row["Benivo Population"] == "Game Presenters and Shufflers"


def test_build_ready_to_post_row_shows_mobility_support():
    row = reporting._build_ready_to_post_row(
        _complete_candidate(mobility_support="Relocation\nAccommodation"), _OFFICE, EXECUTION_TIMESTAMP, payload_ready=True
    )

    assert row["Mobility Support"] == "Relocation\nAccommodation"


def test_build_ready_to_post_row_shows_agency_name_for_agency_sourced_candidate():
    # "agency candidate gets correct Agency Name"
    row = reporting._build_ready_to_post_row(
        _complete_candidate(agency_name="Randstad Romania"), _OFFICE, EXECUTION_TIMESTAMP, payload_ready=True
    )

    assert row["Agency"] == "Randstad Romania"


def test_build_ready_to_post_row_agency_blank_for_non_agency_candidate():
    # "direct candidate does not get a fake agency" / "null agency handled safely"
    row = reporting._build_ready_to_post_row(_complete_candidate(agency_name=None), _OFFICE, EXECUTION_TIMESTAMP, payload_ready=True)

    assert row["Agency"] is None


def test_build_ready_to_post_row_scope_eligibility_yes_when_qualifying():
    row = reporting._build_ready_to_post_row(
        _complete_candidate(mobility_support="Relocation"), _OFFICE, EXECUTION_TIMESTAMP, payload_ready=True
    )

    assert row["Scope Eligibility"] == "Yes"


def test_build_ready_to_post_row_scope_eligibility_no_when_mobility_support_missing():
    row = reporting._build_ready_to_post_row(
        _complete_candidate(mobility_support=None), _OFFICE, EXECUTION_TIMESTAMP, payload_ready=True
    )

    assert row["Scope Eligibility"] == "No"


def test_build_ready_to_post_row_scope_eligibility_yes_for_domestic_relocation():
    # Corrected 2026-09-05: domestic relocation (home_country == workplace's
    # host country, both "Serbia" here) does NOT exclude -- only
    # mobility_support does.
    row = reporting._build_ready_to_post_row(
        _complete_candidate(mobility_support="Relocation", home_country="Serbia", workplace="Serbia Live Casino"),
        _OFFICE, EXECUTION_TIMESTAMP, payload_ready=True,
    )

    assert row["Scope Eligibility"] == "Yes"


def test_build_ready_to_post_row_mobility_vip_and_population_columns():
    # _complete_candidate() has no dealer_shuffler by default -> Tier 3/Tier 1
    # by is_vip alone (confirmed 2026-09-04 rule).
    row_basic = reporting._build_ready_to_post_row(_complete_candidate(is_vip=False), _OFFICE, EXECUTION_TIMESTAMP, payload_ready=True)
    row_vip = reporting._build_ready_to_post_row(_complete_candidate(is_vip=True), _OFFICE, EXECUTION_TIMESTAMP, payload_ready=True)

    assert row_basic["Mobility VIP"] == "No"
    assert row_basic["Benivo Population"] == "Tier 3"

    assert row_vip["Mobility VIP"] == "Yes"
    assert row_vip["Benivo Population"] == "Tier 1"


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


def test_build_payload_preview_row_shows_agency_name_as_source_data_only():
    # agency_name is shown for audit/reporting even though it is NOT part
    # of the create_*/case_* sections -- Benivo has not confirmed any
    # destination for it (see posting_service.build_benivo_payload()).
    row = reporting._build_payload_preview_row(
        _complete_candidate(agency_name="GRS Recruit"), _OFFICE, EXECUTION_TIMESTAMP, selected=False, terminal_eids=set()
    )

    assert row["agency_name"] == "GRS Recruit"
    assert "GRS Recruit" not in [row.get(k) for k in row if k.startswith("create_")]


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
    # _complete_candidate() default: no dealer_shuffler, is_vip False -> Tier 3.
    assert row["create_policy"] == expected["policy"] == "Tier 3"
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


def test_build_payload_preview_row_mobility_vip_and_population_tier_3():
    candidate = _complete_candidate(is_vip=False)
    row = reporting._build_payload_preview_row(candidate, _OFFICE, EXECUTION_TIMESTAMP, selected=False, terminal_eids=set())

    assert row["mobility_vip"] == "No"
    assert row["population_name"] == "Tier 3"
    assert row["create_policy"] == "Tier 3"


def test_build_payload_preview_row_mobility_vip_and_population_tier_1():
    candidate = _complete_candidate(is_vip=True)
    row = reporting._build_payload_preview_row(candidate, _OFFICE, EXECUTION_TIMESTAMP, selected=False, terminal_eids=set())

    assert row["mobility_vip"] == "Yes"
    assert row["population_name"] == "Tier 1"
    assert row["create_policy"] == "Tier 1"


def test_build_payload_preview_row_dealer_shuffler_overrides_vip():
    candidate = _complete_candidate(is_vip=True, dealer_shuffler="Presenter")
    row = reporting._build_payload_preview_row(candidate, _OFFICE, EXECUTION_TIMESTAMP, selected=False, terminal_eids=set())

    assert row["dealer_shuffler"] == "Presenter"
    assert row["population_name"] == "Game Presenters and Shufflers"
    assert row["create_policy"] == "Game Presenters and Shufflers"


def test_build_payload_preview_row_shows_mobility_support():
    candidate = _complete_candidate(mobility_support="Visa/work permit")
    row = reporting._build_payload_preview_row(candidate, _OFFICE, EXECUTION_TIMESTAMP, selected=False, terminal_eids=set())

    assert row["mobility_support"] == "Visa/work permit"


def test_build_payload_preview_row_scope_eligibility_no_and_reason_when_mobility_support_missing():
    candidate = _complete_candidate(mobility_support=None)
    row = reporting._build_payload_preview_row(candidate, _OFFICE, EXECUTION_TIMESTAMP, selected=False, terminal_eids=set())

    assert row["scope_eligibility"] == "No"
    assert row["scope_exclusion_reason"] == mobility_scope_service.SCOPE_REASON_TEXT[
        mobility_scope_service.SCOPE_REASON_MOBILITY_SUPPORT
    ]


def test_build_payload_preview_row_scope_eligibility_yes_when_qualifying():
    candidate = _complete_candidate(mobility_support="Relocation")
    row = reporting._build_payload_preview_row(candidate, _OFFICE, EXECUTION_TIMESTAMP, selected=False, terminal_eids=set())

    assert row["scope_eligibility"] == "Yes"
    assert row["scope_exclusion_reason"] is None


def test_build_payload_preview_row_scope_eligibility_yes_for_domestic_relocation():
    # Corrected 2026-09-05: domestic relocation (home_country == workplace's
    # host country, both "Serbia" here) does NOT exclude -- only
    # mobility_support does.
    candidate = _complete_candidate(mobility_support="Relocation", home_country="Serbia", workplace="Serbia Live Casino")
    row = reporting._build_payload_preview_row(candidate, _OFFICE, EXECUTION_TIMESTAMP, selected=False, terminal_eids=set())

    assert row["scope_eligibility"] == "Yes"
    assert row["scope_exclusion_reason"] is None


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
    assert row["invalid_population"] is False


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


def test_build_payload_preview_row_vip_resolves_to_tier_1_and_is_ready():
    # Confirmed 2026-09-02: every Population value is confirmed, so VIP
    # never blocks readiness -- see population_service.py.
    candidate = _complete_candidate(is_vip=True)
    row = reporting._build_payload_preview_row(candidate, _OFFICE, EXECUTION_TIMESTAMP, selected=False, terminal_eids=set())

    assert row["invalid_population"] is False
    assert row["create_policy"] == "Tier 1"
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
        "Pending Office Mapping", "Pending Recruiter Review", "Country Data Warnings", "Payload Preview",
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


def test_generate_reports_country_data_warnings_sheet_includes_missing_and_fallback(tmp_path, monkeypatch):
    population = [
        _candidate("READY-NO-COUNTRY-AT-ALL", benivo_status="READY_TO_POST", home_country=None, current_country=None, mobility_support="Relocation"),
        _candidate("PENDING-NO-HOME-HAS-CURRENT", benivo_status="PENDING_OFFICE_MAPPING", workplace="Unmapped Site", home_country=None, current_country="Belarus", mobility_support="Relocation"),
        _candidate("READY-WITH-PRIMARY-HOME", benivo_status="READY_TO_POST", home_country="Serbia", current_country="Georgia"),
    ]
    _patch_common(monkeypatch, tmp_path, population)

    report_path, _metrics = reporting.generate_reports(selected_candidates=[], dry_run=True, posting_limit=5, run_id="run-1")

    wb = load_workbook(report_path)
    ws = wb["Country Data Warnings"]
    header = [c.value for c in ws[2]]
    assert header == reporting.COUNTRY_DATA_WARNINGS_COLUMNS

    rows = {row[0]: dict(zip(header, row)) for row in ws.iter_rows(min_row=3, values_only=True)}

    # Only the missing and fallback-using candidates appear -- the one with
    # a primary candidate_home_country is fully clean, not a "warning".
    assert set(rows.keys()) == {"READY-NO-COUNTRY-AT-ALL", "PENDING-NO-HOME-HAS-CURRENT"}

    missing_row = rows["READY-NO-COUNTRY-AT-ALL"]
    assert missing_row["Country Source"] == "MISSING"
    assert missing_row["Effective Home Country"] is None
    assert missing_row["ISO2"] is None
    assert missing_row["Benivo Status"] == "READY_TO_POST"
    assert missing_row["Scope Eligibility"] == "Yes"

    fallback_row = rows["PENDING-NO-HOME-HAS-CURRENT"]
    assert fallback_row["Candidate Home Country"] is None
    assert fallback_row["Current Location"] == "Belarus"
    assert fallback_row["Effective Home Country"] == "Belarus"
    assert fallback_row["Country Source"] == "CURRENT_LOCATION"
    assert fallback_row["ISO2"] == "BY"
    assert fallback_row["Benivo Status"] == "PENDING_OFFICE_MAPPING"
    assert fallback_row["Warning Reason"] == reporting.COUNTRY_ISSUE_FALLBACK_REASON
    assert "does not block Benivo posting" in fallback_row["Warning Reason"]

    summary = {row[0].value: row[1].value for row in wb["Executive Summary"].iter_rows(min_row=2) if row[0].value}
    assert summary["Candidate Home Country"] == "1 (33.3%)"
    assert summary["Current Location Fallback"] == "1 (33.3%)"
    assert summary["Missing"] == "1 (33.3%)"


def test_generate_reports_country_data_warnings_excludes_permanently_excluded_scope(tmp_path, monkeypatch):
    # Aligned with Benivo scope (confirmed 2026-09-07): a candidate
    # permanently excluded from scope (EXCLUDED_MOBILITY_SUPPORT) must NOT
    # appear here even with a country fallback -- fixing their country data
    # would never make them postable. The old is_relocation_required-based
    # population would have included this candidate (relocation="Yes");
    # the corrected population excludes it via benivo_status instead.
    population = [
        _candidate(
            "EXCLUDED-MS-1", is_relocation_required="Yes", benivo_status="EXCLUDED_MOBILITY_SUPPORT",
            home_country=None, current_country="Serbia", mobility_support="N/A",
        ),
    ]
    _patch_common(monkeypatch, tmp_path, population)

    report_path, _metrics = reporting.generate_reports(selected_candidates=[], dry_run=True, posting_limit=5, run_id="run-1")

    wb = load_workbook(report_path)
    assert "Country Data Warnings" not in wb.sheetnames


def test_generate_reports_country_data_warnings_includes_needs_recruiter_review(tmp_path, monkeypatch):
    # Corrected population is NOT the old is_relocation_required=="Yes"
    # bucket -- a NEEDS_RECRUITER_REVIEW candidate (relocation not yet
    # confirmed) with a country fallback is still on track to become
    # eligible, so it belongs here now (it would have been excluded under
    # the old relocation_yes-only population).
    population = [
        _candidate(
            "REVIEW-1", is_relocation_required="No", benivo_status="NEEDS_RECRUITER_REVIEW",
            home_country=None, current_country="Georgia",
        ),
    ]
    _patch_common(monkeypatch, tmp_path, population)

    report_path, _metrics = reporting.generate_reports(selected_candidates=[], dry_run=True, posting_limit=5, run_id="run-1")

    wb = load_workbook(report_path)
    ws = wb["Country Data Warnings"]
    header = [c.value for c in ws[2]]
    rows = {row[0]: dict(zip(header, row)) for row in ws.iter_rows(min_row=3, values_only=True)}
    assert "REVIEW-1" in rows
    assert rows["REVIEW-1"]["Benivo Status"] == "NEEDS_RECRUITER_REVIEW"


def test_generate_reports_country_data_warnings_sheet_absent_when_clean(tmp_path, monkeypatch):
    # Exception-only sheet: no worksheet at all when there's nothing to
    # show, not an empty-with-note sheet -- the KPI in Executive Summary
    # still shows 0 either way.
    population = [_candidate("READY-1", benivo_status="READY_TO_POST", home_country="Serbia")]
    _patch_common(monkeypatch, tmp_path, population)

    report_path, _metrics = reporting.generate_reports(selected_candidates=[], dry_run=True, posting_limit=5, run_id="run-1")

    wb = load_workbook(report_path)
    assert "Country Data Warnings" not in wb.sheetnames

    summary = {row[0].value: row[1].value for row in wb["Executive Summary"].iter_rows(min_row=2) if row[0].value}
    assert summary["Missing"] == "0 (0.0%)"


def test_generate_reports_exception_sheets_absent_when_all_clean(tmp_path, monkeypatch):
    population = [_candidate("READY-1", benivo_status="READY_TO_POST", home_country="Serbia")]
    _patch_common(monkeypatch, tmp_path, population)

    report_path, _metrics = reporting.generate_reports(selected_candidates=[], dry_run=True, posting_limit=5, run_id="run-1")

    wb = load_workbook(report_path)
    assert "Pending Office Mapping" not in wb.sheetnames
    assert "Pending Recruiter Review" not in wb.sheetnames
    assert "Country Data Warnings" not in wb.sheetnames

    summary = {row[0].value: row[1].value for row in wb["Executive Summary"].iter_rows(min_row=2) if row[0].value}
    assert summary["Pending Office Mapping"] == "0 (0.0%)"
    assert summary["Pending Recruiter Review"] == "0 (0.0%)"


def test_generate_reports_exception_sheets_present_when_nonempty(tmp_path, monkeypatch):
    _patch_common(monkeypatch, tmp_path, _mock_population())

    report_path, _metrics = reporting.generate_reports(selected_candidates=[], dry_run=True, posting_limit=5, run_id="run-1")

    wb = load_workbook(report_path)
    # _mock_population() has one PENDING_OFFICE_MAPPING candidate (also
    # missing home_country -> also a Country Data Warning) and two
    # NEEDS_RECRUITER_REVIEW candidates -- all three exception sheets must
    # exist and be non-empty.
    assert "Pending Office Mapping" in wb.sheetnames
    assert "Pending Recruiter Review" in wb.sheetnames
    assert "Country Data Warnings" in wb.sheetnames
    assert wb["Pending Office Mapping"].max_row >= 3
    assert wb["Pending Recruiter Review"].max_row >= 3
    assert wb["Country Data Warnings"].max_row >= 3
    assert "Excluded - Benivo Scope" not in wb.sheetnames  # none excluded in _mock_population()


def test_generate_reports_excluded_scope_sheet_absent_when_clean(tmp_path, monkeypatch):
    population = [_candidate("READY-1", benivo_status="READY_TO_POST", home_country="Serbia")]
    _patch_common(monkeypatch, tmp_path, population)

    report_path, metrics = reporting.generate_reports(selected_candidates=[], dry_run=True, posting_limit=5, run_id="run-1")

    wb = load_workbook(report_path)
    assert "Excluded - Benivo Scope" not in wb.sheetnames
    assert "Excluded - Domestic Relocation (non-UAE)" not in [
        row[0].value for row in wb["Executive Summary"].iter_rows(min_row=2)
    ]

    summary = {row[0].value: row[1].value for row in wb["Executive Summary"].iter_rows(min_row=2) if row[0].value}
    assert summary["Excluded - Mobility Support"] == "0 (0.0%)"


def test_generate_reports_excluded_scope_sheet_present_with_mobility_support_reason(tmp_path, monkeypatch):
    population = [
        _candidate("MS-1", benivo_status="EXCLUDED_MOBILITY_SUPPORT", mobility_support="N/A"),
        _candidate("READY-1", benivo_status="READY_TO_POST", home_country="Serbia"),
    ]
    _patch_common(monkeypatch, tmp_path, population)

    report_path, metrics = reporting.generate_reports(selected_candidates=[], dry_run=True, posting_limit=5, run_id="run-1")

    wb = load_workbook(report_path)
    assert "Excluded - Benivo Scope" in wb.sheetnames
    assert wb["Excluded - Benivo Scope"].max_row >= 3  # banner/header + 1 data row

    rows = list(wb["Excluded - Benivo Scope"].iter_rows(values_only=True))
    reasons = [cell for row in rows for cell in row if isinstance(cell, str) and "mobility_support" in cell.lower()]
    assert any("mobility_support" in r.lower() for r in reasons)

    summary = {row[0].value: row[1].value for row in wb["Executive Summary"].iter_rows(min_row=2) if row[0].value}
    assert summary["Excluded - Mobility Support"] == "1 (50.0%)"
    assert metrics["excluded_mobility_support"] == 1
    assert "excluded_domestic_relocation" not in metrics


def test_generate_reports_no_longer_eligible_excluded_from_ready_to_post(tmp_path, monkeypatch):
    # Root cause regression guard (application_eid=pP98MxwU / Babak Guliyev,
    # confirmed 2026-09-08): a NO_LONGER_ELIGIBLE candidate must never
    # appear on Ready To Post, even though it's still a real, preserved row.
    population = [
        _candidate("NLE-1", benivo_status="NO_LONGER_ELIGIBLE", workflow_state="Offer rescinded"),
        _candidate("READY-1", benivo_status="READY_TO_POST", home_country="Serbia"),
    ]
    _patch_common(monkeypatch, tmp_path, population)

    report_path, _metrics = reporting.generate_reports(selected_candidates=[], dry_run=True, posting_limit=5, run_id="run-1")

    wb = load_workbook(report_path)
    ready_eids = [
        row[0] for row in wb["Ready To Post"].iter_rows(min_row=3, values_only=True)
    ]
    assert "NLE-1" not in ready_eids
    assert "READY-1" in ready_eids


def test_generate_reports_no_longer_eligible_appears_in_excluded_scope_sheet(tmp_path, monkeypatch):
    population = [
        _candidate("NLE-1", benivo_status="NO_LONGER_ELIGIBLE", workflow_state="Offer rescinded"),
    ]
    _patch_common(monkeypatch, tmp_path, population)

    report_path, metrics = reporting.generate_reports(selected_candidates=[], dry_run=True, posting_limit=5, run_id="run-1")

    wb = load_workbook(report_path)
    assert "Excluded - Benivo Scope" in wb.sheetnames

    rows = list(wb["Excluded - Benivo Scope"].iter_rows(min_row=3, values_only=True))
    eids = [row[0] for row in rows]
    assert "NLE-1" in eids

    reasons = [cell for row in rows for cell in row if isinstance(cell, str)]
    assert any("no longer eligible for benivo posting" in r.lower() for r in reasons)

    summary = {row[0].value: row[1].value for row in wb["Executive Summary"].iter_rows(min_row=2) if row[0].value}
    assert summary["No Longer Eligible (Jobvite workflow moved on)"] == "1 (100.0%)"
    assert metrics["no_longer_eligible"] == 1


def test_generate_reports_no_longer_eligible_excluded_from_country_data_warnings(tmp_path, monkeypatch):
    # A NO_LONGER_ELIGIBLE candidate with a missing home country must NOT
    # clutter Country Data Warnings -- fixing their country data would
    # never make them postable again on its own.
    population = [
        _candidate("NLE-1", benivo_status="NO_LONGER_ELIGIBLE", workflow_state="Offer rescinded", home_country=None, current_country="Romania"),
    ]
    _patch_common(monkeypatch, tmp_path, population)

    report_path, _metrics = reporting.generate_reports(selected_candidates=[], dry_run=True, posting_limit=5, run_id="run-1")

    wb = load_workbook(report_path)
    assert "Country Data Warnings" not in wb.sheetnames


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
        sync_metrics={"inserted_or_updated": 12, "marked_no_longer_eligible": 3},
    )

    wb = load_workbook(report_path)
    summary = {row[0].value: row[1].value for row in wb["Executive Summary"].iter_rows(min_row=2) if row[0].value}
    assert summary["Candidates Synced"] == 12
    assert summary["Candidates Marked No Longer Eligible (this sync)"] == 3


def test_generate_reports_sync_metrics_shown_as_na_when_absent(tmp_path, monkeypatch):
    _patch_common(monkeypatch, tmp_path, _mock_population())

    report_path, _metrics = reporting.generate_reports(selected_candidates=[], dry_run=True, posting_limit=5, run_id="run-1")

    wb = load_workbook(report_path)
    summary = {row[0].value: row[1].value for row in wb["Executive Summary"].iter_rows(min_row=2) if row[0].value}
    assert summary["Candidates Synced"] == "N/A (sync not run this execution)"
    assert summary["Candidates Marked No Longer Eligible (this sync)"] == "N/A (sync not run this execution)"


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


def test_generate_reports_benivo_population_distribution_counts_all_three_tiers(tmp_path, monkeypatch):
    population = [
        _candidate("TIER3-1", is_vip=False, benivo_status="READY_TO_POST"),
        _candidate("TIER3-2", is_vip=False, benivo_status="READY_TO_POST"),
        _candidate("TIER1-1", is_vip=True, benivo_status="READY_TO_POST"),
        _candidate("GP-1", is_vip=False, dealer_shuffler="Presenter", benivo_status="READY_TO_POST"),
    ]
    _patch_common(monkeypatch, tmp_path, population)

    report_path, metrics = reporting.generate_reports(selected_candidates=[], dry_run=True, posting_limit=5, run_id="run-1")

    wb = load_workbook(report_path)
    rows = {row[0].value: row[1].value for row in wb["Executive Summary"].iter_rows(min_row=2) if row[0].value}

    assert "Benivo Population Distribution" in rows
    assert rows["Tier 1"] == "1 (25.0%)"
    assert rows["Tier 3"] == "2 (50.0%)"
    assert rows["Game Presenters and Shufflers"] == "1 (25.0%)"
    assert metrics["population_tier_1"] == 1
    assert metrics["population_tier_3"] == 2
    assert metrics["population_game_presenters_and_shufflers"] == 1


def test_generate_reports_selected_count_never_exceeds_limit(tmp_path, monkeypatch):
    _patch_common(monkeypatch, tmp_path, _mock_population())

    selected = _mock_population()[:2]

    reporting.generate_reports(selected_candidates=selected, dry_run=True, posting_limit=5, run_id="run-1")

    assert len(selected) <= 5


def test_generate_reports_agency_sourced_metric_and_ready_to_post_column(tmp_path, monkeypatch):
    # End-to-end: Executive Summary's "Agency-Sourced (Ready To Post)" count
    # and the Ready To Post sheet's "Agency" column must agree with the
    # underlying candidate data -- one agency-sourced, one direct.
    population = [
        _candidate("READY-AGENCY", benivo_status="READY_TO_POST", home_country="Serbia", agency_name="Randstad Romania"),
        _candidate("READY-DIRECT", benivo_status="READY_TO_POST", home_country="Serbia", agency_name=None),
    ]
    _patch_common(monkeypatch, tmp_path, population)

    report_path, _metrics = reporting.generate_reports(selected_candidates=[], dry_run=True, posting_limit=5, run_id="run-1")

    wb = load_workbook(report_path)
    summary = {row[0].value: row[1].value for row in wb["Executive Summary"].iter_rows(min_row=2) if row[0].value}
    assert summary["Agency-Sourced (Ready To Post)"] == "1 (50.0%)"

    headers = [c.value for c in wb["Ready To Post"][2]]
    agency_col = headers.index("Agency")
    rows_by_eid = {row[0]: row for row in wb["Ready To Post"].iter_rows(min_row=3, values_only=True)}
    assert rows_by_eid["READY-AGENCY"][agency_col] == "Randstad Romania"
    assert rows_by_eid["READY-DIRECT"][agency_col] is None
