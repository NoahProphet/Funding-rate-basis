import asyncio
import time
from typing import Any, cast

import pytest
from sqlalchemy.exc import OperationalError
from sqlalchemy.ext.asyncio import AsyncEngine

from basis_bot.core import app


class _UnreachableEngine:
    def __init__(self) -> None:
        self.attempts = 0

    def connect(self) -> Any:
        self.attempts += 1
        raise OperationalError("SELECT 1", {}, Exception("refused"))


def test_connect_retry_gives_up_promptly_on_stop() -> None:
    engine = _UnreachableEngine()
    stop = asyncio.Event()
    stop.set()

    started = time.monotonic()
    connected = asyncio.run(
        app._connect_with_retry(cast(AsyncEngine, engine), stop)
    )

    assert connected is False
    assert engine.attempts == 1
    assert time.monotonic() - started < 0.5


def test_connect_retry_raises_after_last_attempt(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(app, "_CONNECT_BACKOFF_S", 0.001)
    engine = _UnreachableEngine()

    with pytest.raises(OperationalError):
        asyncio.run(
            app._connect_with_retry(cast(AsyncEngine, engine), asyncio.Event())
        )
    assert engine.attempts == app._CONNECT_ATTEMPTS
