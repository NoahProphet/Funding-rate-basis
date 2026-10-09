import sqlalchemy as sa
from alembic.config import Config as AlembicConfig
from alembic.script import ScriptDirectory

from basis_bot.storage.schema import SCHEMA_REVISION, metadata
from tests.conftest import REPO_ROOT

SPEC_TABLES = {
    "instruments",
    "funding_rates",
    "price_snapshots",
    "orders",
    "fills",
    "position_snapshots",
    "balance_snapshots",
    "funding_payments",
    "pnl_daily",
    "events",
    "risk_decisions",
    "config_versions",
}


def test_schema_revision_is_alembic_head() -> None:
    cfg = AlembicConfig(str(REPO_ROOT / "alembic.ini"))
    heads = ScriptDirectory.from_config(cfg).get_heads()
    assert heads == [SCHEMA_REVISION]


def test_all_spec_tables_exist() -> None:
    assert set(metadata.tables) == SPEC_TABLES


def test_money_columns_are_exact_numeric() -> None:
    for table in metadata.tables.values():
        for column in table.columns:
            type_name = type(column.type).__name__
            assert type_name not in {"Float", "REAL", "DOUBLE_PRECISION"}, (
                f"{table.name}.{column.name} is a float column"
            )


def test_timestamps_are_timezone_aware() -> None:
    for table in metadata.tables.values():
        for column in table.columns:
            if isinstance(column.type, sa.DateTime):
                assert column.type.timezone, f"{table.name}.{column.name}"
