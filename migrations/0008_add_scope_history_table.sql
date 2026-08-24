-- benivo.scope_history: durable, append-only record of the first time an
-- application_eid was ever detected inside the Benivo posting scope
-- (workflow_state = 'Mobility in process' AND is_relocation_required IN
-- ('Yes','No') -- the exact same predicate synchronization_service.py's
-- _UPSERT_SQL/_DELETE_OUT_OF_SCOPE_SQL already use).
--
-- Why this can't just be a column on benivo.candidates:
-- synchronization_service._DELETE_OUT_OF_SCOPE_SQL permanently DELETES a
-- candidate's row from benivo.candidates the moment they leave scope
-- (workflow_state moves on, relocation field changes/disappears, or the
-- Jobvite row itself disappears). A first_seen_at column on that row would
-- be lost the instant the candidate leaves and re-enters Mobility later,
-- incorrectly making a pre-existing (pre-go-live) candidate look brand
-- new. This table is never touched by that delete path, and
-- synchronization_service.py only ever inserts into it with
-- ON CONFLICT (application_eid) DO NOTHING, so first_seen_in_scope_at is
-- set exactly once, ever, per application_eid -- durable across any number
-- of scope exits/re-entries.
--
-- Used by:
--   - candidate_repository.get_ready_candidates(): gates automatic
--     posting on go-live (app.config.go_live_at()) without ever touching
--     benivo_status -- a pre-go-live backlog candidate stays visibly
--     READY_TO_POST everywhere, just excluded from auto-selection.
--   - reporting_service.py: computes the report-only "go-live category"
--     (Pre-Go-Live Backlog / Newly Eligible / Already Posted /
--     Automatically Eligible) shown on the "Go-Live Status" sheet.
--
-- IMPORTANT -- deployment order:
--   1. Apply this migration.
--   2. Run the one-time baseline backfill: scripts/backfill_scope_history.py
--      (see that script and docs/PHASE1_ARCHITECTURE.md's Go-Live section
--      for exactly why this must happen before step 3).
--   3. Only then deploy the code that reads/writes this table
--      (synchronization_service.py, candidate_repository.py,
--      reporting_service.py) and, separately, set BENIVO_GO_LIVE_AT to
--      actually activate the go-live gate.
-- Deploying the code before this migration is applied will make
-- sync_candidates() fail on every run (INSERT into a table that doesn't
-- exist yet) -- this migration must land first.
--
-- Deliberately NO foreign key to benivo.candidates(application_eid):
-- that's the entire point of this table. A FK with ON DELETE CASCADE
-- would erase first_seen_in_scope_at the moment
-- _DELETE_OUT_OF_SCOPE_SQL removes the candidates row -- exactly the
-- data loss this table exists to prevent. ON DELETE RESTRICT would be
-- worse: it would make that DELETE fail outright and break sync_candidates()
-- entirely for any application_eid ever recorded here. No FK is the
-- correct, intentional choice, not an oversight.
--
-- Reviewed 2026-08-24 (no schema change from the original version):
-- added an explicit immutability guard. Application code (synchronization_
-- service.py, scripts/backfill_scope_history.py) already only ever writes
-- via INSERT ... ON CONFLICT (application_eid) DO NOTHING, so
-- first_seen_in_scope_at is never supposed to change once set -- but
-- nothing previously stopped a future UPDATE statement (manual fix,
-- careless migration, admin tooling) from silently violating that. This
-- trigger makes the invariant a hard DB-level guarantee instead of a
-- convention every future caller has to remember.

CREATE TABLE IF NOT EXISTS benivo.scope_history (
    application_eid TEXT PRIMARY KEY,
    candidate_eid TEXT,
    first_seen_in_scope_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS ix_benivo_scope_history_first_seen
    ON benivo.scope_history (first_seen_in_scope_at);

CREATE OR REPLACE FUNCTION benivo.reject_scope_history_first_seen_update()
RETURNS TRIGGER AS $$
BEGIN
    IF NEW.first_seen_in_scope_at IS DISTINCT FROM OLD.first_seen_in_scope_at THEN
        RAISE EXCEPTION
            'benivo.scope_history.first_seen_in_scope_at is immutable once set (application_eid=%). '
            'Go-live gating and reporting depend on this value never changing after first insert.',
            OLD.application_eid;
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_scope_history_first_seen_immutable ON benivo.scope_history;

CREATE TRIGGER trg_scope_history_first_seen_immutable
    BEFORE UPDATE ON benivo.scope_history
    FOR EACH ROW
    EXECUTE FUNCTION benivo.reject_scope_history_first_seen_update();
