"""Common shared helper functions."""

from __future__ import annotations

from datetime import datetime


def current_time_str(fmt: str = "%Y-%m-%d %H:%M:%S") -> str:
    """Return current local time as formatted string."""
    return datetime.now().strftime(fmt)


def is_transient_timeout_error(exc: Exception) -> bool:
    """Whether an exception is likely a transient timeout/network delay."""
    text = str(exc).lower()
    return (
        "timeout" in text
        or "deadline exceeded" in text
        or "awaiting headers" in text
    )
