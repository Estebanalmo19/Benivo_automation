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
# Benivo scope exclusion -- confirmed with Mobility 2026-09-04, corrected
# 2026-09-05 to drop a domestic-relocation exclusion this status originally
# had a sibling for (see app/services/mobility_scope_service.py, the one
# centralized place this rule lives). Evaluated only for candidates who
# already passed the is_relocation_required=Yes gate above.
EXCLUDED_MOBILITY_SUPPORT = "EXCLUDED_MOBILITY_SUPPORT"
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
