# Benivo Automation - Phase 1 Architecture

## Objective

Automate the creation of relocation candidates in Benivo using candidate data stored in PostgreSQL.

Phase 1 does NOT include the Serbia Excel file.

---

## High-Level Flow

Jobvite
    ↓
Jobvite ETL
    ↓
PostgreSQL
    ↓
benivo.candidates
    ↓
Benivo Automation
    ↓
Benivo API
    ↓
benivo.post_log
    ↓
Execution Report (Excel)

---

## PostgreSQL Tables

### benivo.candidates

Stores all relocation candidates eligible for Benivo.

Main fields:

- application_eid
- candidate_eid
- first_name
- last_name
- email
- phone_number
- workplace
- population
- host_country
- host_city
- start_date
- host_legal_entity
- host_business_unit
- is_relocation_required
- benivo_status

### benivo.post_log

Stores every execution against Benivo.

Includes:

- run_id
- application_eid
- status
- benivo_user_id
- response_code
- error_message
- processed_at

---

## Candidate Selection Rules

A candidate is eligible only if:

- benivo_status = READY_TO_POST
- is_relocation_required = Yes
- No previous SUCCESS exists in benivo.post_log

---

## Benivo Mapping

Jobvite → Benivo

- first_name → firstName
- last_name → lastName
- email → email
- start_date → startDateOfAssignment
- VIP custom field → policy
- workplace / host_country / host_city → officeId
- home_country → homeCountry (create-user, confirmed 2026-08-10)
- job_title → hostJobRole (Case PATCH only, confirmed 2026-08-10)
- home_country → homeLocation.country (Case PATCH only, confirmed 2026-08-10)

Policy rules:

- VIP → "VIP"
- Null / empty / No → "Basic"

OfficeId will be resolved using Benivo refdata.

---

## Synchronization

Candidate data must remain current.

The synchronization process updates benivo.candidates every 3 hours.

Preferred implementation:

cron
    ↓
Jobvite Sync
    ↓
PostgreSQL

---

## Reports

Each execution generates:

- Summary
- Success
- Failed
- Skipped

---

## Host Country (office-derived) & Case API status

Confirmed 2026-08-06 by official email from Benivo (full office catalogue:
display name + PublicId):

- `hostCountry` is derived **exclusively** from the resolved Benivo office
  (`office_resolution_service.OFFICE_NAME_TO_HOST_COUNTRY`, keyed on the
  confirmed officeName catalogue) -- never inferred from the candidate's own
  address/location fields.
- `officeId` (as already sent in the `create-user` payload) is the confirmed
  Benivo **PublicId**, not a guessed or internal-only identifier.

Confirmed 2026-08-10 by official email from Gina (Benivo) -- Case API
integration is **implemented**:

- `assignmentId` returned by `create-user` **is** the `caseId` required by
  `PATCH https://externalapi.uat.benivo.com/clients/v1/Case`.
- Job Title (`candidates.job_title`, synced from Jobvite) maps to
  `hostJobRole` on the Case PATCH payload.
- `create-user` now populates `homeCountry` from `candidates.home_country`
  (already synced from Jobvite's `candidate_home_country` custom field).
- The Case PATCH populates `homeLocation.country`, also from
  `candidates.home_country`.
- Only these confirmed fields are sent on the Case PATCH (`caseId`,
  `hostJobRole`, `homeLocation.country`) -- no other Case field has been
  confirmed, so none is guessed.
- Confirmed 2026-08-19 by Gina after a real UAT PATCH returned error 999:
  the endpoint requires a `findBy`/`data` envelope, not a flat body --
  `{"findBy": {"caseId": ...}, "data": {"hostJobRole": ..., "homeLocation":
  {"country": ...}}}`. A flat `{"caseId": ..., "hostJobRole": ..., ...}`
  body is what caused the 999.
- Confirmed 2026-08-21 by a real Benivo UAT PATCH: `homeLocation.country`
  must be a 2-character ISO 3166-1 alpha-2 code (e.g. `"RS"`), not the full
  country name -- a full name causes error 4422. See
  `app/services/country_code_service.py` (`resolve_iso2()`), used ONLY for
  this field. `create-user`'s `homeCountry` has no evidence of the same
  requirement and is deliberately left sending the full name as-is.
- The Case PATCH is called immediately after a **successful** create-user
  (see `posting_service.post_single_candidate()`), never for
  `already_exists` or `failed` outcomes.
- The Case PATCH is best-effort and independently audited: a failure is
  recorded as its own `benivo.post_log` row (`action = 'UPDATE_CASE'`,
  see `migrations/0006`) and never changes the candidate's `POSTED` status
  set by a successful create-user.

---

## Benivo API endpoint configuration (UAT vs Production)

Confirmed 2026-08-21: every Benivo HTTP endpoint is environment-driven --
`app/clients/benivo_client.py` never hardcodes a URL, it reads
`config.BENIVO_TOKEN_URL` / `BENIVO_REFDATA_URL` / `BENIVO_CREATE_USER_URL` /
`BENIVO_USER_LOOKUP_URL` / `BENIVO_CASE_URL` exclusively. These five are
`REQUIRED_SETTINGS` in `app/config.py`, so `config.validate()` fails fast
and clearly at startup (used by `app/main.py` and every `scripts/*.py`
entrypoint) if any is missing -- see `.env.example` for the current
confirmed UAT values.

**Switching the whole app from UAT to Production is meant to be ONLY a
`.env` change** (these five URLs plus `BENIVO_CLIENT_ID`/`BENIVO_CLIENT_SECRET`)
-- no Python source code should need to change.

Production endpoint values are **not yet confirmed** as of 2026-08-21. Do
not guess them -- get them confirmed by Benivo (their GitBook, once
available, or a direct confirmation email the same way the UAT endpoints
were confirmed) before ever setting them in a production `.env`.

---

## Go-Live design: excluding the pre-existing backlog from automatic posting

Business requirement (2026-08-21): when Production goes live, the existing
`READY_TO_POST` backlog must **not** be automatically posted. Only
candidates the automation detects entering the Benivo posting scope
**after** go-live should be auto-posted. "New" is defined strictly as
"first time this automation ever saw the application inside scope"
(`workflow_state = 'Mobility in process'` + a recognized
`is_relocation_required` value) -- explicitly **not** the Jobvite
application/candidate creation date, since candidates can apply months
before entering Mobility. The design must also survive a candidate leaving
Mobility and re-entering later.

**Why not a column on `benivo.candidates`:** originally, `synchronization_
service._DELETE_OUT_OF_SCOPE_SQL` permanently deleted a candidate's row the
moment they left scope. A `first_seen_at` column on that row would have
been lost the instant the candidate left and re-entered, incorrectly making
a pre-existing candidate look brand new. **Corrected 2026-09-08:**
out-of-scope candidates are no longer deleted at all -- `synchronization_
service._MARK_OUT_OF_SCOPE_SQL` now transitions the row to
`NO_LONGER_ELIGIBLE` in place instead (see that module and
`app/models/domain.py`). `benivo.scope_history` remains necessary anyway:
it is the only reliable `first_seen_in_scope_at` record for any candidate
whose row WAS deleted-and-reinserted under the old (pre-2026-09-08)
behavior, and it stays immune to any future regression of this kind by
design (see migrations/0008's trigger).

**Design: a separate, append-only `benivo.scope_history` table**
(`migrations/0008_add_scope_history_table.sql`, not yet applied):

```sql
CREATE TABLE benivo.scope_history (
    application_eid TEXT PRIMARY KEY,
    candidate_eid TEXT,
    first_seen_in_scope_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
```

- `synchronization_service.sync_candidates()` inserts into this table with
  `ON CONFLICT (application_eid) DO NOTHING`, using the exact same scope
  predicate as its own upsert/delete SQL, in the same transaction, before
  the out-of-scope delete runs. An application_eid gets exactly one
  `first_seen_in_scope_at`, ever -- durable across any number of scope
  exits/re-entries.
- `candidate_repository.get_ready_candidates()` gates automatic selection:
  when `config.go_live_at()` is set, a candidate is only auto-selected if
  `scope_history.first_seen_in_scope_at >= go_live_at`. A candidate with no
  `scope_history` row at all is conservatively excluded (never auto-posted
  on unproven "new" status). **`benivo_status` is completely untouched by
  this** -- a backlog candidate stays visibly `READY_TO_POST` everywhere
  (DB, reports, UAT override), just excluded from automatic selection.
  While `BENIVO_GO_LIVE_AT` is unset, this adds no filtering at all
  (today's exact behavior).

**Baseline procedure required before enabling production go-live** (see
`scripts/backfill_scope_history.py`), in order:

1. Apply `migrations/0008_add_scope_history_table.sql`.
2. Run `python scripts/backfill_scope_history.py --check`, review the
   counts, then `--apply`. This inserts one row per application_eid known
   from `benivo.candidates` (current backlog) **UNION** `benivo.post_log`
   (every application_eid ever attempted, including ones whose
   `candidates` row has since been deleted after leaving scope), with
   `first_seen_in_scope_at = NOW()` -- guaranteed before go-live since the
   cutover isn't enabled yet. Safe to re-run (`ON CONFLICT DO NOTHING`).
   **Known limitation:** a candidate who fully entered and left scope
   before ever being posted and before this backfill ran has no trace in
   either source table and cannot be backfilled; run this as close to the
   actual cutover as practical to minimize that window.
3. Only then deploy the code that reads/writes `scope_history`
   (`synchronization_service.py`, `candidate_repository.py`,
   `reporting_service.py`) -- deploying it before step 1 makes
   `sync_candidates()` fail on every run (INSERT into a table that doesn't
   exist yet).
4. Only then set `BENIVO_GO_LIVE_AT` (ISO 8601, e.g.
   `2026-09-01T00:00:00Z`) to actually activate the gate.

**Reporting:** the "Go-Live Status" sheet and the Executive Summary's
"Go-Live Readiness" section (see `app/services/reporting_service.py`)
classify every in-scope candidate into one of four mutually exclusive,
report-only categories (never written back to `benivo_status`):
`Pre-Go-Live Backlog`, `Newly Eligible`, `Automatically Eligible` (exactly
who `get_ready_candidates()` will pick up next run), `Already Posted`.

---

## Phase 2

Phase 2 adds:

- Serbia Excel integration
- Candidate enrichment
- Additional validations before posting