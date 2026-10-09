"""The single source of wall-clock time; always UTC-aware."""

from collections.abc import Callable
from datetime import UTC, datetime

Clock = Callable[[], datetime]


def utc_now() -> datetime:
    return datetime.now(UTC)
