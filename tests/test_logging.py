import io
import json
import logging
from datetime import UTC, datetime, timedelta, timezone
from decimal import Decimal

import pytest
import structlog

from basis_bot.core.run_id import RUN_ID_RE, new_run_id
from basis_bot.log import bind_run_id, configure_logging


def test_run_id_format_uses_utc() -> None:
    local = datetime(
        2026, 10, 9, 16, 30, 5, tzinfo=timezone(timedelta(hours=2))
    )
    run_id = new_run_id(local, entropy="a1f3")
    assert run_id == "261009143005a1f3"
    assert RUN_ID_RE.match(run_id)


def test_run_id_is_random_by_default() -> None:
    now = datetime(2026, 10, 9, tzinfo=UTC)
    ids = {new_run_id(now) for _ in range(50)}
    assert len(ids) > 1
    assert all(RUN_ID_RE.match(i) for i in ids)


def test_run_id_rejects_naive_time() -> None:
    with pytest.raises(ValueError, match="timezone-aware"):
        new_run_id(datetime(2026, 10, 9))  # noqa: DTZ001


def test_log_lines_are_json_with_run_id_and_utc() -> None:
    stream = io.StringIO()
    configure_logging("INFO", stream)
    structlog.contextvars.clear_contextvars()
    bind_run_id("261009143005a1f3")

    structlog.get_logger("t").info("hello", amount=Decimal("0.1"))
    logging.getLogger("sqlalchemy.test").warning("from stdlib")

    lines = [json.loads(line) for line in stream.getvalue().splitlines()]
    assert [line["event"] for line in lines] == ["hello", "from stdlib"]
    for line in lines:
        assert line["run_id"] == "261009143005a1f3"
        assert line["ts"].endswith("Z")
    assert lines[0]["amount"] == "0.1"
    structlog.contextvars.clear_contextvars()
