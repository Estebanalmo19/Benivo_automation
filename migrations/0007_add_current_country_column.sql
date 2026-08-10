-- Add benivo.candidates.current_country: the candidate's structured
-- current-location country from Jobvite (raw_payload.countryName -- a
-- top-level candidate-profile field, sibling of firstName/lastName/email,
-- distinct from the 'location' display string "city, state country" and
-- distinct from application.job.location, which is the JOB's location, not
-- the candidate's).
--
-- Investigated 2026-08-10 while tracing why pdr4myw7/p6Ejaywg appeared in
-- the Missing Home Country sheet despite having country data in Jobvite:
-- neither has an application.customField[fieldCode=candidate_home_country]
-- entry (the confirmed primary source for benivo.candidates.home_country),
-- but both -- and 100% of the 99 candidates missing home_country as of
-- this investigation -- have a populated raw_payload.countryName. This
-- column is the fallback SOURCE; app/services/home_country_service.py
-- resolves the effective value (candidate_home_country, else
-- current_country, else NULL) -- home_country itself is never overwritten.
--
-- Non-destructive: ADD COLUMN IF NOT EXISTS is idempotent, nullable.

ALTER TABLE benivo.candidates
    ADD COLUMN IF NOT EXISTS current_country TEXT;
