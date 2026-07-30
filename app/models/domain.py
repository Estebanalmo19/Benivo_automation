"""Shared status constants -- the single source of truth for candidate/post_log status strings.

Centralized here (rather than duplicated across classification_service and
posting_service, or cross-imported between them) so every module agrees on
the exact same set of values. Plain string constants, not an enum class:
these are stored/compared as raw strings in SQL, so an enum would just add
an unwrap step everywhere for no practical benefit.
"""

# --- benivo.candidates.benivo_status -----------------------------------------
PENDING = "PENDING"
READY_TO_POST = "READY_TO_POST"
PENDING_MISSING_START_DATE = "PENDING_MISSING_START_DATE"
PENDING_OFFICE_MAPPING = "PENDING_OFFICE_MAPPING"
NEEDS_RECRUITER_REVIEW = "NEEDS_RECRUITER_REVIEW"
POSTED = "POSTED"
POST_FAILED = "POST_FAILED"

# The only terminal candidate status: classification never re-evaluates a
# POSTED candidate. POST_FAILED is deliberately NOT terminal -- it's
# freely reclassified (and retryable) every run.
TERMINAL_CANDIDATE_STATUSES = {POSTED}

# --- benivo.post_log.status ---------------------------------------------------
SUCCESS = "SUCCESS"
ALREADY_EXISTS = "ALREADY_EXISTS"
FAILED = "FAILED"

# SUCCESS and ALREADY_EXISTS block a candidate from ever being selected
# again; FAILED remains retryable.
TERMINAL_POST_LOG_STATUSES = (SUCCESS, ALREADY_EXISTS)

POST_LOG_STATUS_TO_CANDIDATE_STATUS = {
    SUCCESS: POSTED,
    ALREADY_EXISTS: POSTED,
    FAILED: POST_FAILED,
}

ACTION_CREATE_USER = "CREATE_USER"

# --- Policy ------------------------------------------------------------------
POLICY_BASIC = "Basic"
POLICY_VIP = "VIP"
