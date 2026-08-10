"""Power Automate delivery for already-generated Benivo operational reports.

Delivery is NOT part of the Benivo business transaction. A delivery failure
must NEVER roll back or retry create-user/Case PATCH, modify benivo_status,
create a POST_FAILED result, modify posting results, or remove the
generated Excel file -- see deliver_report(), which never raises and always
returns a structured result instead. Report generation
(reporting_service.generate_reports()) and report delivery are separate
concerns: this module owns HTTP delivery only, and never recalculates
business metrics -- it only forwards the metrics generate_reports() already
produced.

No row is ever written to benivo.post_log for a delivery attempt --
post_log is exclusively the Benivo business-operation audit trail
(CREATE_USER, UPDATE_CASE). Delivery is application/integration telemetry.

SECURITY: the webhook URL (which carries a signed query string), the
outgoing JSON payload, and the Base64-encoded file content are NEVER
logged or returned by this module, at any log level. Only the filename,
HTTP status code, and a small set of sanitized, static error strings are
ever logged -- see the log statements below, which are the complete set
this module produces.
"""

import base64
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional

import requests

from app import config

logger = logging.getLogger(__name__)

CONTENT_TYPE_XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
REPORT_TYPE = "Benivo Operational Report"
REQUEST_TIMEOUT_SECONDS = 30


def _result(
    attempted: bool,
    success: Optional[bool],
    status_code: Optional[int],
    file_name: str,
    error: Optional[str],
) -> Dict[str, Any]:
    return {
        "attempted": attempted,
        "success": success,
        "status_code": status_code,
        "file_name": file_name,
        "error": error,
    }


def _build_payload(report_path: Path, run_id: str, metrics: Dict[str, Any]) -> Dict[str, Any]:
    """
    Pure payload construction, no I/O beyond reading the already-generated
    file. metrics is the dict reporting_service.generate_reports() already
    computed -- never recalculated here. Any metric absent from `metrics`
    is sent as null rather than an invented value.
    """
    file_bytes = report_path.read_bytes()

    return {
        "fileName": report_path.name,
        "contentType": CONTENT_TYPE_XLSX,
        "fileContentBase64": base64.b64encode(file_bytes).decode("ascii"),
        "fileSizeBytes": len(file_bytes),
        "runId": run_id,
        "generatedAt": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "reportType": REPORT_TYPE,
        "summary": {
            "readyToPost": metrics.get("ready_to_post"),
            "posted": metrics.get("posted"),
            "alreadyExists": metrics.get("already_exists"),
            "failed": metrics.get("failed"),
            "pendingRecruiterReview": metrics.get("pending_recruiter_review"),
            "pendingOfficeMapping": metrics.get("pending_office_mapping"),
            "countryFallback": metrics.get("country_fallback"),
        },
    }


def deliver_report(report_path: Path, run_id: str, metrics: Dict[str, Any]) -> Dict[str, Any]:
    """
    Best-effort, at-most-once delivery of one already-generated report to
    Power Automate. Callers must call this exactly once per generated
    report (see app/main.py's single orchestration boundary) -- this
    function does not deduplicate calls itself.

    Never raises: every failure mode (disabled, misconfigured, unreadable
    file, network error, timeout, non-2xx response) is caught and returned
    as a structured result so a delivery problem can never interrupt or
    roll back the caller's business execution.
    """
    file_name = report_path.name

    if not config.report_delivery_enabled():
        logger.info("Report delivery disabled.")
        return _result(attempted=False, success=None, status_code=None, file_name=file_name, error=None)

    webhook_url = config.report_webhook_url()

    if not webhook_url:
        logger.error("Report delivery failed: webhook URL not configured, filename=%s", file_name)
        return _result(
            attempted=True, success=False, status_code=None, file_name=file_name,
            error="BENIVO_REPORT_WEBHOOK_URL is not configured",
        )

    logger.info("Report delivery started: %s", file_name)

    try:
        payload = _build_payload(report_path, run_id, metrics)
        response = requests.post(
            webhook_url,
            json=payload,
            headers={"Content-Type": "application/json"},
            timeout=REQUEST_TIMEOUT_SECONDS,
        )
    except requests.exceptions.Timeout:
        logger.error("Report delivery failed: timeout, filename=%s", file_name)
        return _result(attempted=True, success=False, status_code=None, file_name=file_name, error="Request timed out")
    except requests.exceptions.RequestException:
        # Deliberately not logging str(exc): requests' own exception messages
        # frequently embed the request URL, which carries the signed query string.
        logger.error("Report delivery failed: network error, filename=%s", file_name)
        return _result(attempted=True, success=False, status_code=None, file_name=file_name, error="Network error")
    except OSError:
        logger.error("Report delivery failed: could not read report file, filename=%s", file_name)
        return _result(attempted=True, success=False, status_code=None, file_name=file_name, error="Could not read generated report file")
    except Exception:
        # Final safety net -- this function must never raise into the caller's
        # business execution, no matter what goes wrong.
        logger.error("Report delivery failed: unexpected error, filename=%s", file_name)
        return _result(attempted=True, success=False, status_code=None, file_name=file_name, error="Unexpected delivery error")

    success = 200 <= response.status_code < 300

    if success:
        logger.info("Report delivery succeeded: %s, HTTP %s", file_name, response.status_code)
        return _result(attempted=True, success=True, status_code=response.status_code, file_name=file_name, error=None)

    logger.error("Report delivery failed: %s, HTTP %s", file_name, response.status_code)
    return _result(
        attempted=True, success=False, status_code=response.status_code, file_name=file_name,
        error=f"HTTP {response.status_code}",
    )
