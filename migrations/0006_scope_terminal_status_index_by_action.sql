-- Scope the terminal-status idempotency index by action, not just
-- application_eid, so a Case-update audit row (action='UPDATE_CASE') can
-- coexist with the create-user audit row (action='CREATE_USER') for the
-- same application_eid without violating uniqueness.
--
-- Confirmed 2026-08-10 by Gina (Benivo): assignmentId returned by
-- create-user IS the caseId required by PATCH /clients/v1/Case, so every
-- successful create-user now immediately triggers a follow-up Case PATCH,
-- recorded as its own post_log row (see app/services/posting_service.py
-- build_case_update_post_log_insert()/record_post_result()). Without this
-- migration, a candidate whose create-user succeeds (CREATE_USER/SUCCESS)
-- and whose Case PATCH also succeeds (UPDATE_CASE/SUCCESS) would violate
-- the previous index (migrations/0002), which only allowed ONE
-- SUCCESS/ALREADY_EXISTS row per application_eid regardless of action.
--
-- Postability logic (get_terminal_post_log_application_eids(),
-- get_ready_candidates()) is updated in the same change to filter
-- action='CREATE_USER' explicitly, so this widening is purely additive --
-- it does not change which candidates are considered already-posted.
--
-- Pre-flight check before applying to a database with existing rows:
--   SELECT application_eid, action, COUNT(*)
--   FROM benivo.post_log
--   WHERE status IN ('SUCCESS', 'ALREADY_EXISTS')
--   GROUP BY application_eid, action
--   HAVING COUNT(*) > 1;
-- -- must return zero rows before this migration is safe to apply.

DROP INDEX IF EXISTS benivo.ux_benivo_post_terminal_application;

CREATE UNIQUE INDEX IF NOT EXISTS ux_benivo_post_terminal_application_action
    ON benivo.post_log (application_eid, action)
    WHERE status IN ('SUCCESS', 'ALREADY_EXISTS');
