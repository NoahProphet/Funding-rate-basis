import os
from pathlib import Path

import sqlalchemy as sa
from alembic import context

from basis_bot.config import load_config
from basis_bot.log import configure_logging
from basis_bot.storage.db import database_url, read_password
from basis_bot.storage.schema import metadata


def _url() -> sa.URL:
    cfg = load_config(Path(os.environ["BASIS_BOT_CONFIG"]))
    configure_logging(cfg.logging.level)
    return database_url(cfg.database, read_password(cfg.database))


def run_migrations_online() -> None:
    engine = sa.create_engine(_url(), poolclass=sa.NullPool)
    with engine.connect() as conn:
        context.configure(
            connection=conn,
            target_metadata=metadata,
            compare_type=True,
            transaction_per_migration=True,
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    raise SystemExit("offline migrations are not supported")
run_migrations_online()
