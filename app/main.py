"""Benivo Automation entry point.

Usage:
    python -m app.main sync       -- synchronize benivo.candidates only
    python -m app.main classify   -- re-classify benivo.candidates only
    python -m app.main report     -- generate the operational report only (never posts, regardless of BENIVO_DRY_RUN)
    python -m app.main post       -- select + post eligible candidates (respects BENIVO_DRY_RUN), then report
    python -m app.main run        -- the full scheduled sequence: sync -> classify -> post -> report

"run" is what a cron/systemd timer on the VM should call. The last two
steps of "post"/"run" (post_log write + candidate status update) only
execute when BENIVO_DRY_RUN=false. See .env.example for every supported
setting, including the BENIVO_UAT_APPLICATION_EID safety override for a
single explicitly-confirmed candidate.
"""

import argparse
import logging
import sys
import uuid
from typing import List, Optional

from app import config
from app.logging_config import configure_logging
from app.services import classification_service, posting_service, reporting_service, synchronization_service

logger = logging.getLogger(__name__)


def _run_sync() -> None:
    metrics = synchronization_service.sync_candidates()
    logger.info("Sync metrics: %s", metrics)


def _run_classify() -> None:
    metrics = classification_service.classify_candidates()
    logger.info("Classification metrics: %s", metrics)


def _select_and_post(run_id: str, force_dry_run: Optional[bool] = None):
    """Returns (candidates, results, dry_run, posting_limit). force_dry_run=True is used by cmd_report() to guarantee it never posts."""
    dry_run = posting_service.is_dry_run() if force_dry_run is None else force_dry_run
    posting_limit = posting_service._get_max_candidates()

    candidates = posting_service.select_postable_candidates(limit=posting_limit)

    logger.info(
        "Selected %d candidate(s) for posting (mode=%s, limit=%d).",
        len(candidates),
        "DRY RUN" if dry_run else "SYNC",
        posting_limit,
    )
    logger.info("Selected application_eids: %s", [c.get("application_eid") for c in candidates])

    results = posting_service.post_candidates(candidates, dry_run=dry_run)

    if dry_run:
        logger.info("DRY RUN: no Benivo API call made, no post_log/candidate writes performed.")
        for preview in results:
            logger.info("Preview: %s", preview)
    else:
        success = already_exists = failed = 0

        for candidate, result in zip(candidates, results):
            posting_service.record_post_result(candidate, result, run_id=run_id)
            outcome = result.get("outcome")
            success += outcome == "success"
            already_exists += outcome == "already_exists"
            failed += outcome == "failed"

        logger.info(
            "Posting cycle finished: %d attempt(s) recorded (success=%d, already_exists=%d, failed=%d).",
            len(results),
            success,
            already_exists,
            failed,
        )

    return candidates, results, dry_run, posting_limit


def cmd_sync(_args: argparse.Namespace) -> int:
    _run_sync()
    return 0


def cmd_classify(_args: argparse.Namespace) -> int:
    _run_classify()
    return 0


def cmd_report(_args: argparse.Namespace) -> int:
    """Never posts for real, regardless of BENIVO_DRY_RUN -- this command only ever produces a report."""
    run_id = str(uuid.uuid4())
    candidates, results, dry_run, posting_limit = _select_and_post(run_id, force_dry_run=True)
    report_path = reporting_service.generate_reports(candidates, results, dry_run, posting_limit)
    logger.info("Report generated: %s", report_path)
    return 0


def cmd_post(args: argparse.Namespace) -> int:
    run_id = str(uuid.uuid4())
    candidates, results, dry_run, posting_limit = _select_and_post(run_id)
    report_path = reporting_service.generate_reports(candidates, results, dry_run, posting_limit)
    logger.info("Report generated: %s", report_path)
    return 0


def cmd_run(args: argparse.Namespace) -> int:
    run_id = str(uuid.uuid4())
    logger.info("Run started: run_id=%s", run_id)

    try:
        _run_sync()
    except Exception:
        logger.error("Aborting: candidate synchronization failed, downstream steps will not run.")
        return 1

    try:
        _run_classify()
    except Exception:
        logger.error("Aborting: candidate classification failed, downstream steps will not run.")
        return 1

    candidates, results, dry_run, posting_limit = _select_and_post(run_id)
    report_path = reporting_service.generate_reports(candidates, results, dry_run, posting_limit)
    logger.info("Report generated: %s", report_path)
    return 0


COMMANDS = {
    "sync": cmd_sync,
    "classify": cmd_classify,
    "report": cmd_report,
    "post": cmd_post,
    "run": cmd_run,
}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m app.main", description="Benivo Automation")
    parser.add_argument("command", choices=sorted(COMMANDS), help="Which step to run")
    return parser


def main(argv: Optional[List[str]] = None) -> int:
    configure_logging()

    try:
        config.validate()
    except RuntimeError as exc:
        logger.error("Configuration error: %s", exc)
        return 1

    args = build_parser().parse_args(argv)

    try:
        return COMMANDS[args.command](args)
    except Exception:
        logger.exception("Fatal error running command %r.", args.command)
        return 1


if __name__ == "__main__":
    sys.exit(main())
