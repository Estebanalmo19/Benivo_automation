-- Add VIP/policy tracking columns.
--
-- Business rule (confirmed): Basic is the default Benivo policy. VIP is an
-- explicit, business-owned attribute with no confirmed Jobvite source yet
-- (see sync_candidates.py comments) -- it defaults to FALSE for every
-- existing and new candidate until a confirmed source mapping exists.
--
-- Non-destructive: ADD COLUMN IF NOT EXISTS is idempotent. Adding a NOT NULL
-- boolean column with DEFAULT FALSE backfills every existing row to FALSE
-- as part of the same statement -- no separate UPDATE needed, no row left
-- NULL.

ALTER TABLE benivo.candidates
    ADD COLUMN IF NOT EXISTS is_vip BOOLEAN NOT NULL DEFAULT FALSE;

-- post_log is a historical record: each row captures the policy actually
-- used for that specific attempt, so is_vip is nullable here (no default)
-- rather than defaulting to FALSE -- a row with no policy decision made yet
-- should not silently read as "Basic".
ALTER TABLE benivo.post_log
    ADD COLUMN IF NOT EXISTS is_vip BOOLEAN,
    ADD COLUMN IF NOT EXISTS policy_name TEXT;
