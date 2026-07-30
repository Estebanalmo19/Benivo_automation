-- Add Benivo response-tracking columns to benivo.candidates and benivo.post_log.
--
-- Revised after live schema inspection on 2026-07-29 (see delivery notes).
-- The original version of this file assumed several columns were missing;
-- live inspection showed they already exist:
--   benivo.candidates.benivo_user_id   (character varying) -- already exists
--   benivo.candidates.benivo_response  (jsonb)              -- already exists
--   benivo.post_log.request_payload    (jsonb)              -- already exists
--   benivo.post_log.response_payload   (jsonb)              -- already exists
--   benivo.post_log.error_message      (text)               -- already exists
--   benivo.post_log.benivo_user_id     (character varying)  -- already exists
--
-- Only the columns below are genuinely missing. IF NOT EXISTS is kept
-- regardless, so this remains safe to re-run.
--
-- benivo_assignment_id is BIGINT to match how create_user_benivo.py already
-- treats it: int(created["assignmentId"]).
--
-- benivo_profile_url format is still not confirmed against a real Benivo
-- API response -- TEXT is a safe, format-agnostic placeholder.

ALTER TABLE benivo.candidates
    ADD COLUMN IF NOT EXISTS benivo_assignment_id BIGINT,
    ADD COLUMN IF NOT EXISTS benivo_profile_url TEXT;

ALTER TABLE benivo.post_log
    ADD COLUMN IF NOT EXISTS benivo_assignment_id BIGINT,
    ADD COLUMN IF NOT EXISTS benivo_profile_url TEXT;
