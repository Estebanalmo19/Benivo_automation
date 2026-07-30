"""Benivo Automation entry point.

sync_candidates -> classify_candidates -> select_postable_candidates ->
post_candidates -> generate_reports -> write_post_log ->
update_candidate_status.

The report is generated AFTER selection/posting (not before) so it can
include selected_for_current_run and this run's posting-outcome counts.

The last two steps only run when posting.is_dry_run() is False. During this
phase BENIVO_DRY_RUN defaults to true, so no real Benivo call or post_log/
candidate write happens unless that is explicitly overridden.
"""

import logging
import sys
import uuid

import posting
from classify_candidates import classify_candidates
from reporting import generate_reports
from sync_candidates import sync_candidates

logger = logging.getLogger(__name__)

if not logging.getLogger().handlers:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )


def main() -> None:
    run_id = str(uuid.uuid4())
    logger.info("Run started: run_id=%s", run_id)

    try:
        sync_metrics = sync_candidates()
    except Exception:
        logger.error("Aborting: candidate synchronization failed, downstream steps will not run.")
        sys.exit(1)

    logger.info("Sync metrics: %s", sync_metrics)

    try:
        classify_metrics = classify_candidates()
    except Exception:
        logger.error("Aborting: candidate classification failed, downstream steps will not run.")
        sys.exit(1)

    logger.info("Classification metrics: %s", classify_metrics)

    dry_run = posting.is_dry_run()
    posting_limit = posting._get_max_candidates()
    candidates = posting.select_postable_candidates(limit=posting_limit)

    logger.info(
        "Selected %d candidate(s) for posting (mode=%s, limit=%d).",
        len(candidates),
        "DRY RUN" if dry_run else "SYNC",
        posting_limit,
    )
    logger.info(
        "Selected application_eids: %s",
        [candidate.get("application_eid") for candidate in candidates],
    )

    results = posting.post_candidates(candidates, dry_run=dry_run)

    if dry_run:
        logger.info("DRY RUN: no Benivo API call made, no post_log/candidate writes performed.")
        for preview in results:
            logger.info("Preview: %s", preview)
    else:
        for candidate, result in zip(candidates, results):
            posting.record_post_result(candidate, result, run_id=run_id)
        logger.info("Posting cycle finished: %d attempt(s) recorded.", len(results))

    report_path = generate_reports(
        selected_candidates=candidates,
        posting_results=results,
        dry_run=dry_run,
        posting_limit=posting_limit,
    )
    logger.info("Report generated: %s", report_path)


if __name__ == "__main__":
    main()
