-- Add start-date resolution audit columns to benivo.post_log, supporting
-- the temporary "calculated third-month" start-date business rule
-- (approved by the business owner, 2026-08-06) -- see
-- app/services/start_date_service.py for the rule itself.
--
-- Every posting attempt now records: the single execution_timestamp that
-- run used (execution_date), the start date actually sent to Benivo
-- (effective_start_date), and whether it came directly from Jobvite or was
-- calculated (start_date_source: 'JOBVITE' | 'CALCULATED'). start_date_source
-- records the ORIGIN of the date, not the specific offset rule that produced
-- it -- the offset (currently 3 months, see
-- start_date_service.START_DATE_OFFSET_MONTHS) is separate, changeable
-- configuration, so this audit trail stays meaningful even after the rule
-- changes.
--
-- Non-destructive: ADD COLUMN IF NOT EXISTS is idempotent. All three are
-- nullable -- historical rows predating this rule simply have NULL here.

ALTER TABLE benivo.post_log
    ADD COLUMN IF NOT EXISTS execution_date TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS effective_start_date DATE,
    ADD COLUMN IF NOT EXISTS start_date_source TEXT;
