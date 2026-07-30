# Operations

Day-2 reference for running and troubleshooting Benivo Automation. See
[`../README.md`](../README.md) for what the system does and initial setup.

## Normal scheduled operation

The VM runs one command on a schedule:

```bash
cd /opt/benivo-automation
source venv/bin/activate
python -m app.main run
```

`run` executes sync -> classify -> select -> post (only if
`BENIVO_DRY_RUN=false`) -> report, and exits non-zero only on a fatal
failure (bad config, DB unreachable, Benivo auth failure). An individual
candidate failing to post does **not** fail the batch -- it's recorded as
`FAILED` in `post_log` and the run continues.

### cron

```cron
0 */3 * * * cd /opt/benivo-automation && venv/bin/python -m app.main run >> /var/log/benivo-automation/run.log 2>&1
```

### systemd timer (preferred -- gives you `journalctl`, retries, and status)

`/etc/systemd/system/benivo-automation.service`:
```ini
[Unit]
Description=Benivo Automation run

[Service]
Type=oneshot
WorkingDirectory=/opt/benivo-automation
EnvironmentFile=/opt/benivo-automation/.env
ExecStart=/opt/benivo-automation/venv/bin/python -m app.main run
```

`/etc/systemd/system/benivo-automation.timer`:
```ini
[Unit]
Description=Run Benivo Automation every 3 hours

[Timer]
OnCalendar=*-*-* 0/3:00:00
Persistent=true

[Install]
WantedBy=timers.target
```

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now benivo-automation.timer
```

## How to inspect logs

- systemd: `journalctl -u benivo-automation.service -n 200 --no-pager`
- cron: `tail -n 200 /var/log/benivo-automation/run.log`

Every log line includes the stage (`sync_candidates`, `classify_candidates`,
etc.), the `run_id` for that invocation, and counts (candidates
synchronized/classified/selected, posting result counts). Fatal errors log
a full traceback (`logger.exception`). No passwords, tokens, or full
candidate payloads are ever logged -- only sanitized summaries
(masked email, counts, application_eid).

## How to verify the latest run

```bash
ls -t reports/*.xlsx | head -1   # newest report
```

Open it and check the `Summary` sheet: `selected_for_current_run`,
`posting_limit`, `dry_run`, and the three `posting_*_current_run` counts
tell you exactly what happened in that run.

## How to inspect post_log

```sql
-- Full history for one candidate
SELECT id, run_id, status, benivo_user_id, policy_api_value, posted_at, error_message
FROM benivo.post_log
WHERE application_eid = '<application_eid>'
ORDER BY posted_at;

-- Everything that failed and is still retryable (no later SUCCESS/ALREADY_EXISTS)
SELECT pl.*
FROM benivo.post_log pl
WHERE pl.status = 'FAILED'
  AND NOT EXISTS (
      SELECT 1 FROM benivo.post_log pl2
      WHERE pl2.application_eid = pl.application_eid
        AND pl2.status IN ('SUCCESS', 'ALREADY_EXISTS')
  );
```

`post_log` is append-only -- never delete or edit a row. A `FAILED` row is
resolved by a *new* row (a later successful attempt), never by editing the
old one.

## How to retry a failed candidate

Nothing manual is usually needed: `POST_FAILED` is not terminal, so
`classification_service` re-evaluates the candidate on the next `run` and
it becomes `READY_TO_POST` again automatically (if the underlying issue --
e.g. a missing office mapping -- has been fixed) and gets picked up by the
normal selection.

For a single, explicitly controlled retry (recommended after fixing
something like a policy/office mapping bug), use the UAT override so
exactly one candidate is touched:

```bash
python scripts/validate_uat_candidate.py <application_eid>   # read-only pre-flight check first

BENIVO_DRY_RUN=false BENIVO_MAX_CANDIDATES=1 BENIVO_ALLOW_REFERENCE_DATA_CALLS=true \
  BENIVO_UAT_APPLICATION_EID=<application_eid> \
  python -m app.main post
```

If the candidate isn't eligible, nothing is posted -- the log states
exactly which check failed (e.g. `office_resolved: False`).

## How to stop processing

- **Immediately**: `sudo systemctl stop benivo-automation.timer` (or remove
  the cron line). If a run is mid-flight, let it finish -- it's not
  designed to be killed mid-transaction; each DB write is a short,
  self-contained transaction (sync, classify-per-candidate,
  post_log+status-per-candidate), so killing the process leaves the
  database in a consistent state either way, just possibly partway through
  the batch.
- **Just pause real posting, keep everything else running**: set
  `BENIVO_DRY_RUN=true` in `.env` and restart the timer/service. Sync,
  classify, and reporting keep running; nothing gets posted.

## How to perform a dry run

```bash
BENIVO_DRY_RUN=true BENIVO_MAX_CANDIDATES=5 BENIVO_ALLOW_REFERENCE_DATA_CALLS=true \
  python -m app.main run
```

Safe to run anytime -- makes no create-user or user-lookup call, writes no
`post_log` rows, changes no `benivo_status` to `POSTED`/`POST_FAILED`. Sync
and classify still write to `benivo.candidates` (that's their job; it's not
"posting").

## How to roll back to the previous Git commit

```bash
git log --oneline -5              # find the commit to roll back to
git status                        # make sure there's nothing uncommitted you'd lose
git checkout <previous-commit-sha> -- .
# or, to move the branch pointer itself (only if nothing has been pushed past it):
git reset --hard <previous-commit-sha>
```

Prefer `git checkout <sha> -- .` over `git reset --hard` unless you're
certain nothing important sits on top of the commit you're leaving --
`reset --hard` discards work with no confirmation.

Database schema changes are **not** rolled back by a Git revert -- migrations
are forward-only (see README). If a bad deploy included a migration,
rolling back the code does not undo the schema change; fix forward instead.
