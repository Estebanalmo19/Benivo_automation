-- Separate internal business policy (policy_name: Basic/VIP) from the exact
-- string Benivo's API expects (policy_api_value: e.g. "Tier 1"). Root cause
-- of the first real UAT attempt's FAILED result: the payload sent
-- policy="Basic", which Benivo rejected ("Policy is misspelled") -- Benivo's
-- refdata "policies" endpoint only recognizes "Tier 1"/"Tier 2"/"Tier 3"/
-- "Game Presenters and Shufflers"; "Basic"/"VIP" are business labels only
-- and were never valid API values.
--
-- Audit column only, nullable, non-destructive: ADD COLUMN IF NOT EXISTS.

ALTER TABLE benivo.post_log
    ADD COLUMN IF NOT EXISTS policy_api_value TEXT;
