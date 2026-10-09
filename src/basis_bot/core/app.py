"""Core process lifecycle. Phase 0: connect, audit start, idle, audit stop."""

import asyncio
import contextlib
import signal
from collections.abc import Callable
from typing import Any

import structlog
from sqlalchemy.exc import OperationalError
from sqlalchemy.ext.asyncio import AsyncEngine

from basis_bot.clock import Clock
from basis_bot.config import Config
from basis_bot.storage import db

log = structlog.get_logger(__name__)

SOURCE = "core"
_CONNECT_ATTEMPTS = 6
_CONNECT_BACKOFF_S = 1.0


async def _sleep_unless_stopped(stop: asyncio.Event, seconds: float) -> None:
    with contextlib.suppress(TimeoutError):
        await asyncio.wait_for(stop.wait(), seconds)


async def _connect_with_retry(engine: AsyncEngine, stop: asyncio.Event) -> bool:
    """Returns False if a stop was requested before a connection succeeded."""
    delay = _CONNECT_BACKOFF_S
    for attempt in range(1, _CONNECT_ATTEMPTS + 1):
        try:
            async with engine.connect() as conn:
                await db.ping(conn)
            return True
        except OperationalError as exc:
            if attempt == _CONNECT_ATTEMPTS:
                raise
            log.warning(
                "db_connect_retry",
                attempt=attempt,
                delay_s=delay,
                error=str(exc.orig),
            )
        await _sleep_unless_stopped(stop, delay)
        if stop.is_set():
            return False
        delay *= 2
    return False


def install_stop_handlers(stop: asyncio.Event) -> None:
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, stop.set)
        except NotImplementedError:
            # Windows dev machines; production runs on Linux.
            signal.signal(sig, _threadsafe_setter(loop, stop.set))


def _threadsafe_setter(
    loop: asyncio.AbstractEventLoop, set_: Callable[[], None]
) -> Callable[..., None]:
    def handler(*_: Any) -> None:
        loop.call_soon_threadsafe(set_)

    return handler


async def run(
    config: Config,
    engine: AsyncEngine,
    run_id: str,
    clock: Clock,
    stop: asyncio.Event,
) -> None:
    config_hash = config.config_hash()
    if not await _connect_with_retry(engine, stop):
        log.info("core_stopped_before_start")
        return
    async with engine.begin() as conn:
        await db.check_schema_revision(conn)
        is_new = await db.record_config_version(conn, config, clock())
        if is_new:
            await db.write_event(
                conn,
                ts=clock(),
                severity=db.Severity.INFO,
                type_="config_version_changed",
                source=SOURCE,
                payload={"config_hash": config_hash},
            )
        await db.write_event(
            conn,
            ts=clock(),
            severity=db.Severity.INFO,
            type_="bot_start",
            source=SOURCE,
            payload={"run_id": run_id, "config_hash": config_hash},
        )
    log.info("core_started", config_hash=config_hash, venue=config.venue.name)

    await stop.wait()

    log.info("core_stopping")
    async with engine.begin() as conn:
        await db.write_event(
            conn,
            ts=clock(),
            severity=db.Severity.INFO,
            type_="bot_stop",
            source=SOURCE,
            payload={"run_id": run_id},
        )
    log.info("core_stopped")
