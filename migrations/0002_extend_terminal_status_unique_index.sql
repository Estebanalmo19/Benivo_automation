-- Widen benivo.post_log's terminal-status idempotency index from
-- SUCCESS-only to SUCCESS + ALREADY_EXISTS, matching the confirmed rule:
-- terminal = SUCCESS, ALREADY_EXISTS; retryable = FAILED.
--
-- Pre-flight check performed live on 2026-07-29 before writing this file:
--   SELECT COUNT(*) FROM benivo.post_log;                      -> 0 rows
--   SELECT indexname, indexdef FROM pg_indexes
--     WHERE schemaname='benivo' AND tablename='post_log';
--   -> post_log_pkey                          UNIQUE btree (id)
--   -> ux_benivo_post_success_application      UNIQUE btree (application_eid)
--                                               WHERE status = 'SUCCESS'
--
-- benivo.post_log has zero rows, so there is no possibility of a duplicate
-- (application_eid, terminal-status) pair already existing -- this change
-- is non-destructive. If this is ever re-run against a database that DOES
-- have post_log rows, check first:
--
--   SELECT application_eid, COUNT(*)
--   FROM benivo.post_log
--   WHERE status IN ('SUCCESS', 'ALREADY_EXISTS')
--   GROUP BY application_eid
--   HAVING COUNT(*) > 1;
--
-- -- must return zero rows before this migration is safe to apply.

DROP INDEX IF EXISTS benivo.ux_benivo_post_success_application;

CREATE UNIQUE INDEX IF NOT EXISTS ux_benivo_post_terminal_application
    ON benivo.post_log (application_eid)
    WHERE status IN ('SUCCESS', 'ALREADY_EXISTS');
