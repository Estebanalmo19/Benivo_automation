"""Shared status constants -- the single source of truth for candidate/post_log status strings.

Centralized here (rather than duplicated across classification_service and
posting_service, or cross-imported between them) so every module agrees on
the exact same set of values. Plain string constants, not an enum class:
these are stored/compared as raw strings in SQL, so an enum would just add
an unwrap step everywhere for no practical benefit.
"""

# The one authoritative Jobvite workflow_state value that puts a candidate
# in Benivo's scope at all -- confirmed source of truth for the entire
# pipeline (synchronization_service.py, classification_service.py,
# candidate_repository.py's final posting safety gate, reporting_service.py).
# Centralized here 2026-09-08 -- previously duplicated as three independent
# literals (synchronization_service.WORKFLOW_STATE, reporting_service.
# MOBILITY_WORKFLOW_STATE, and an implicit assumption in classify()).
MOBILITY_WORKFLOW_STATE = "Mobility in process"

# --- benivo.candidates.benivo_status -----------------------------------------
PENDING = "PENDING"
READY_TO_POST = "READY_TO_POST"
PENDING_MISSING_START_DATE = "PENDING_MISSING_START_DATE"
PENDING_OFFICE_MAPPING = "PENDING_OFFICE_MAPPING"
NEEDS_RECRUITER_REVIEW = "NEEDS_RECRUITER_REVIEW"
# Benivo scope exclusion -- confirmed with Mobility 2026-09-04, corrected
# 2026-09-05 to drop a domestic-relocation exclusion this status originally
# had a sibling for (see app/services/mobility_scope_service.py, the one
# centralized place this rule lives). Evaluated only for candidates who
# already passed the is_relocation_required=Yes gate above. A BUSINESS-scope
# exclusion: the candidate is still in Jobvite's "Mobility in process"
# workflow, just doesn't have a qualifying mobility_support selection.
EXCLUDED_MOBILITY_SUPPORT = "EXCLUDED_MOBILITY_SUPPORT"
# A SOURCE-eligibility exclusion -- deliberately a different concept from
# EXCLUDED_MOBILITY_SUPPORT above, confirmed 2026-09-08: the candidate's
# authoritative Jobvite workflow_state has moved away from
# MOBILITY_WORKFLOW_STATE entirely (Offer rescinded, Offer rejected, Hired,
# Candidate withdrew, or any other state) -- Benivo eligibility requires
# CURRENTLY being in that workflow, not merely having been there once. See
# synchronization_service.py (sets this instead of deleting the row) and
# classification_service.classify() (re-affirms it independently every run
# from the row's own, kept-fresh workflow_state). Never collapsed into
# EXCLUDED_MOBILITY_SUPPORT -- they answer different questions ("is this
# candidate still in the pipeline at all" vs "does this in-pipeline
# candidate want Benivo's support").
NO_LONGER_ELIGIBLE = "NO_LONGER_ELIGIBLE"
POSTED = "POSTED"
POST_FAILED = "POST_FAILED"

# The only terminal candidate status: classification never re-evaluates a
# POSTED candidate. POST_FAILED is deliberately NOT terminal -- it's
# freely reclassified (and retryable) every run. NO_LONGER_ELIGIBLE is ALSO
# deliberately NOT terminal (see classify()'s docstring): a candidate who
# legitimately returns to MOBILITY_WORKFLOW_STATE must be able to be
# reclassified normally, not stuck here forever.
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

# Case update follow-up call (PATCH /clients/v1/Case), made immediately
# after a successful create-user. Recorded as its own post_log action/row
# -- see migrations/0006 -- so it never collides with the create-user row's
# terminal-status idempotency index, and a PATCH failure never overwrites
# the create-user outcome.
ACTION_UPDATE_CASE = "UPDATE_CASE"

# --- Benivo Population ---------------------------------------------------------
# Confirmed with Mobility 2026-09-02, replacing the retired is_vip-only
# Basic/VIP -> Tier 1/Tier 2 "policy" scheme (see app/services/
# population_service.py, which is the one centralized place this business
# rule lives). These three strings are simultaneously the business label AND
# the exact Benivo API value -- refdata['policies'] returns exactly these
# plus nothing else (see migrations/0004's comment).
POPULATION_GAME_PRESENTERS_AND_SHUFFLERS = "Game Presenters and Shufflers"
POPULATION_TIER_1 = "Tier 1"
POPULATION_TIER_3 = "Tier 3"
