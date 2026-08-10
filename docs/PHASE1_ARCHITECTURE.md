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
- The Case PATCH is called immediately after a **successful** create-user
  (see `posting_service.post_single_candidate()`), never for
  `already_exists` or `failed` outcomes.
- The Case PATCH is best-effort and independently audited: a failure is
  recorded as its own `benivo.post_log` row (`action = 'UPDATE_CASE'`,
  see `migrations/0006`) and never changes the candidate's `POSTED` status
  set by a successful create-user.

---

## Phase 2

Phase 2 adds:

- Serbia Excel integration
- Candidate enrichment
- Additional validations before posting