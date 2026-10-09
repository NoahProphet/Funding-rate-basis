"""Run identifiers: one per process start, prefix of every client order id."""

import re
import secrets
from datetime import UTC, datetime

# 16 chars keeps `<run_id>-<symbol>-<seq>` short; venue client id length
# limits are not yet verified (decided with D1).
RUN_ID_RE = re.compile(r"^\d{12}[0-9a-f]{4}$")


def new_run_id(now: datetime, entropy: str | None = None) -> str:
    """`yymmddHHMMSS` in UTC plus 4 hex chars, e.g. `261009143005a1f3`."""
    if now.utcoffset() is None:
        raise ValueError("run id time must be timezone-aware")
    if entropy is None:
        entropy = secrets.token_hex(2)
    return now.astimezone(UTC).strftime("%y%m%d%H%M%S") + entropy
