"""Database connection, schema revision check and audit writes."""

import json
from datetime import datetime
from enum import StrEnum
from typing import Any

import sqlalchemy as sa
from alembic.runtime.migration import MigrationContext
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.engine import URL, Connection
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine
from sqlalchemy.ext.asyncio import create_async_engine as _create_engine

from basis_bot.config import Config, DatabaseConfig, canonical_json
from basis_bot.storage.schema import SCHEMA_REVISION, config_versions, events


class Severity(StrEnum):
    INFO = "INFO"
    WARN = "WARN"
    CRIT = "CRIT"


class SchemaMismatchError(Exception):
    pass


def read_password(cfg: DatabaseConfig) -> str:
    return cfg.password_file.read_text(encoding="utf-8").strip()


def database_url(cfg: DatabaseConfig, password: str) -> URL:
    # URL.create escapes the password and masks it in repr and logs.
    return URL.create(
        "postgresql+psycopg",
        username=cfg.user,
        password=password,
        host=cfg.host,
        port=cfg.port,
        database=cfg.name,
    )


def create_engine(cfg: DatabaseConfig) -> AsyncEngine:
    url = database_url(cfg, read_password(cfg))
    return _create_engine(
        url,
        pool_pre_ping=True,
        connect_args={"options": "-c timezone=UTC"},
    )


def _current_revision(conn: Connection) -> str | None:
    return MigrationContext.configure(conn).get_current_revision()


async def check_schema_revision(conn: AsyncConnection) -> None:
    current = await conn.run_sync(_current_revision)
    if current != SCHEMA_REVISION:
        raise SchemaMismatchError(
            f"database schema is at {current!r}, code expects "
            f"{SCHEMA_REVISION!r}; run migrations first"
        )


async def write_event(
    conn: AsyncConnection,
    *,
    ts: datetime,
    severity: Severity,
    type_: str,
    source: str,
    payload: dict[str, Any],
    key: str | None = None,
) -> None:
    # Round-trip through JSON with default=str so Decimal and datetime
    # payload values are stored as exact strings.
    safe_payload = json.loads(json.dumps(payload, default=str))
    await conn.execute(
        events.insert().values(
            ts=ts,
            severity=severity.value,
            type=type_,
            key=key,
            payload=safe_payload,
            source=source,
        )
    )


async def record_config_version(
    conn: AsyncConnection, config: Config, ts: datetime
) -> bool:
    """Store the config if its hash is new. Returns True if it was new."""
    stmt = (
        insert(config_versions)
        .values(
            ts=ts,
            hash=config.config_hash(),
            config=json.loads(canonical_json(config)),
        )
        .on_conflict_do_nothing(index_elements=["hash"])
        .returning(config_versions.c.id)
    )
    result = await conn.execute(stmt)
    return result.first() is not None


async def ping(conn: AsyncConnection) -> None:
    await conn.execute(sa.text("SELECT 1"))
