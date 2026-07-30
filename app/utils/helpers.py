"""Small, generic, pure helpers shared across the app. No I/O, no config coupling."""

import os
from typing import Optional


def env_flag(name: str, default: bool) -> bool:
    """True/false env var: 'false'/'0'/'no' (case-insensitive) -> False, anything else -> True; unset -> default."""
    raw = os.getenv(name)

    if raw is None or raw.strip() == "":
        return default

    if default:
        return raw.strip().lower() not in {"false", "0", "no"}

    return raw.strip().lower() in {"true", "1", "yes"}


def env_positive_int(name: str, default: int) -> int:
    """Positive integer env var; falls back to default if unset, non-numeric, or <= 0."""
    raw = os.getenv(name)

    if raw is None or raw.strip() == "":
        return default

    try:
        value = int(raw)
    except ValueError:
        return default

    return value if value > 0 else default


def mask_email(email: Optional[str]) -> Optional[str]:
    """First character + domain only, e.g. 'jane@example.com' -> 'j***@example.com'."""
    if not email or "@" not in email:
        return email

    local, domain = email.split("@", 1)
    masked_local = (local[0] + "***") if local else "***"
    return f"{masked_local}@{domain}"
