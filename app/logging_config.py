"""One place to configure logging. Call configure_logging() once, at process startup, before anything else logs."""

import logging

from app import config

_LOG_FORMAT = "%(asctime)s %(levelname)s %(name)s: %(message)s"

_configured = False


def configure_logging(level: str = None) -> None:
    """Idempotent: safe to call multiple times (e.g. once per script and again in a test)."""
    global _configured

    if _configured:
        return

    logging.basicConfig(level=(level or config.log_level()), format=_LOG_FORMAT)
    _configured = True
