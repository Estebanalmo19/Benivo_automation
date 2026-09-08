"""Operational Excel report: an executive, daily-ops-facing snapshot of the current run.

Each generated workbook represents ONLY the current execution -- candidates
currently ready, what actually happened this run (pulled live from
benivo.post_log by run_id, never from an in-memory result list), and current
data-quality gaps. benivo.post_log is the durable historical source of
truth; this report is a point-in-time snapshot for people without direct
database access, not a history store. Anyone wanting trends across many runs
should query Postgres directly, not diff old Excel files.
"""

import logging
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from openpyxl import Workbook
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter
from openpyxl.worksheet.worksheet import Worksheet

from app import config
from app.clients import benivo_client
from app.models.domain import (
    ACTION_CREATE_USER,
    ACTION_UPDATE_CASE,
    EXCLUDED_MOBILITY_SUPPORT,
    MOBILITY_WORKFLOW_STATE,
    NO_LONGER_ELIGIBLE,
)
from app.repositories import candidate_repository, post_log_repository
from app.services import mobility_scope_service, posting_service
from app.services.country_code_service import resolve_iso2
from app.services.home_country_service import (
    SOURCE_CANDIDATE_HOME_COUNTRY,
    SOURCE_CURRENT_LOCATION,
    SOURCE_MISSING,
    resolve_effective_home_country,
)
from app.services.office_resolution_service import resolve_office
from app.services.population_service import resolve_population_values
from app.services.start_date_service import resolve_effective_start_date

logger = logging.getLogger(__name__)

REPORT_FILENAME_PREFIX = "benivo_operational_report"

# benivo_status values that mean a candidate will NEVER be posted to Benivo
# for a business reason (as opposed to PENDING_*/NEEDS_RECRUITER_REVIEW,
# which are still on track). Used to keep Country Data Warnings aligned
# with actual Benivo scope -- confirmed 2026-09-07, extended 2026-09-08 with
# NO_LONGER_ELIGIBLE (a source-eligibility exclusion, distinct from
# EXCLUDED_MOBILITY_SUPPORT's business-scope exclusion -- see domain.py). A
# future permanent exclusion status only needs to be added here, nowhere else.
PERMANENTLY_EXCLUDED_SCOPE_STATUSES = {EXCLUDED_MOBILITY_SUPPORT, NO_LONGER_ELIGIBLE}

# Go-live categories -- report-only labels computed on demand by
# _go_live_category(), never stored anywhere (benivo_status is completely
# independent of go-live timing; see docs/PHASE1_ARCHITECTURE.md's Go-Live
# section and candidate_repository.get_ready_candidates()).
GO_LIVE_ALREADY_POSTED = "Already Posted"
GO_LIVE_PRE_GO_LIVE_BACKLOG = "Pre-Go-Live Backlog"
GO_LIVE_AUTOMATICALLY_ELIGIBLE = "Automatically Eligible"
GO_LIVE_NEWLY_ELIGIBLE = "Newly Eligible"

# --- Arrise visual language -----------------------------------------------
# Presentation only -- these constants and every helper below them touch
# ONLY how the workbook looks (fills, fonts, borders, layout). No function
# in this section computes, filters, or selects data.
COLOR_PRIMARY = "35106A"
COLOR_PRIMARY_DARK = "2A0C55"
COLOR_PRIMARY_SOFT = "F4EEFC"
COLOR_INK = "1C0F38"
COLOR_BG = "F7F5FB"
COLOR_SURFACE = "FFFFFF"
COLOR_BORDER = "E5E1EE"
COLOR_TEXT_PRIMARY = "1A1424"
COLOR_TEXT_SECONDARY = "6B6475"
COLOR_SUCCESS = "1F9D55"
COLOR_SUCCESS_BG = "E8F7EE"
COLOR_WARNING = "B7791F"
COLOR_WARNING_BG = "FDF3DD"
COLOR_ERROR = "C0392B"
COLOR_ERROR_BG = "FBEAE8"
COLOR_INFO = "2F6FD6"
COLOR_INFO_BG = "E8F0FD"

_SEMANTIC_FILLS = {
    "success": (COLOR_SUCCESS_BG, COLOR_SUCCESS),
    "warning": (COLOR_WARNING_BG, COLOR_WARNING),
    "error": (COLOR_ERROR_BG, COLOR_ERROR),
    "info": (COLOR_INFO_BG, COLOR_INFO),
}

# Column-agnostic: these exact string VALUES mean the same thing regardless
# of which sheet/column they appear in (benivo_status, action status,
# go_live_category, start_date_source, country source, ...), so one table
# covers every sheet instead of a per-column special case each.
_SEMANTIC_STRING_VALUES = {
    "POSTED": "success",
    "SUCCESS": "success",
    GO_LIVE_AUTOMATICALLY_ELIGIBLE: "success",
    GO_LIVE_ALREADY_POSTED: "success",
    "ALREADY_EXISTS": "info",
    "CALCULATED": "info",
    "CURRENT_LOCATION": "info",
    GO_LIVE_NEWLY_ELIGIBLE: "info",
    "PENDING_OFFICE_MAPPING": "warning",
    "NEEDS_RECRUITER_REVIEW": "warning",
    "PENDING_MISSING_START_DATE": "warning",
    "PENDING": "warning",
    GO_LIVE_PRE_GO_LIVE_BACKLOG: "warning",
    "MISSING": "warning",
    "FAILED": "error",
    "POST_FAILED": "error",
}

# Boolean columns where the semantic meaning of True/False depends on the
# column itself (e.g. payload_ready=True is good, missing_job_title=True is
# a problem) -- handled separately from the value table above.
_POSITIVE_BOOL_COLUMNS = {"Payload Ready", "payload_ready", "ready_to_create_user", "ready_to_update_case"}
_NEGATIVE_BOOL_COLUMNS = {
    "missing_home_country",
    "missing_job_title",
    "missing_effective_start_date",
    "unresolved_office",
    "invalid_population",
}

# Columns whose values are free text and should wrap + left-align instead
# of being centered -- matched by substring so every current and future
# name/email/title/reason-shaped column is covered without an exhaustive list.
_WRAP_HINTS = ("reason", "email", "name", "title", "workplace", "location", "note", "message", "role")

_KEY_KPI_LABELS = {
    "Total Candidates",
    "Ready To Post",
    "Successfully Posted",
    "Failed",
    GO_LIVE_AUTOMATICALLY_ELIGIBLE,
    "Game Presenters and Shufflers",
}

_THIN_SIDE = Side(style="thin", color=COLOR_BORDER)
_THIN_BORDER = Border(left=_THIN_SIDE, right=_THIN_SIDE, top=_THIN_SIDE, bottom=_THIN_SIDE)

GO_LIVE_STATUS_COLUMNS = [
    "application_eid",
    "candidate_name",
    "workflow_state",
    "benivo_status",
    "first_seen_in_scope_at",
    "go_live_category",
    "job_title",
    "workplace",
]

# Used by _build_row() -- Pending Office Mapping and Pending Recruiter
# Review share this shape. Country Data Warnings has its own dedicated shape
# (see _build_country_data_warning_row()/COUNTRY_DATA_WARNINGS_COLUMNS).
REQUIRED_COLUMNS = [
    "application_eid",
    "candidate_eid",
    "candidate_name",
    "email",
    "workflow_state",
    "is_relocation_required",
    "start_date",
    "effective_start_date",
    "start_date_source",
    "workplace",
    "resolved_benivo_office_name",
    "resolved_benivo_office_id",
    "job_title",
    "department",
    "location",
    "benivo_status",
    "is_vip",
    "dealer_shuffler",
    "population_name",
    "mobility_support",
    "scope_eligibility",
    "reason",
    "selected_for_current_run",
]

# "Ready To Post" is the clean, executive-facing operational view -- exactly
# the columns an ops person needs to review what's about to be posted, no
# raw payload data. Full payload detail lives in "Payload Preview" instead.
READY_TO_POST_COLUMNS = [
    "Application EID",
    "Candidate Name",
    "Email",
    "Workplace",
    "Resolved Office",
    "OfficeId",
    "Host Country",
    "Candidate Home Country",
    "Current Country",
    "Country Sent to Benivo",
    "Country Source",
    "Job Title",
    "Dealer / Shuffler",
    "Mobility VIP",
    "Mobility Support",
    "Benivo Population",
    "Scope Eligibility",
    "Original Start Date",
    "Effective Start Date",
    "Start Date Source",
    "Payload Ready",
]

# "Payload Preview" is the technical/troubleshooting sheet: every field the
# real Benivo integration would use or send, plus data-quality flags. Every
# create_*/case_* column is pulled directly from posting_service.
# build_benivo_payload()/build_case_update_payload() -- the exact same
# functions the real posting path calls -- never rebuilt independently
# here, so it always reflects exactly what would actually be sent.
PAYLOAD_PREVIEW_COLUMNS = [
    # Identity
    "application_eid",
    "candidate_eid",
    "first_name",
    "last_name",
    "email",
    "phone_number",
    # Jobvite / business data
    "workflow_state",
    "is_relocation_required",
    "job_title",
    "department",
    "workplace",
    "location",
    "home_country",
    "current_country",
    "home_country_source",
    "home_city",
    # Start-date data
    "original_jobvite_start_date",
    "effective_start_date",
    "start_date_source",
    "execution_date",
    # Benivo resolution
    "resolved_office_name",
    "resolved_office_id",
    "resolved_host_country",
    "is_vip",
    "mobility_vip",
    "dealer_shuffler",
    "mobility_support",
    "population_name",
    "population_api_value",
    "scope_eligibility",
    "scope_exclusion_reason",
    # Create User payload preview (== posting_service.build_benivo_payload())
    "create_firstName",
    "create_lastName",
    "create_email",
    "create_homeCountry",
    "create_policy",
    "create_officeId",
    "create_officeName",
    "create_startDateOfAssignment",
    # Case PATCH payload preview (== posting_service.build_case_update_payload())
    "case_caseId_available",
    "case_hostJobRole",
    "case_home_country",
    "case_home_country_iso",
    # Posting state
    "benivo_status",
    "has_terminal_create_user_result",
    "selected_for_current_run",
    "ready_reason",
    # Data quality flags
    "missing_home_country",
    "missing_job_title",
    "missing_effective_start_date",
    "unresolved_office",
    "invalid_population",
    "ready_to_create_user",
    "ready_to_update_case",
    "payload_ready",
    "reason_not_payload_ready",
]

# "Posting Results" is built directly from benivo.post_log (see
# post_log_repository.get_post_log_rows_for_run()), never from an in-memory
# result list -- it can never diverge from what was actually persisted.
POSTING_RESULTS_COLUMNS = [
    "Candidate",
    "Application EID",
    "Create User Result",
    "Case Update Result",
    "Benivo User Id",
    "Assignment Id",
    "Execution Time",
]

# "Country Data Warnings" (formerly "Country Data Issues"/"Missing Home
# Country") -- broader than just missing: a candidate appears here if the
# country actually used is missing OR came from the fallback
# (current_country) rather than the primary source (candidate_home_country),
# so ops can immediately see who's relying on a fallback value. A data
# QUALITY warning, never a posting blocker -- see COUNTRY_ISSUE_FALLBACK_REASON.
# Population is now aligned with Benivo scope (see generate_reports()):
# candidates permanently excluded from scope (EXCLUDED_MOBILITY_SUPPORT)
# never appear here, since fixing their country data would not make them
# postable -- everyone still in scope or on track to be (READY_TO_POST,
# PENDING_OFFICE_MAPPING, PENDING_MISSING_START_DATE, NEEDS_RECRUITER_REVIEW,
# POSTED) can.
COUNTRY_DATA_WARNINGS_COLUMNS = [
    "Application EID",
    "Candidate",
    "Candidate Home Country",
    "Current Location",
    "Effective Home Country",
    "Country Source",
    "ISO2",
    "Warning Reason",
    "Scope Eligibility",
    "Benivo Status",
]

NOT_ATTEMPTED = "NOT_ATTEMPTED"

# Shown in case_caseId_available instead of NULL when a READY_TO_POST
# candidate has never been through create-user yet (the normal case --
# POSTED is terminal, so a candidate with a real caseId never appears in
# this sheet again).
CASE_ID_PENDING_LABEL = "PENDING_CREATE_USER"

# Fields required for payload_ready = TRUE. Deliberately wider than
# posting_service._validate_payload() (which only gates the real
# create-user call and does not require homeCountry/officeName/Case
# fields, since the Case PATCH is best-effort and never blocks or reverses
# create-user) -- this is a stricter, report-only "fully confirmed
# integration data" readiness signal, not the actual posting gate.
CREATE_USER_REQUIRED_FOR_READINESS = (
    "firstName", "lastName", "email", "homeCountry", "policy", "officeId", "officeName", "startDateOfAssignment",
)

READY_REASON = "Candidate meets all current posting requirements"
RELOCATION_NO_REASON = "Relocation is marked as No and requires recruiter confirmation"
RELOCATION_UNRECOGNIZED_REASON = "Relocation value is blank or unrecognized and requires recruiter confirmation"
MISSING_OFFICE_REASON = "No confirmed Benivo office mapping exists for the candidate workplace"
# See mobility_scope_service.SCOPE_REASON_TEXT -- the exact same reason
# strings, reused here rather than duplicated, so classify()'s decision and
# this report's explanation of it can never drift apart.
MOBILITY_SUPPORT_EXCLUDED_REASON = mobility_scope_service.SCOPE_REASON_TEXT[mobility_scope_service.SCOPE_REASON_MOBILITY_SUPPORT]
# Confirmed 2026-09-08: a candidate whose Jobvite workflow left "Mobility in
# process" (Offer rescinded/rejected, Hired, Candidate withdrew, or any
# other state) -- a SOURCE-eligibility exclusion, distinct from
# MOBILITY_SUPPORT_EXCLUDED_REASON above (a business-scope exclusion for a
# candidate who IS still in that workflow). The record itself is preserved,
# never deleted -- see synchronization_service.py.
NO_LONGER_ELIGIBLE_REASON = (
    "Candidate's Jobvite workflow status is no longer \"Mobility in process\" -- the record is preserved "
    "for traceability but is no longer eligible for Benivo posting."
)
COUNTRY_ISSUE_MISSING_REASON = (
    "Candidate Home Country and Current Location are both missing. No country is available. "
    "This does not block Benivo posting."
)
COUNTRY_ISSUE_FALLBACK_REASON = (
    "Candidate Home Country is missing. Current Location is being used as fallback. "
    "This does not block Benivo posting."
)
DRY_RUN_NOTE = "No real posting was performed (DRY RUN)."

# Sentinel: a (label, SECTION_HEADER) entry in the Executive Summary's row
# list renders as a bold section divider instead of a metric/value pair.
SECTION_HEADER = object()


def _report_dir() -> Path:
    configured = config.report_folder()
    report_dir = Path(configured) if configured else Path(__file__).resolve().parent.parent.parent
    report_dir.mkdir(parents=True, exist_ok=True)
    return report_dir


def _excel_safe(value: Any) -> Any:
    if isinstance(value, datetime) and value.tzinfo is not None:
        return value.astimezone(timezone.utc).replace(tzinfo=None)
    return value


def _candidate_name(candidate: Dict[str, Any]) -> Optional[str]:
    parts = [candidate.get("first_name"), candidate.get("last_name")]
    joined = " ".join(part for part in parts if part)
    return joined or None


def _relocation_bucket(is_relocation_required: Optional[str]) -> str:
    value = (is_relocation_required or "").strip().lower()

    if value == "yes":
        return "yes"

    if value == "no":
        return "no"

    return "blank_or_unrecognized"


def _count_with_pct(count: int, denominator: int) -> str:
    """'826 (98.3%)', or just the raw count when a percentage isn't meaningful (denominator is 0)."""
    if not denominator:
        return str(count)
    return f"{count} ({(count / denominator) * 100:.1f}%)"


def _pct(count: int, denominator: int) -> str:
    if not denominator:
        return "N/A"
    return f"{(count / denominator) * 100:.1f}%"


def _resolve_scope_display(candidate: Dict[str, Any]) -> Tuple[str, Optional[str]]:
    """
    ("Yes"/"No", reason text or None) -- computed independently via
    mobility_scope_service.resolve_scope() for EVERY candidate, regardless
    of which sheet/benivo_status they currently have. This is deliberate:
    e.g. a Pending Office Mapping candidate might ALSO fail the
    mobility_support scope rule, which is useful to surface even before
    their office gets mapped.
    """
    in_scope, reason_code = mobility_scope_service.resolve_scope(candidate.get("mobility_support"))
    reason_text = mobility_scope_service.SCOPE_REASON_TEXT.get(reason_code) if reason_code else None
    return ("Yes" if in_scope else "No"), reason_text


def _build_row(
    candidate: Dict[str, Any],
    reason: str,
    execution_timestamp: datetime,
    office: Optional[Dict[str, str]] = None,
    selected: bool = False,
) -> Dict[str, Any]:
    """Generic candidate-evidence row shared by Pending Office Mapping, Pending Recruiter Review, Missing Home Country."""
    effective_start_date, start_date_source = resolve_effective_start_date(candidate.get("start_date"), execution_timestamp)

    return {
        "application_eid": candidate.get("application_eid"),
        "candidate_eid": candidate.get("candidate_eid"),
        "candidate_name": _candidate_name(candidate),
        "email": candidate.get("email"),
        "workflow_state": candidate.get("workflow_state"),
        "is_relocation_required": candidate.get("is_relocation_required"),
        "start_date": _excel_safe(candidate.get("start_date")),
        "effective_start_date": _excel_safe(effective_start_date),
        "start_date_source": start_date_source,
        "workplace": candidate.get("workplace"),
        "resolved_benivo_office_name": office.get("officeName") if office else None,
        "resolved_benivo_office_id": office.get("officeId") if office else None,
        "job_title": candidate.get("job_title"),
        "department": candidate.get("department"),
        "location": candidate.get("location"),
        "benivo_status": candidate.get("benivo_status"),
        "is_vip": candidate.get("is_vip"),
        "dealer_shuffler": candidate.get("dealer_shuffler"),
        "population_name": resolve_population_values(candidate.get("dealer_shuffler"), candidate.get("is_vip"))[0],
        "mobility_support": candidate.get("mobility_support"),
        "scope_eligibility": _resolve_scope_display(candidate)[0],
        "reason": reason,
        "selected_for_current_run": "Yes" if selected else "No",
    }


def _build_country_data_warning_row(candidate: Dict[str, Any]) -> Dict[str, Any]:
    """
    "Country Data Warnings" row -- a candidate appears here (see
    generate_reports()'s population filter) only when the effective country
    is missing, or came from the Current Location fallback rather than the
    primary Candidate Home Country field, so ops can immediately see who's
    relying on a fallback value. A data-quality warning, never a posting
    blocker on its own -- country resolution/ISO2 logic is unchanged, this
    only surfaces which source was used.

    Population is aligned with Benivo scope (see generate_reports()):
    candidates permanently excluded from scope (EXCLUDED_MOBILITY_SUPPORT)
    are filtered out before this is ever called -- fixing their country data
    would not make them postable. Scope Eligibility/Benivo Status are shown
    anyway for full audit context, exactly as on the other sheets.
    """
    effective_home_country, home_country_source = resolve_effective_home_country(candidate)
    reason = COUNTRY_ISSUE_MISSING_REASON if home_country_source == SOURCE_MISSING else COUNTRY_ISSUE_FALLBACK_REASON

    return {
        "Application EID": candidate.get("application_eid"),
        "Candidate": _candidate_name(candidate),
        "Candidate Home Country": candidate.get("home_country"),
        "Current Location": candidate.get("current_country"),
        "Effective Home Country": effective_home_country,
        "Country Source": home_country_source,
        "ISO2": resolve_iso2(effective_home_country),
        "Warning Reason": reason,
        "Scope Eligibility": _resolve_scope_display(candidate)[0],
        "Benivo Status": candidate.get("benivo_status"),
    }


def _go_live_category(
    benivo_status: Optional[str],
    first_seen_in_scope_at: Optional[datetime],
    go_live_at: Optional[datetime],
) -> str:
    """
    Mutually exclusive go-live classification, entirely independent of
    benivo_status -- a report-only label, never written back to the
    database. Priority order:
      1. Already Posted: benivo_status == POSTED, regardless of timing --
         once posted, go-live timing no longer matters operationally.
      2. Pre-Go-Live Backlog: go_live_at isn't configured yet, OR this
         application_eid has no benivo.scope_history row at all, OR its
         first_seen_in_scope_at is before go_live_at. The "no row at all"
         case is a conservative default -- see
         candidate_repository.get_ready_candidates(), which applies the
         exact same "no proof it's new -> treat as backlog" rule so this
         report and actual posting behavior can never disagree.
      3. Automatically Eligible: first seen on/after go_live_at AND
         currently READY_TO_POST -- exactly the population
         get_ready_candidates() will actually pick up on the next run.
      4. Newly Eligible: first seen on/after go_live_at but not yet
         READY_TO_POST (still missing something -- office mapping, start
         date, relocation confirmation).
    """
    if benivo_status == "POSTED":
        return GO_LIVE_ALREADY_POSTED

    if go_live_at is None or first_seen_in_scope_at is None or first_seen_in_scope_at < go_live_at:
        return GO_LIVE_PRE_GO_LIVE_BACKLOG

    if benivo_status == "READY_TO_POST":
        return GO_LIVE_AUTOMATICALLY_ELIGIBLE

    return GO_LIVE_NEWLY_ELIGIBLE


def _build_go_live_status_row(
    candidate: Dict[str, Any],
    first_seen_in_scope_at: Optional[datetime],
    go_live_at: Optional[datetime],
) -> Dict[str, Any]:
    return {
        "application_eid": candidate.get("application_eid"),
        "candidate_name": _candidate_name(candidate),
        "workflow_state": candidate.get("workflow_state"),
        "benivo_status": candidate.get("benivo_status"),
        "first_seen_in_scope_at": _excel_safe(first_seen_in_scope_at),
        "go_live_category": _go_live_category(candidate.get("benivo_status"), first_seen_in_scope_at, go_live_at),
        "job_title": candidate.get("job_title"),
        "workplace": candidate.get("workplace"),
    }


def _build_ready_to_post_row(
    candidate: Dict[str, Any],
    office: Optional[Dict[str, str]],
    execution_timestamp: datetime,
    payload_ready: bool,
) -> Dict[str, Any]:
    """
    Clean, executive-facing "Ready To Post" row. payload_ready is passed in
    (computed once by _build_payload_preview_row()) rather than
    recalculated here, so the two sheets can never disagree about whether a
    given candidate is actually ready.
    """
    effective_start_date, start_date_source = resolve_effective_start_date(candidate.get("start_date"), execution_timestamp)
    _population_name, population_api_value = resolve_population_values(candidate.get("dealer_shuffler"), candidate.get("is_vip"))
    effective_home_country, home_country_source = resolve_effective_home_country(candidate)

    return {
        "Application EID": candidate.get("application_eid"),
        "Candidate Name": _candidate_name(candidate),
        "Email": candidate.get("email"),
        "Workplace": candidate.get("workplace"),
        "Resolved Office": office.get("officeName") if office else None,
        "OfficeId": office.get("officeId") if office else None,
        "Host Country": office.get("hostCountry") if office else None,
        "Candidate Home Country": candidate.get("home_country"),
        "Current Country": candidate.get("current_country"),
        "Country Sent to Benivo": effective_home_country,
        "Country Source": home_country_source,
        "Job Title": candidate.get("job_title"),
        # Jobvite-sourced (application.job.customField[fieldCode=
        # 'dealer__shuffler'] -- see candidate_repository.
        # DEALER_SHUFFLER_SUBQUERY), NOT Jobvite's own "Job Title" column
        # above -- this is what actually drives Game Presenter/Shuffler
        # Population classification. HiBob/hr_work_title is no longer used.
        "Dealer / Shuffler": candidate.get("dealer_shuffler"),
        # mobility_vip source field is synced directly into is_vip (see
        # synchronization_service.py) -- "Yes"/"No" here is exactly that
        # boolean formatted back to the raw Jobvite label, not a separate
        # stored value, so it can never drift from what is_vip holds. The
        # raw is_vip boolean itself is not repeated here (redundant) -- see
        # Payload Preview for the raw technical value.
        "Mobility VIP": "Yes" if candidate.get("is_vip") else "No",
        # Raw Jobvite multi-select value -- see mobility_scope_service.py
        # for how it's parsed. Every row on this sheet already qualifies
        # (READY_TO_POST implies it passed the scope gate), so this is shown
        # for audit context, not as a pass/fail flag.
        "Mobility Support": candidate.get("mobility_support"),
        # The literal Benivo API value that will actually be sent -- see
        # population_service.resolve_population_values(), the one
        # centralized mapping this and every other consumer reads.
        # Population and VIP Status are separate Benivo concepts; there is
        # no confirmed "VIP Status" column here -- see this workbook's
        # Instructions sheet.
        "Benivo Population": population_api_value,
        # Independently recomputed via mobility_scope_service.resolve_scope()
        # -- always "Yes" here (READY_TO_POST already implies it), shown for
        # a consistent column set with the exception sheets.
        "Scope Eligibility": _resolve_scope_display(candidate)[0],
        "Original Start Date": _excel_safe(candidate.get("start_date")),
        "Effective Start Date": _excel_safe(effective_start_date),
        "Start Date Source": start_date_source,
        "Payload Ready": payload_ready,
    }


def _build_payload_preview_row(
    candidate: Dict[str, Any],
    office: Optional[Dict[str, str]],
    execution_timestamp: datetime,
    selected: bool,
    terminal_eids: set,
) -> Dict[str, Any]:
    """
    "Payload Preview" row: every field the Benivo integration would
    actually use or send, plus data-quality flags. create_*/case_* preview
    columns come from posting_service.build_benivo_payload()/
    build_case_update_payload() -- the exact same functions the real
    posting path calls -- never rebuilt independently here.
    """
    effective_start_date, start_date_source = resolve_effective_start_date(candidate.get("start_date"), execution_timestamp)

    create_payload = posting_service.build_benivo_payload(candidate, office, effective_start_date)

    # caseId is only known once create-user has actually run (it's the
    # returned assignmentId, persisted as candidates.benivo_assignment_id).
    # A READY_TO_POST candidate has never been created yet -- labeled
    # PENDING_CREATE_USER rather than left as a bare NULL.
    case_id = candidate.get("benivo_assignment_id")
    case_payload = posting_service.build_case_update_payload(candidate, case_id)
    case_data = case_payload.get("data") or {}
    case_home_country_iso = (case_data.get("homeLocation") or {}).get("country")

    population_name, population_api_value = resolve_population_values(candidate.get("dealer_shuffler"), candidate.get("is_vip"))
    scope_eligibility, scope_exclusion_reason = _resolve_scope_display(candidate)
    # Same resolution build_case_update_payload() feeds into
    # country_code_service.resolve_iso2() -- shown here separately (pre-ISO
    # conversion) so ops can see the plain country name alongside the ISO
    # code actually sent, and tell "no country resolved at all" apart from
    # "country resolved but has no confirmed ISO mapping yet".
    effective_home_country, home_country_source = resolve_effective_home_country(candidate)

    unresolved_office = office is None
    invalid_population = population_api_value is None

    missing_create_fields = [field for field in CREATE_USER_REQUIRED_FOR_READINESS if not create_payload.get(field)]
    ready_to_create_user = not missing_create_fields

    missing_case_fields = []
    if not case_data.get("hostJobRole"):
        missing_case_fields.append("hostJobRole")
    if not case_home_country_iso:
        missing_case_fields.append("homeLocation.country")
    ready_to_update_case = not missing_case_fields

    payload_ready = ready_to_create_user and ready_to_update_case

    reason_parts = []
    if missing_create_fields:
        reason_parts.append("Create User missing: " + ", ".join(missing_create_fields))
    if missing_case_fields:
        reason_parts.append("Case Update missing: " + ", ".join(missing_case_fields))
    reason_not_payload_ready = "; ".join(reason_parts)

    return {
        "application_eid": candidate.get("application_eid"),
        "candidate_eid": candidate.get("candidate_eid"),
        "first_name": candidate.get("first_name"),
        "last_name": candidate.get("last_name"),
        "email": candidate.get("email"),
        "phone_number": candidate.get("phone_number"),
        "workflow_state": candidate.get("workflow_state"),
        "is_relocation_required": candidate.get("is_relocation_required"),
        "job_title": candidate.get("job_title"),
        "department": candidate.get("department"),
        "workplace": candidate.get("workplace"),
        "location": candidate.get("location"),
        "home_country": candidate.get("home_country"),
        "current_country": candidate.get("current_country"),
        "home_country_source": home_country_source,
        "home_city": candidate.get("home_city"),
        "original_jobvite_start_date": _excel_safe(candidate.get("start_date")),
        "effective_start_date": _excel_safe(effective_start_date),
        "start_date_source": start_date_source,
        "execution_date": _excel_safe(execution_timestamp),
        "resolved_office_name": office.get("officeName") if office else None,
        "resolved_office_id": office.get("officeId") if office else None,
        "resolved_host_country": office.get("hostCountry") if office else None,
        "is_vip": candidate.get("is_vip"),
        "mobility_vip": "Yes" if candidate.get("is_vip") else "No",
        "dealer_shuffler": candidate.get("dealer_shuffler"),
        "mobility_support": candidate.get("mobility_support"),
        "population_name": population_name,
        "population_api_value": population_api_value,
        "scope_eligibility": scope_eligibility,
        "scope_exclusion_reason": scope_exclusion_reason,
        "create_firstName": create_payload.get("firstName"),
        "create_lastName": create_payload.get("lastName"),
        "create_email": create_payload.get("email"),
        "create_homeCountry": create_payload.get("homeCountry"),
        "create_policy": create_payload.get("policy"),
        "create_officeId": create_payload.get("officeId"),
        "create_officeName": create_payload.get("officeName"),
        "create_startDateOfAssignment": create_payload.get("startDateOfAssignment"),
        "case_caseId_available": case_id if case_id is not None else CASE_ID_PENDING_LABEL,
        "case_hostJobRole": case_data.get("hostJobRole"),
        "case_home_country": effective_home_country,
        "case_home_country_iso": case_home_country_iso,
        "benivo_status": candidate.get("benivo_status"),
        "has_terminal_create_user_result": candidate.get("application_eid") in terminal_eids,
        "selected_for_current_run": "Yes" if selected else "No",
        "ready_reason": READY_REASON,
        # Reflects the EFFECTIVE value (post-fallback), not just the primary
        # candidate_home_country field -- home_country_source (above)
        # already distinguishes "used the primary" from "used the fallback".
        "missing_home_country": home_country_source == SOURCE_MISSING,
        "missing_job_title": not candidate.get("job_title"),
        "missing_effective_start_date": effective_start_date is None,
        "unresolved_office": unresolved_office,
        "invalid_population": invalid_population,
        "ready_to_create_user": ready_to_create_user,
        "ready_to_update_case": ready_to_update_case,
        "payload_ready": payload_ready,
        "reason_not_payload_ready": reason_not_payload_ready,
    }


def _group_post_log_rows_by_candidate(rows: List[Dict[str, Any]]) -> Dict[str, Dict[str, Dict[str, Any]]]:
    grouped: Dict[str, Dict[str, Dict[str, Any]]] = {}

    for row in rows:
        grouped.setdefault(row["application_eid"], {})[row["action"]] = row

    return grouped


def _build_posting_results_row(
    application_eid: str,
    actions: Dict[str, Dict[str, Any]],
    candidates_by_eid: Dict[str, Dict[str, Any]],
) -> Dict[str, Any]:
    create_row = actions.get(ACTION_CREATE_USER)
    case_row = actions.get(ACTION_UPDATE_CASE)
    candidate = candidates_by_eid.get(application_eid, {})
    execution_time = create_row["posted_at"] if create_row else (case_row["posted_at"] if case_row else None)

    return {
        "Candidate": _candidate_name(candidate) or application_eid,
        "Application EID": application_eid,
        "Create User Result": create_row["status"] if create_row else NOT_ATTEMPTED,
        "Case Update Result": case_row["status"] if case_row else NOT_ATTEMPTED,
        "Benivo User Id": create_row.get("benivo_user_id") if create_row else None,
        "Assignment Id": create_row.get("benivo_assignment_id") if create_row else None,
        "Execution Time": _excel_safe(execution_time),
    }


def _is_wrap_column(column_name: str) -> bool:
    lowered = str(column_name).lower()
    return any(hint in lowered for hint in _WRAP_HINTS)


def _semantic_class(column_name: str, value: Any) -> Optional[str]:
    """Value/column -> 'success'/'warning'/'error'/'info'/None. Presentation only -- never changes what's stored."""
    if isinstance(value, bool):
        if column_name in _POSITIVE_BOOL_COLUMNS:
            return "success" if value else "warning"
        if column_name in _NEGATIVE_BOOL_COLUMNS:
            return "warning" if value else None
        return None

    if isinstance(value, str):
        return _SEMANTIC_STRING_VALUES.get(value)

    return None


def _write_banner(ws: Worksheet, banner_text: str, num_columns: int) -> None:
    """Compact one-row title banner: report name, run timestamp, run mode -- consistent across every sheet."""
    ws.append([banner_text])
    row_idx = ws.max_row
    last_col = get_column_letter(max(num_columns, 1))

    if num_columns > 1:
        ws.merge_cells(f"A{row_idx}:{last_col}{row_idx}")

    cell = ws.cell(row=row_idx, column=1)
    cell.font = Font(bold=True, color="FFFFFF", size=12)
    cell.fill = PatternFill("solid", fgColor=COLOR_PRIMARY)
    cell.alignment = Alignment(horizontal="left", vertical="center", indent=1)
    ws.row_dimensions[row_idx].height = 22


def _style_header_row(ws: Worksheet, row_idx: int, num_columns: int) -> None:
    for col in range(1, num_columns + 1):
        cell = ws.cell(row=row_idx, column=col)
        cell.font = Font(bold=True, color="FFFFFF")
        cell.fill = PatternFill("solid", fgColor=COLOR_PRIMARY)
        cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
        cell.border = _THIN_BORDER
    ws.row_dimensions[row_idx].height = 32


def _style_data_rows(ws: Worksheet, columns: List[str], first_data_row: int, last_row: int) -> None:
    for r in range(first_data_row, last_row + 1):
        banded = (r - first_data_row) % 2 == 1

        for col_idx, column_name in enumerate(columns, start=1):
            cell = ws.cell(row=r, column=col_idx)
            cell.border = _THIN_BORDER

            if isinstance(cell.value, datetime):
                cell.number_format = "yyyy-mm-dd hh:mm"
            elif isinstance(cell.value, date):
                cell.number_format = "yyyy-mm-dd"

            wrap = _is_wrap_column(column_name)
            cell.alignment = Alignment(
                horizontal="left" if wrap else "center",
                vertical="center",
                wrap_text=wrap,
            )

            semantic = _semantic_class(column_name, cell.value)

            if semantic:
                bg, fg = _SEMANTIC_FILLS[semantic]
                cell.fill = PatternFill("solid", fgColor=bg)
                cell.font = Font(color=fg)
            else:
                cell.fill = PatternFill("solid", fgColor=COLOR_BG if banded else COLOR_SURFACE)
                cell.font = Font(color=COLOR_TEXT_PRIMARY)


def _autosize_columns(ws: Worksheet, columns: List[str]) -> None:
    for idx, column_name in enumerate(columns, start=1):
        column_letter = get_column_letter(idx)
        cells = ws[column_letter]
        max_length = max((len(str(c.value)) if c.value is not None else 0) for c in cells)
        cap = 42 if _is_wrap_column(column_name) else 60
        ws.column_dimensions[column_letter].width = max(min(max_length + 2, cap), 10)


def _apply_print_setup(ws: Worksheet, header_row_idx: int) -> None:
    ws.sheet_view.showGridLines = False
    ws.page_setup.orientation = "landscape"
    ws.page_setup.fitToWidth = 1
    ws.page_setup.fitToHeight = 0
    ws.sheet_properties.pageSetUpPr.fitToPage = True
    ws.print_title_rows = f"{header_row_idx}:{header_row_idx}"


def _write_table_sheet(
    ws: Worksheet,
    rows: List[Dict[str, Any]],
    empty_note: Optional[str] = None,
    columns: Optional[List[str]] = None,
    banner_text: Optional[str] = None,
) -> None:
    columns = columns or REQUIRED_COLUMNS

    if banner_text:
        _write_banner(ws, banner_text, len(columns))

    if not rows and empty_note:
        ws.append([empty_note])
        note_cell = ws.cell(row=ws.max_row, column=1)
        note_cell.font = Font(italic=True, color=COLOR_TEXT_SECONDARY)
        return

    ws.append(columns)
    header_row_idx = ws.max_row
    _style_header_row(ws, header_row_idx, len(columns))
    first_data_row = header_row_idx + 1

    for row in rows:
        ws.append([row.get(column) for column in columns])

    last_row = ws.max_row

    if last_row >= first_data_row:
        _style_data_rows(ws, columns, first_data_row, last_row)
        ws.auto_filter.ref = f"A{header_row_idx}:{get_column_letter(len(columns))}{last_row}"

    ws.freeze_panes = f"B{first_data_row}"
    _autosize_columns(ws, columns)
    _apply_print_setup(ws, header_row_idx)


def _write_summary_sheet(ws: Worksheet, rows: List[Any], banner_text: Optional[str] = None) -> None:
    """
    rows: ordered (label, value) pairs. A pair whose value is SECTION_HEADER
    renders as a bold section divider (blank spacer row + purple-filled
    label row) instead of a metric/value row.
    """
    if banner_text:
        _write_banner(ws, banner_text, 2)

    ws.append(["Metric", "Value"])
    header_row_idx = ws.max_row
    _style_header_row(ws, header_row_idx, 2)

    for label, value in rows:
        if value is SECTION_HEADER:
            ws.append([])
            ws.append([label])
            section_row = ws.max_row
            ws.merge_cells(f"A{section_row}:B{section_row}")
            cell = ws.cell(row=section_row, column=1)
            cell.font = Font(bold=True, color="FFFFFF", size=11)
            cell.fill = PatternFill("solid", fgColor=COLOR_PRIMARY_DARK)
            cell.alignment = Alignment(horizontal="left", vertical="center", indent=1)
            ws.row_dimensions[section_row].height = 20
            continue

        ws.append([label, value])
        row_idx = ws.max_row
        banded = row_idx % 2 == 0
        fill_color = COLOR_BG if banded else COLOR_SURFACE
        is_key_kpi = label in _KEY_KPI_LABELS

        label_cell = ws.cell(row=row_idx, column=1)
        label_cell.font = Font(color=COLOR_TEXT_PRIMARY, bold=is_key_kpi)
        label_cell.alignment = Alignment(horizontal="left", vertical="center")
        label_cell.fill = PatternFill("solid", fgColor=fill_color)
        label_cell.border = _THIN_BORDER

        value_cell = ws.cell(row=row_idx, column=2)
        value_cell.alignment = Alignment(horizontal="left", vertical="center")
        value_cell.fill = PatternFill("solid", fgColor=fill_color)
        value_cell.border = _THIN_BORDER
        value_cell.font = Font(bold=True, color=COLOR_PRIMARY, size=12) if is_key_kpi else Font(color=COLOR_TEXT_PRIMARY)

    ws.column_dimensions["A"].width = 48
    ws.column_dimensions["B"].width = 38
    ws.freeze_panes = f"A{header_row_idx + 1}"
    _apply_print_setup(ws, header_row_idx)


def _write_instructions_sheet(ws: Worksheet, banner_text: str) -> None:
    """First worksheet: short, practical, business-English guide to reading this report. No business logic here."""
    _write_banner(ws, banner_text, 2)

    ws.append(["Benivo Operational Report -- Instructions"])
    title_row = ws.max_row
    ws.merge_cells(f"A{title_row}:B{title_row}")
    title_cell = ws.cell(row=title_row, column=1)
    title_cell.font = Font(bold=True, size=14, color=COLOR_PRIMARY)
    ws.row_dimensions[title_row].height = 26
    ws.append([])

    sections = [
        (
            "Purpose",
            "This workbook is the operational snapshot of the Benivo relocation-posting automation for one run. "
            "It shows exactly which Jobvite candidates are ready, what would be (or was) sent to Benivo, and any "
            "data-quality gaps -- built from the automation's own database and audit log, not a manual export.",
        ),
        (
            "What the automation does",
            "Each run synchronizes candidates in the \"Mobility in process\" Jobvite workflow, classifies their "
            "readiness (relocation confirmed, start date resolved, office mapped), and -- outside of a dry run -- "
            "creates the candidate in Benivo, then immediately follows up with a Case update carrying Job Title "
            "and Home Country.",
        ),
        (
            "How to read each worksheet",
            "Executive Summary: run-level KPIs and distributions. Ready To Post: the clean, business view of "
            "everyone about to be posted. Payload Preview: the exact technical fields the integration would send, "
            "plus data-quality flags -- use this to troubleshoot a specific candidate. Posting Results: what "
            "actually happened this run, read from the permanent audit log. Pending Office Mapping / Pending "
            "Recruiter Review / Excluded - Benivo Scope / Country Data Warnings: exception lists needing "
            "attention (only appear when non-empty). Go-Live Status: where each candidate sits relative to the "
            "production go-live cutover.",
        ),
        (
            "Statuses",
            "READY_TO_POST: everything needed is confirmed, awaiting posting. POSTED: successfully created in "
            "Benivo. POST_FAILED: a posting attempt failed and will be retried automatically. "
            "PENDING_OFFICE_MAPPING: the workplace has no confirmed Benivo office yet. NEEDS_RECRUITER_REVIEW: "
            "relocation has not been confirmed \"Yes\". EXCLUDED_MOBILITY_SUPPORT: the candidate does not have a "
            "qualifying Mobility Support selection -- see \"Benivo Scope\" below. NO_LONGER_ELIGIBLE: the "
            "candidate's Jobvite status has moved on and they are no longer eligible for posting -- see "
            "\"Jobvite Workflow Eligibility\" below.",
        ),
        (
            "Benivo Scope",
            "A candidate is in scope for Benivo when their Mobility Support answer includes Relocation, "
            "Visa/work permit, or Accommodation -- any one of the three, or any combination, qualifies. "
            "\"N/A\" on its own, a blank answer, or a missing answer does NOT qualify, and the candidate is "
            "excluded (EXCLUDED_MOBILITY_SUPPORT). The \"Scope Eligibility\" column (Yes/No) shows this on every "
            "relevant sheet. The \"Excluded - Benivo Scope\" sheet (only present when non-empty) lists exactly "
            "who was excluded and why.",
        ),
        (
            "Jobvite Workflow Eligibility",
            "A candidate is only eligible for Benivo posting while their current Jobvite status is still "
            "\"Mobility in process\". If a candidate later moves to any other status -- Offer rescinded, Offer "
            "rejected, Hired, Candidate withdrew, or anything else -- they immediately stop being eligible and "
            "are marked NO_LONGER_ELIGIBLE, even if they were previously READY_TO_POST. Their record is always "
            "preserved for traceability, never deleted, and if a candidate genuinely returns to \"Mobility in "
            "process\" later, they re-enter the normal review process automatically. As an extra safeguard, the "
            "candidate's current Jobvite status is re-checked one final time immediately before any real posting "
            "attempt, so a candidate can never be posted based on outdated information -- see the \"Excluded - "
            "Benivo Scope\" sheet for exactly who is currently excluded this way and why.",
        ),
        (
            "Domestic relocation",
            "Domestic relocation (moving within the same country, e.g. within Serbia, Romania, or Bulgaria) is "
            "NOT a reason to exclude a candidate. A domestic candidate with a qualifying Mobility Support "
            "selection stays in scope exactly like an international one -- this includes domestic relocations "
            "within the UAE.",
        ),
        (
            "Benivo Population",
            "Population and VIP Status are separate Benivo concepts. \"Mobility VIP\" is shown for information "
            "only and is not sent to Benivo as a standalone value. \"Dealer / Shuffler\" is the candidate's role "
            "type as selected in Jobvite. The confirmed Population rule: any valid Dealer / Shuffler selection "
            "(e.g. Presenter, Dealer, Shuffler, Gameshow host, Prive Specialist Dealer, or any future addition to "
            "that list) -> \"Game Presenters and Shufflers\". Otherwise, if Mobility VIP is \"Yes\" -> \"Tier 1\". "
            "Otherwise -> \"Tier 3\".",
        ),
        (
            "Jobvite Start Date vs Calculated Start Date",
            "When Jobvite provides a start date, it is used as-is (source = JOBVITE). When it doesn't, the "
            "automation calculates a fallback date under the existing, unchanged business rule (source = "
            "CALCULATED) so a candidate is never blocked purely for a missing date.",
        ),
        (
            "Home Country",
            "The candidate's own declared Home Country is the primary source and is used whenever present. If "
            "it's blank, the automation falls back to their Current Location instead of leaving the field empty "
            "-- the \"Country Source\" column shows which one was actually used. This fallback is a data-quality "
            "note, not by itself a reason a candidate can't be posted.",
        ),
        (
            "Country Data Warnings",
            "This sheet lists candidates whose Home Country is missing and Current Location was used as a "
            "fallback (or, rarely, both are missing). These are data-quality warnings, not posting blockers -- a "
            "candidate can appear on both the Ready To Post sheet and the Country Data Warnings sheet at the "
            "same time. Only candidates who are still eligible, or on track to become eligible, for Benivo are "
            "listed here; a candidate who is permanently out of Benivo scope for an unrelated reason (e.g. "
            "EXCLUDED_MOBILITY_SUPPORT) is left off this sheet, since fixing their country data would not change "
            "their eligibility.",
        ),
        (
            "Go-Live categories",
            "Pre-Go-Live Backlog: existed in scope before the production cutover -- excluded from automatic "
            "posting even while still READY_TO_POST. Newly Eligible: entered scope after the cutover but isn't "
            "fully ready yet. Automatically Eligible: entered scope after the cutover AND is READY_TO_POST -- "
            "will be posted automatically. Already Posted: successfully posted, regardless of timing.",
        ),
    ]

    for heading, body in sections:
        ws.append([heading])
        h_row = ws.max_row
        ws.merge_cells(f"A{h_row}:B{h_row}")
        h_cell = ws.cell(row=h_row, column=1)
        h_cell.font = Font(bold=True, color="FFFFFF")
        h_cell.fill = PatternFill("solid", fgColor=COLOR_PRIMARY)
        h_cell.alignment = Alignment(vertical="center", indent=1)
        ws.row_dimensions[h_row].height = 18

        ws.append([body])
        b_row = ws.max_row
        ws.merge_cells(f"A{b_row}:B{b_row}")
        b_cell = ws.cell(row=b_row, column=1)
        b_cell.alignment = Alignment(wrap_text=True, vertical="top", horizontal="left", indent=1)
        b_cell.font = Font(color=COLOR_TEXT_PRIMARY)
        b_cell.fill = PatternFill("solid", fgColor=COLOR_PRIMARY_SOFT)
        ws.row_dimensions[b_row].height = 60
        ws.append([])

    ws.column_dimensions["A"].width = 70
    ws.column_dimensions["B"].width = 30
    ws.sheet_view.showGridLines = False
    ws.page_setup.orientation = "portrait"
    ws.page_setup.fitToWidth = 1
    ws.sheet_properties.pageSetUpPr.fitToPage = True


def _fetch_refdata_if_allowed() -> Optional[Dict[str, Any]]:
    if not posting_service.allow_reference_data_calls():
        return None

    try:
        token = benivo_client.get_access_token()
        return benivo_client.get_refdata(token)
    except Exception:
        logger.exception(
            "BENIVO_ALLOW_REFERENCE_DATA_CALLS is set but fetching refdata failed; "
            "office resolution will be treated as unresolved for this report."
        )
        return None


def generate_reports(
    selected_candidates: List[Dict[str, Any]],
    dry_run: bool,
    posting_limit: int,
    run_id: str,
    sync_metrics: Optional[Dict[str, Any]] = None,
) -> Tuple[Path, Dict[str, Any]]:
    """
    Build the operational Excel report from the current synchronized/classified
    data plus this run's selection. Must be called AFTER
    select_postable_candidates()/post_candidates() so selected_for_current_run
    reflects the actual current execution.

    Returns (report_path, report_metrics). report_metrics is a small dict of
    already-computed summary numbers (ready_to_post, posted, already_exists,
    failed, pending_recruiter_review, pending_office_mapping,
    country_fallback) -- this is the ONLY thing report_delivery_service.py
    is allowed to use for its Power Automate summary payload; it never
    recalculates business logic itself. This function performs NO delivery
    or other I/O beyond generating and saving the workbook -- see
    app/services/report_delivery_service.py and app/main.py's single
    orchestration boundary for that.

    run_id identifies this execution's rows in benivo.post_log -- the
    "Posting Results" sheet is queried directly from there (see
    post_log_repository.get_post_log_rows_for_run()), never from an
    in-memory result list, so it can never diverge from what was actually
    persisted. A dry run never writes post_log rows, so this naturally
    comes back empty for a dry-run report.

    sync_metrics is the dict returned by synchronization_service.
    sync_candidates() when sync ran as part of THIS execution (only
    "app.main run" does this) -- there is no persistent sync audit trail,
    so "Candidates Synced"/"Candidates Removed" can only be reported when
    sync actually happened in this same process. Omit it (cmd_post/
    cmd_report never call sync) and the Executive Summary shows an
    explicit "sync not run this execution" note instead of a fabricated 0.
    """
    # Generated once for the whole report and reused for every candidate/row
    # below -- never call datetime.now() per-candidate. See
    # start_date_service.resolve_effective_start_date().
    execution_timestamp = datetime.now(timezone.utc)

    all_candidates = candidate_repository.get_all_candidates_for_report()
    terminal_eids = post_log_repository.get_terminal_post_log_application_eids()
    scope_history_map = candidate_repository.get_scope_history_map()
    go_live_at = config.go_live_at()
    selected_eids = {c.get("application_eid") for c in selected_candidates}
    candidates_by_eid = {c.get("application_eid"): c for c in all_candidates}

    mobility_candidates = [c for c in all_candidates if c.get("workflow_state") == MOBILITY_WORKFLOW_STATE]

    # Go-Live Status: covers the full in-scope population (not just
    # READY_TO_POST), independent of benivo_status -- see
    # _go_live_category()'s docstring for the exact classification rules.
    go_live_status_rows = [
        _build_go_live_status_row(c, scope_history_map.get(c.get("application_eid")), go_live_at)
        for c in mobility_candidates
    ]
    go_live_already_posted_count = sum(1 for r in go_live_status_rows if r["go_live_category"] == GO_LIVE_ALREADY_POSTED)
    go_live_pre_backlog_count = sum(1 for r in go_live_status_rows if r["go_live_category"] == GO_LIVE_PRE_GO_LIVE_BACKLOG)
    go_live_auto_eligible_count = sum(
        1 for r in go_live_status_rows if r["go_live_category"] == GO_LIVE_AUTOMATICALLY_ELIGIBLE
    )
    go_live_newly_eligible_count = sum(1 for r in go_live_status_rows if r["go_live_category"] == GO_LIVE_NEWLY_ELIGIBLE)

    vip_candidates = sum(1 for c in all_candidates if c.get("is_vip") is True)

    relocation_yes, relocation_no, relocation_unrecognized = [], [], []
    for candidate in mobility_candidates:
        bucket = _relocation_bucket(candidate.get("is_relocation_required"))
        {"yes": relocation_yes, "no": relocation_no, "blank_or_unrecognized": relocation_unrecognized}[bucket].append(candidate)

    # Start-date provenance: still computed (feeds the Executive Summary
    # KPIs and the Start Date Source column on Ready To Post), but no
    # longer split into their own standalone sheets -- see the 2026-08-10
    # report redesign: this duplicated the Ready To Post population split
    # only by one column that already lives on every row there.
    jobvite_start_date_count = 0
    calculated_start_date_count = 0

    for candidate in relocation_yes:
        _effective_start_date, start_date_source = resolve_effective_start_date(candidate.get("start_date"), execution_timestamp)

        if start_date_source == "JOBVITE":
            jobvite_start_date_count += 1
        elif start_date_source == "CALCULATED":
            calculated_start_date_count += 1

    # Sheet placement reads the already-persisted benivo_status directly.
    # classification_service is the single place that decides READY_TO_POST
    # vs PENDING_OFFICE_MAPPING (via the same static WORKPLACE_TO_OFFICE_NAME
    # mapping) -- this report displays that decision, it does not re-derive it.
    ready_to_post_population = [c for c in mobility_candidates if c.get("benivo_status") == "READY_TO_POST"]
    pending_office_mapping_population = [c for c in mobility_candidates if c.get("benivo_status") == "PENDING_OFFICE_MAPPING"]
    excluded_mobility_support_population = [c for c in mobility_candidates if c.get("benivo_status") == EXCLUDED_MOBILITY_SUPPORT]
    # Sourced from all_candidates, NOT mobility_candidates: a NO_LONGER_ELIGIBLE
    # candidate's workflow_state is, by design, refreshed to whatever it
    # actually is now (Offer rescinded, Hired, ...) -- see
    # synchronization_service._MARK_OUT_OF_SCOPE_SQL -- so they no longer
    # satisfy workflow_state == MOBILITY_WORKFLOW_STATE and would be missed
    # entirely if sourced from mobility_candidates like the statuses above.
    no_longer_eligible_population = [c for c in all_candidates if c.get("benivo_status") == NO_LONGER_ELIGIBLE]

    refdata = _fetch_refdata_if_allowed()

    # officeName/officeId here are display enrichment only (live refdata
    # lookup), not a readiness decision -- readiness was already decided by
    # classification_service and is reflected in ready_to_post_population.
    ready_to_post_rows: List[Dict[str, Any]] = []
    payload_preview_rows: List[Dict[str, Any]] = []
    terminal_in_ready_population = 0

    for candidate in ready_to_post_population:
        application_eid = candidate.get("application_eid")

        if application_eid in terminal_eids:
            terminal_in_ready_population += 1
            continue

        selected = application_eid in selected_eids
        office = resolve_office(candidate, refdata) if refdata is not None else None

        preview_row = _build_payload_preview_row(candidate, office, execution_timestamp, selected=selected, terminal_eids=terminal_eids)
        payload_preview_rows.append(preview_row)

        ready_to_post_rows.append(
            _build_ready_to_post_row(candidate, office, execution_timestamp, payload_ready=preview_row["payload_ready"])
        )

    missing_office_rows = [
        _build_row(c, MISSING_OFFICE_REASON, execution_timestamp, selected=c.get("application_eid") in selected_eids)
        for c in pending_office_mapping_population
    ]

    relocation_review_rows = [
        _build_row(c, RELOCATION_NO_REASON, execution_timestamp, selected=c.get("application_eid") in selected_eids)
        for c in relocation_no
    ] + [
        _build_row(c, RELOCATION_UNRECOGNIZED_REASON, execution_timestamp, selected=c.get("application_eid") in selected_eids)
        for c in relocation_unrecognized
    ]

    # "Excluded - Benivo Scope": candidates excluded by one of the two
    # conceptually distinct, permanent exclusion reasons (see
    # PERMANENTLY_EXCLUDED_SCOPE_STATUSES) -- distinct from Pending Office
    # Mapping/Pending Recruiter Review, which are blocked on missing/
    # unconfirmed data rather than a deliberate exclusion:
    #   - EXCLUDED_MOBILITY_SUPPORT: still in Jobvite's Mobility workflow,
    #     but no qualifying mobility_support selection (mobility_scope_service.py).
    #   - NO_LONGER_ELIGIBLE: the Jobvite workflow itself has moved on
    #     (synchronization_service.py) -- confirmed 2026-09-08.
    # Domestic/local relocation is NOT a scope exclusion (corrected
    # 2026-09-05) -- a domestic candidate with a qualifying mobility_support
    # selection never appears here for that reason.
    excluded_scope_rows = [
        _build_row(c, MOBILITY_SUPPORT_EXCLUDED_REASON, execution_timestamp, selected=c.get("application_eid") in selected_eids)
        for c in excluded_mobility_support_population
    ] + [
        _build_row(c, NO_LONGER_ELIGIBLE_REASON, execution_timestamp, selected=c.get("application_eid") in selected_eids)
        for c in no_longer_eligible_population
    ]

    # Country Source Distribution (Executive Summary KPI only) -- unchanged
    # scope (relocation_yes), unchanged country resolution/ISO2 logic.
    country_source_counts = {SOURCE_CANDIDATE_HOME_COUNTRY: 0, SOURCE_CURRENT_LOCATION: 0, SOURCE_MISSING: 0}

    for candidate in relocation_yes:
        _effective_home_country, home_country_source = resolve_effective_home_country(candidate)
        country_source_counts[home_country_source] += 1

    # Country Data Warnings: population aligned with Benivo scope (confirmed
    # 2026-09-07) -- every Mobility-in-process candidate EXCEPT those
    # permanently excluded from Benivo scope (PERMANENTLY_EXCLUDED_SCOPE_STATUSES,
    # currently just EXCLUDED_MOBILITY_SUPPORT), never the old
    # is_relocation_required-based relocation_yes bucket. A NEEDS_RECRUITER_REVIEW
    # candidate with a country fallback is now visible here (they may still
    # confirm relocation and proceed); a candidate permanently excluded from
    # scope for an unrelated reason no longer clutters this list, since
    # fixing their country data would never make them postable. Country
    # resolution/ISO2 logic itself is unchanged -- only which candidates are
    # shown changed.
    country_data_warning_candidates = []

    for candidate in mobility_candidates:
        if candidate.get("benivo_status") in PERMANENTLY_EXCLUDED_SCOPE_STATUSES:
            continue

        _effective_home_country, home_country_source = resolve_effective_home_country(candidate)

        if home_country_source != SOURCE_CANDIDATE_HOME_COUNTRY:
            country_data_warning_candidates.append(candidate)

    country_data_warning_rows = [_build_country_data_warning_row(c) for c in country_data_warning_candidates]

    # Benivo Population distribution -- same population scope as Country
    # Source Distribution above (relocation_yes, not just READY_TO_POST) so
    # both "distribution" sections in the Executive Summary answer the same
    # question: "of everyone we could potentially post, how do they break
    # down". Resolution goes through population_service.resolve_population_values()
    # -- the one centralized place -- never re-derived here. Replaces the
    # retired Mobility VIP / Policy Tier (Tier 1/Tier 2, is_vip-only)
    # distribution -- see population_service.py for why that mapping was
    # conceptually wrong (it treated Population as if it were Policy).
    population_tier_1_count = sum(
        1 for c in relocation_yes if resolve_population_values(c.get("dealer_shuffler"), c.get("is_vip"))[1] == "Tier 1"
    )
    population_tier_3_count = sum(
        1 for c in relocation_yes if resolve_population_values(c.get("dealer_shuffler"), c.get("is_vip"))[1] == "Tier 3"
    )
    population_game_presenters_and_shufflers_count = sum(
        1
        for c in relocation_yes
        if resolve_population_values(c.get("dealer_shuffler"), c.get("is_vip"))[1] == "Game Presenters and Shufflers"
    )

    # Posting Results: queried directly from benivo.post_log by run_id --
    # never from an in-memory result list. Empty for a dry run (nothing was
    # ever written under this run_id).
    post_log_rows = post_log_repository.get_post_log_rows_for_run(run_id)
    grouped_post_log = _group_post_log_rows_by_candidate(post_log_rows)
    posting_results_rows = [
        _build_posting_results_row(application_eid, actions, candidates_by_eid)
        for application_eid, actions in grouped_post_log.items()
    ]

    create_user_rows = [row for row in post_log_rows if row["action"] == ACTION_CREATE_USER]
    posting_success = sum(1 for row in create_user_rows if row["status"] == "SUCCESS")
    posting_already_exists = sum(1 for row in create_user_rows if row["status"] == "ALREADY_EXISTS")
    posting_failed = sum(1 for row in create_user_rows if row["status"] == "FAILED")
    posting_attempted = len(create_user_rows)

    terminal_already_processed = sum(1 for c in all_candidates if c.get("application_eid") in terminal_eids)

    # Data-quality rollup over the Payload Preview population -- mirrors the
    # per-row flags in _build_payload_preview_row() so these counts are
    # visible in the Executive Summary without opening the detail sheet.
    ready_payload_ready = sum(1 for r in payload_preview_rows if r["payload_ready"])
    ready_missing_home_country = sum(1 for r in payload_preview_rows if r["missing_home_country"])
    ready_missing_job_title = sum(1 for r in payload_preview_rows if r["missing_job_title"])
    ready_unresolved_office = sum(1 for r in payload_preview_rows if r["unresolved_office"])
    ready_to_create_user_count = sum(1 for r in payload_preview_rows if r["ready_to_create_user"])
    ready_to_update_case_count = sum(1 for r in payload_preview_rows if r["ready_to_update_case"])

    total_candidates = len(all_candidates)
    ready_to_post_count = len(ready_to_post_rows)
    pending_office_mapping_count = len(pending_office_mapping_population)
    pending_recruiter_review_count = len(relocation_no) + len(relocation_unrecognized)
    excluded_mobility_support_count = len(excluded_mobility_support_population)
    no_longer_eligible_count = len(no_longer_eligible_population)

    if sync_metrics is None:
        candidates_synced_display: Any = "N/A (sync not run this execution)"
        candidates_marked_no_longer_eligible_display: Any = "N/A (sync not run this execution)"
    else:
        candidates_synced_display = sync_metrics.get("inserted_or_updated", "N/A")
        # "marked_no_longer_eligible" replaces the old sync_metrics "removed"
        # key -- confirmed 2026-09-08: synchronization_service.py no longer
        # deletes out-of-scope candidates, it transitions them in place (see
        # synchronization_service._MARK_OUT_OF_SCOPE_SQL), so nothing is
        # "removed" from benivo.candidates anymore.
        candidates_marked_no_longer_eligible_display = sync_metrics.get("marked_no_longer_eligible", "N/A")

    summary_rows: List[Any] = [
        ("Run Information", SECTION_HEADER),
        ("Run Timestamp", _excel_safe(execution_timestamp)),
        ("Run Mode", "DRY RUN" if dry_run else "REAL"),
        ("Processing Summary", SECTION_HEADER),
        ("Total Candidates", total_candidates),
        ("Candidates Synced", candidates_synced_display),
        ("Candidates Marked No Longer Eligible (this sync)", candidates_marked_no_longer_eligible_display),
        ("Ready To Post", _count_with_pct(ready_to_post_count, total_candidates)),
        ("Successfully Posted", _count_with_pct(posting_success, posting_attempted)),
        ("Already Exists", _count_with_pct(posting_already_exists, posting_attempted)),
        ("Failed", _count_with_pct(posting_failed, posting_attempted)),
        ("Pending Recruiter Review", _count_with_pct(pending_recruiter_review_count, total_candidates)),
        ("Pending Office Mapping", _count_with_pct(pending_office_mapping_count, total_candidates)),
        ("Excluded - Mobility Support", _count_with_pct(excluded_mobility_support_count, total_candidates)),
        ("No Longer Eligible (Jobvite workflow moved on)", _count_with_pct(no_longer_eligible_count, total_candidates)),
        ("Country Source Distribution", SECTION_HEADER),
        ("Candidate Home Country", _count_with_pct(country_source_counts[SOURCE_CANDIDATE_HOME_COUNTRY], len(relocation_yes))),
        ("Current Location Fallback", _count_with_pct(country_source_counts[SOURCE_CURRENT_LOCATION], len(relocation_yes))),
        ("Missing", _count_with_pct(country_source_counts[SOURCE_MISSING], len(relocation_yes))),
        ("Start Date Distribution", SECTION_HEADER),
        ("Jobvite Start Date", _count_with_pct(jobvite_start_date_count, len(relocation_yes))),
        ("Calculated Start Date", _count_with_pct(calculated_start_date_count, len(relocation_yes))),
        ("Benivo Population Distribution", SECTION_HEADER),
        ("Tier 1", _count_with_pct(population_tier_1_count, len(relocation_yes))),
        ("Tier 3", _count_with_pct(population_tier_3_count, len(relocation_yes))),
        ("Game Presenters and Shufflers", _count_with_pct(population_game_presenters_and_shufflers_count, len(relocation_yes))),
        ("Data Quality", SECTION_HEADER),
        ("Office Mapping Completeness", _pct(ready_to_post_count, ready_to_post_count + pending_office_mapping_count)),
        ("Home Country Completeness (Effective, Ready To Post)", _pct(ready_to_post_count - ready_missing_home_country, ready_to_post_count)),
        ("Job Title Completeness (Ready To Post)", _pct(ready_to_post_count - ready_missing_job_title, ready_to_post_count)),
        ("Ready To Create User (Ready To Post)", _pct(ready_to_create_user_count, ready_to_post_count)),
        ("Ready To Update Case (Ready To Post)", _pct(ready_to_update_case_count, ready_to_post_count)),
        ("Fully Payload Ready (Ready To Post)", _pct(ready_payload_ready, ready_to_post_count)),
        ("Go-Live Readiness", SECTION_HEADER),
        ("Go-Live Cutover", _excel_safe(go_live_at) if go_live_at else "Not configured (no filtering applied yet)"),
        (GO_LIVE_PRE_GO_LIVE_BACKLOG, _count_with_pct(go_live_pre_backlog_count, len(mobility_candidates))),
        (GO_LIVE_NEWLY_ELIGIBLE, _count_with_pct(go_live_newly_eligible_count, len(mobility_candidates))),
        (GO_LIVE_AUTOMATICALLY_ELIGIBLE, _count_with_pct(go_live_auto_eligible_count, len(mobility_candidates))),
        (GO_LIVE_ALREADY_POSTED, _count_with_pct(go_live_already_posted_count, len(mobility_candidates))),
    ]

    wb = Workbook()

    # One consistent banner across every sheet -- report name, run
    # timestamp, run mode. Presentation only; computed once here so every
    # sheet shows the exact same values.
    banner_text = (
        f"Benivo Operational Report   |   Run: {execution_timestamp.strftime('%Y-%m-%d %H:%M UTC')}   |   "
        f"Mode: {'DRY RUN' if dry_run else 'REAL'}"
    )

    instructions_ws = wb.active
    instructions_ws.title = "Instructions"
    _write_instructions_sheet(instructions_ws, banner_text)

    summary_ws = wb.create_sheet("Executive Summary")
    _write_summary_sheet(summary_ws, summary_rows, banner_text=banner_text)

    _write_table_sheet(
        wb.create_sheet("Ready To Post"), ready_to_post_rows, columns=READY_TO_POST_COLUMNS, banner_text=banner_text
    )
    _write_table_sheet(
        wb.create_sheet("Posting Results"),
        posting_results_rows,
        empty_note=DRY_RUN_NOTE if dry_run else "No posting attempts were recorded in this run.",
        columns=POSTING_RESULTS_COLUMNS,
        banner_text=banner_text,
    )
    # Exception-only sheets: a worksheet is created only when it has rows --
    # a clean state (e.g. Pending Office Mapping = 0) is still fully visible
    # as a KPI = 0 in the Executive Summary, it just doesn't get an empty
    # worksheet cluttering the workbook. Ready To Post, Posting Results, and
    # Payload Preview are core sheets and are always created.
    if missing_office_rows:
        _write_table_sheet(wb.create_sheet("Pending Office Mapping"), missing_office_rows, banner_text=banner_text)

    if relocation_review_rows:
        _write_table_sheet(wb.create_sheet("Pending Recruiter Review"), relocation_review_rows, banner_text=banner_text)

    if excluded_scope_rows:
        _write_table_sheet(wb.create_sheet("Excluded - Benivo Scope"), excluded_scope_rows, banner_text=banner_text)

    if country_data_warning_rows:
        _write_table_sheet(
            wb.create_sheet("Country Data Warnings"),
            country_data_warning_rows,
            columns=COUNTRY_DATA_WARNINGS_COLUMNS,
            banner_text=banner_text,
        )

    _write_table_sheet(
        wb.create_sheet("Payload Preview"), payload_preview_rows, columns=PAYLOAD_PREVIEW_COLUMNS, banner_text=banner_text
    )
    _write_table_sheet(
        wb.create_sheet("Go-Live Status"), go_live_status_rows, columns=GO_LIVE_STATUS_COLUMNS, banner_text=banner_text
    )

    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    report_path = _report_dir() / f"{REPORT_FILENAME_PREFIX}_{timestamp}.xlsx"

    wb.save(report_path)

    logger.info(
        "Report generated: %s (ready_to_post=%d, pending_office_mapping=%d, pending_recruiter_review=%d, "
        "excluded_mobility_support=%d, no_longer_eligible=%d, country_data_warnings=%d, "
        "posting_results=%d, terminal_already_processed=%d, terminal_in_ready_population=%d).",
        report_path,
        ready_to_post_count,
        pending_office_mapping_count,
        pending_recruiter_review_count,
        excluded_mobility_support_count,
        no_longer_eligible_count,
        len(country_data_warning_rows),
        len(posting_results_rows),
        terminal_already_processed,
        terminal_in_ready_population,
    )

    report_metrics = {
        "ready_to_post": ready_to_post_count,
        "posted": posting_success,
        "already_exists": posting_already_exists,
        "failed": posting_failed,
        "pending_recruiter_review": pending_recruiter_review_count,
        "pending_office_mapping": pending_office_mapping_count,
        "excluded_mobility_support": excluded_mobility_support_count,
        "no_longer_eligible": no_longer_eligible_count,
        "country_fallback": country_source_counts[SOURCE_CURRENT_LOCATION],
        "go_live_pre_backlog": go_live_pre_backlog_count,
        "go_live_newly_eligible": go_live_newly_eligible_count,
        "go_live_automatically_eligible": go_live_auto_eligible_count,
        "go_live_already_posted": go_live_already_posted_count,
        "population_tier_1": population_tier_1_count,
        "population_tier_3": population_tier_3_count,
        "population_game_presenters_and_shufflers": population_game_presenters_and_shufflers_count,
    }

    return report_path, report_metrics
