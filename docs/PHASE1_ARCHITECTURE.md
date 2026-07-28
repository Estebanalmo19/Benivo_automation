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

## Phase 2

Phase 2 adds:

- Serbia Excel integration
- Candidate enrichment
- Additional validations before posting